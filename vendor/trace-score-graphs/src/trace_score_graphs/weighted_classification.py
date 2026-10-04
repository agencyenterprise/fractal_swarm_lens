"""Class-dependent simplex weights applied to adjacencies BEFORE Laplacians.

Select weights by nested, task-group validation. Outer folds are copied unchanged
from the existing experiment. All graph features use saved scores; no API calls.
"""
import argparse
import concurrent.futures
import csv
import html
import json
import platform
import time
import warnings
from pathlib import Path

import numpy as np
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import GroupKFold

from . import classification
from .classification import LABELS, LABEL_NAMES, LAYERS, SPECTRAL, metrics, preprocess_fold, summarize
from .common import digest, file_sha, jsonl, read, save

VERSION = 'class-dependent-graph-mixtures-v1'
BASELINES = ('average_graph', 'all_laplacians', 'scores_only', 'activity_only')


def candidates(seed):
    """Frozen label-independent search bank: uniform, directed moves, Dirichlet."""
    n = len(LAYERS)
    uniform = np.full(n, 1 / n)
    result = [uniform]
    for strength in (.25, .5, .75, 1.):
        for j in range(n):
            result.append((1 - strength) * uniform + strength * np.eye(n)[j])
    rng = np.random.default_rng(seed)
    for i in range(28):
        result.append(rng.dirichlet(np.full(n, (.3, 1., 3.)[i % 3])))
    return np.array(result)


def concentration(weights):
    weights = np.asarray(weights)
    n = weights.shape[-1]
    return np.sum((weights - 1 / n) ** 2, axis=-1) / (1 - 1 / n)


def mix_graphs(adjacencies, observed, weights):
    """Supports (..., layers, n, n), retaining a per-edge availability mask."""
    weights = np.asarray(weights, dtype=float)
    if weights.shape != (len(LAYERS),) or (weights < 0).any() or not np.isclose(weights.sum(), 1):
        raise ValueError('Require nine nonnegative weights summing to one')
    numerator = np.sum(adjacencies * observed * weights[:, None, None], axis=-3)
    denominator = np.sum(observed * weights[:, None, None], axis=-3)
    return np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0), denominator


def batched_spectrum(adjacencies):
    """Same descriptors as classification.spectrum_features, with batched eigensolvers."""
    a = np.asarray(adjacencies)
    count, n, _ = a.shape
    b = np.zeros((count, 2*n, 2*n))
    b[:, :n, n:], b[:, n:, :n] = a, np.transpose(a, (0, 2, 1))
    degree = np.abs(b).sum(axis=2)
    lap = -b
    ix = np.arange(2*n)
    lap[:, ix, ix] = degree
    inv = np.divide(1., np.sqrt(degree), out=np.zeros_like(degree), where=degree > 0)
    normalized = inv[:, :, None] * lap * inv[:, None, :]
    eig, raw = np.linalg.eigvalsh(normalized), np.linalg.eigvalsh(lap)
    if eig.min() < -1e-8 or eig.max() > 2 + 1e-8 or raw.min() < -1e-8:
        raise ValueError('Invalid Laplacian spectrum')
    eig, raw = np.maximum(eig, 0), np.maximum(raw, 0)
    return np.column_stack([np.mean(eig > 1e-8, axis=1), np.quantile(eig, [.25, .5, .75], axis=1).T,
                            eig.max(axis=1), np.exp(-.5*eig).mean(axis=1), np.exp(-2*eig).mean(axis=1),
                            raw.mean(axis=1), raw.max(axis=1)])


def feature_bank(base, weights, out):
    """Unsupervised features for each mixture; all-label-independent and cacheable."""
    metadata = read(base / 'metadata.json')['conversations']
    names = read(base / 'feature-names.json')['average_graph']
    original = np.load(base / 'feature-matrices.npz')['average_graph']
    groups, offsets, total = {}, [], 0
    for item in metadata:
        cid = item['conversation_id']
        wins = read(base / 'windows' / (cid + '.json'))
        offsets.append((total, total + len(wins)))
        with np.load(base / 'matrices' / (cid + '.npz')) as matrices:
            for w in wins:
                prefix = f'w{w["window"]:04d}_'
                a = np.stack([matrices[prefix + layer + '_adjacency'] for layer in LAYERS])
                mask = np.stack([matrices[prefix + layer + '_observed_counts'] > 0 for layer in LAYERS])
                groups.setdefault(len(w['actors']), []).append((total, a, mask))
                total += 1
    batched = {n: (np.array([v[0] for v in items]), np.stack([v[1] for v in items]), np.stack([v[2] for v in items]))
               for n, items in groups.items()}
    result = np.empty((len(weights), len(metadata), len(names)))
    index = {name: i for i, name in enumerate(names)}
    max_uniform_error = 0.
    for k, alpha in enumerate(weights):
        descriptors = np.empty((total, len(SPECTRAL)))
        for ids, adj, masks in batched.values():
            combined, _ = mix_graphs(adj, masks, alpha)
            descriptors[ids] = batched_spectrum(combined)
        result[k] = original.copy()  # Original, UNWEIGHTED coverage features stay identical.
        for i, (start, stop) in enumerate(offsets):
            f = summarize(descriptors[start:stop], SPECTRAL, 'average')
            for name, value in f.items():
                result[k, i, index[name]] = value if value is not None else np.nan
        if k == 0:
            np.testing.assert_allclose(result[0], original, atol=1e-8, rtol=1e-8, equal_nan=True)
            max_uniform_error = float(np.nanmax(np.abs(result[0] - original)))
            result[0] = original  # Exact equal-weight control matches the previous experiment.
        if (k + 1) % 10 == 0 or k + 1 == len(weights):
            print(json.dumps({'feature_mixtures': k + 1, 'total': len(weights)}), flush=True)
    np.savez_compressed(out / 'feature-bank.npz', features=result, weights=weights)
    save(out / 'feature-names.json', names)
    save(out / 'feature-audit.json', {'uniform_matches_original': True,
                                    'max_uniform_recomputation_error': max_uniform_error,
                                    'mixtures': len(weights), 'windows': total, 'features_per_conversation': len(names)})
    return result


def inner_splits(train, groups, seed):
    train = np.asarray(train, dtype=int)
    count = min(3, len({groups[i] for i in train}))
    if count < 2:
        return []
    splitter = GroupKFold(n_splits=count, shuffle=True, random_state=seed)
    result = []
    for a, b in splitter.split(train, groups=np.asarray(groups)[train]):
        result.append({'train': train[a].tolist(), 'validation': train[b].tolist()})
        assert not {groups[i] for i in train[a]} & {groups[i] for i in train[b]}
    return result


def fit_binary(x, y, train, test, seed, serialize=False):
    known = np.array([i for i in train if y[i] >= 0], dtype=int)
    target = y[known]
    info = {'training_known': len(known), 'training_positive': int(target.sum())}
    if not len(target) or len(np.unique(target)) < 2:
        probability = float(target.mean()) if len(target) else .5
        return np.full(len(test), probability), {**info, 'constant': probability}
    a, b, medians, scaler, keep = preprocess_fold(x[known], x[test])
    model = LogisticRegression(C=.1, solver='liblinear', class_weight='balanced', max_iter=5000, random_state=seed)
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        model.fit(a, target)
    if serialize:
        info.update({'imputation_median': medians.tolist(), 'scaler_mean': scaler.mean_.tolist(),
                     'scaler_scale': scaler.scale_.tolist(), 'kept_features': np.flatnonzero(keep).tolist(),
                     'constant_design': not bool(keep.any()), 'coef': model.coef_[0].tolist(),
                     'intercept': float(model.intercept_[0]), 'iterations': int(model.n_iter_[0])})
    return model.predict_proba(b)[:, 1], info


def choose_candidate(ap, weights, penalty):
    regularizer = concentration(weights)
    objective = np.asarray(ap) - penalty * regularizer
    # Uniform wins a numerical tie, then the candidate closest to uniform.
    best = np.max(objective)
    tied = np.flatnonzero(np.isclose(objective, best, atol=1e-12, rtol=0))
    chosen = min(tied, key=lambda i: (regularizer[i], i))
    return int(chosen), objective


def select_mixture(bank, y, train, folds, weights, penalty, seed):
    """Every accessed reference label is indexed from the supplied training set."""
    train = np.asarray(train, dtype=int)
    lookup = {int(i): j for j, i in enumerate(train)}
    for split in folds:
        assert set(split['train']) | set(split['validation']) <= set(train)
        assert not set(split['train']) & set(split['validation'])
    known = y[train] >= 0
    target = y[train][known]
    predictions = np.full((len(weights), len(train)), np.nan)
    if not folds or len(np.unique(target)) < 2:
        return {'candidate': 0, 'weights': weights[0].tolist(), 'reason': 'Insufficient classes or groups for inner selection',
                'inner_ap': [None] * len(weights), 'objective': [None] * len(weights)}, predictions
    aps, constant_fits = [], 0
    for k in range(len(weights)):
        for split in folds:
            val = split['validation']
            pred, model = fit_binary(bank[k], y, split['train'], val, seed)
            predictions[k, [lookup[i] for i in val]] = pred
            constant_fits += int('constant' in model)
        assert np.isfinite(predictions[k]).all()
        aps.append(float(average_precision_score(target, predictions[k, known])))
    chosen, objective = choose_candidate(aps, weights, penalty)
    return {'candidate': chosen, 'weights': weights[chosen].tolist(), 'reason': 'Maximum penalized inner out-of-fold AP',
            'inner_ap': aps, 'objective': objective.tolist(), 'constant_inner_fits': constant_fits,
            'training_indices': train.tolist(), 'training_known': len(target), 'training_positive': int(target.sum())}, predictions


def fit_class(j, bank, y, train, test, folds, weights, penalty, seed, directory):
    choice, inner_pred = select_mixture(bank, y[:, j], train, folds, weights, penalty, seed)
    k = choice['candidate']
    pred, model = fit_binary(bank[k], y[:, j], train, test, seed, serialize=True)
    label = LABELS[j]
    save(directory / (label + '.json'), {'selection': choice, 'model': model})
    np.savez_compressed(directory / (label + '-inner.npz'), probabilities=inner_pred, training_indices=np.asarray(train))
    return j, pred, choice


def paired_intervals(y, new, base_predictions, groups, seed):
    names = list(BASELINES)
    members = [np.flatnonzero(np.asarray(groups) == g) for g in sorted(set(groups))]
    rng = np.random.default_rng(seed)
    deltas = {name: [] for name in names}
    for _ in range(200):
        ids = np.concatenate([members[i] for i in rng.integers(len(members), size=len(members))])
        value = classification.fast_macro_ap(y, new, ids)
        for name in names:
            deltas[name].append(value - classification.fast_macro_ap(y, base_predictions[name], ids))
    ids = np.arange(len(y))
    actual = classification.fast_macro_ap(y, new, ids)
    return {name: {'macro_ap_difference': actual - classification.fast_macro_ap(y, base_predictions[name], ids),
                   'paired_task_bootstrap_95_interval': np.quantile(deltas[name], [.025, .975]).tolist(),
                   'resamples': 200} for name in names}


def write_reports(out, result):
    with (out / 'per-label-metrics.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=['protocol', 'method', 'label', 'label_name', 'known', 'positive', 'negative', 'ap', 'roc_auc', 'precision', 'recall', 'f1'])
        writer.writeheader()
        for protocol_name, ev in result['evaluations'].items():
            for method, m in ev['methods'].items():
                for label, title in zip(LABELS, LABEL_NAMES):
                    writer.writerow({'protocol': protocol_name, 'method': method, 'label': label,
                                     'label_name': title, **m['per_label'][label]})
    with (out / 'weights-per-fold.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=['protocol', 'fold', 'label', 'candidate', *LAYERS])
        writer.writeheader()
        for p, ev in result['evaluations'].items():
            for fold in ev['folds']:
                for label, item in fold['selected_weights'].items():
                    writer.writerow({'protocol': p, 'fold': fold['name'], 'label': label,
                                     'candidate': item['candidate'], **dict(zip(LAYERS, item['weights']))})
    with (out / 'final-weights.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=['label', 'label_name', *LAYERS])
        writer.writeheader()
        for label, name in zip(LABELS, LABEL_NAMES):
            writer.writerow({'label': label, 'label_name': name, **dict(zip(LAYERS, result['final_fit'][label]['weights']))})
    body = []
    for p, ev in result['evaluations'].items():
        rows = ''.join(f'<tr><td>{html.escape(name)}</td><td>{m["macro_ap"]:.3f}</td><td>{m["macro_f1"]:.3f}</td></tr>' for name, m in ev['methods'].items())
        body.append(f'<h2>{p}</h2><table><tr><th>Method</th><th>Macro AP</th><th>Macro F1</th></tr>{rows}</table>')
        rows = []
        methods = ev['methods']
        for lab, title in zip(LABELS, LABEL_NAMES):
            old, new = methods['average_graph']['per_label'][lab], methods['class_weighted_graph']['per_label'][lab]
            rows.append(f'<tr><td>{lab} · {html.escape(title)}</td><td>{new["positive"]}/{new["known"]}</td><td>{old["ap"]:.3f}</td><td>{new["ap"]:.3f}</td><td>{new["ap"]-old["ap"]:+.3f}</td><td>{new["f1"]:.3f}</td></tr>')
        body.append('<h3>Per-label comparison</h3><table><tr><th>Class</th><th>Positive/known</th><th>Equal AP</th><th>Learned AP</th><th>Difference</th><th>Learned F1</th></tr>'+''.join(rows)+'</table>')
        body.append('<details><summary>Paired uncertainty estimates</summary><pre>'+html.escape(json.dumps(ev['comparisons'], indent=2))+'</pre></details>')
    rows = []
    for lab, title in zip(LABELS, LABEL_NAMES):
        cells = ''.join(f'<td>{w:.1%}</td>' for w in result['final_fit'][lab]['weights'])
        rows.append(f'<tr><td>{lab} · {html.escape(title)}</td>{cells}</tr>')
    body.append('<h2>Weights fitted using all 300 conversations</h2><p>These deployment/refit weights did not produce the held-out evaluation above. Each outer fold selected its own weights using only its training conversations. Layer weights are model parameters, not causal contributions.</p><div class="scroll"><table><tr><th>Class</th>'+''.join('<th>'+html.escape(layer.replace('_',' '))+'</th>' for layer in LAYERS)+'</tr>'+''.join(rows)+'</table></div>')
    doc = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Class-dependent graph weights</title><style>body{font:16px/1.55 system-ui;max-width:1200px;margin:40px auto;padding:0 24px;color:#19323c;background:#f7fafb}table{border-collapse:collapse;width:100%;background:#fff;margin:16px 0 30px;font-size:14px}td,th{padding:9px 10px;border-bottom:1px solid #dce6e9;text-align:right}td:first-child,th:first-child{text-align:left}th{background:#e4f0ee}h2{margin-top:40px}.note{background:#edf4ff;border-left:4px solid #5575b6;padding:14px 20px}.scroll{overflow-x:auto}pre{white-space:pre-wrap;font-size:12px}img{max-width:100%}a{color:#087a6c}</style><body>'
    doc += f'<h1>Class-dependent graph weights</h1><p>{result["window_events"]}-event windows · 300 conversations · nine score layers · 14 labels</p><p class="note">Each class learns a nonnegative, sum-to-one mixture before its Laplacians are computed. Selection uses three inner task-group folds and 65 fixed candidate mixtures. The outer test labels never participate in selection. This is finite constrained search, not a continuous optimum or end-to-end gradient training.</p>'
    doc += '<p><a href="protocol.json">Frozen protocol</a> · <a href="results.json">Full results</a> · <a href="per-label-metrics.csv">Per-label CSV</a> · <a href="weights-per-fold.csv">Fold weights</a> · <a href="final-weights.csv">Refitted weights</a></p><img src="comparison.png" alt="Held-out average precision comparison and class-specific weight heatmap">'
    doc += ''.join(body) + '<h2>Limitations</h2><ul>' + ''.join('<li>'+html.escape(s)+'</li>' for s in result['limitations'])+'</ul></body></html>'
    (out / 'report.html').write_text(doc)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(16, 8), gridspec_kw={'width_ratios': [1, 2]})
    names = [*BASELINES, 'class_weighted_graph']
    for offset, p, color in [(-.18, 'grouped_5fold', '#159b8e'), (.18, 'leave_framework_out', '#536cba')]:
        axes[0].barh(np.arange(len(names))+offset, [result['evaluations'][p]['methods'][n]['macro_ap'] for n in names], height=.34, color=color, label=p.replace('_',' '))
    axes[0].set_yticks(np.arange(len(names)), [n.replace('_',' ') for n in names])
    axes[0].invert_yaxis(); axes[0].set_xlim(0,1); axes[0].set_xlabel('Macro average precision'); axes[0].legend(fontsize=8)
    w = np.array([result['final_fit'][lab]['weights'] for lab in LABELS])
    im = axes[1].imshow(w, cmap='YlGnBu', vmin=0, vmax=1, aspect='auto')
    axes[1].set_yticks(np.arange(len(LABELS)), LABELS)
    axes[1].set_xticks(np.arange(len(LAYERS)), [l.replace('_','\n') for l in LAYERS], fontsize=8)
    axes[1].set_title('All-data refit weights (not held-out performance)')
    for i in range(len(LABELS)):
        for j in range(len(LAYERS)):
            axes[1].text(j,i,f'{w[i,j]:.0%}',ha='center',va='center',fontsize=8,color='white' if w[i,j]>.55 else '#17343d')
    fig.colorbar(im, ax=axes[1], fraction=.035, pad=.02)
    fig.suptitle(f'Class-dependent mixtures · {result["window_events"]}-event windows')
    fig.tight_layout(); fig.savefig(out / 'comparison.png', dpi=160, bbox_inches='tight'); plt.close(fig)


def run(base, workers=4, penalty=.05, seed=20261004):
    base = Path(base)
    if not read(base / 'classification-audit.json')['passed']:
        raise ValueError('Baseline experiment failed audit')
    base_result = read(base / 'results.json')
    meta = read(base / 'metadata.json')['conversations']
    y = np.array([m['labels'] for m in meta])
    groups = [m['task_group'] for m in meta]
    weights = candidates(seed)
    source_paths = [base / n for n in ['results.json', 'manifest.json', 'splits.json', 'metadata.json', 'feature-names.json', 'feature-matrices.npz']]
    source_paths += sorted((base / 'matrices').glob('*.npz')) + sorted((base / 'windows').glob('*.json'))
    source_paths += sorted(base.glob('predictions-*.npz'))
    hashes = {str(p.relative_to(base)): file_sha(p) for p in source_paths}
    config = {'version': VERSION, 'seed': seed, 'weight_penalty': penalty, 'candidate_count': len(weights),
              'layers': list(LAYERS), 'candidates': weights.tolist(),
              'graph_mixture': 'A[c,t,i,j] = sum_d(alpha[c,d]*mask[d,t,i,j]*A[d,t,i,j]) / sum_d(alpha[c,d]*mask[d,t,i,j]); zero if no positive-weight observed layer. Signed agreement retained.',
              'search': '65 fixed simplex candidates: uniform; 9 layer-directed moves at strengths .25, .5, .75, 1; 28 seeded Dirichlet draws with concentrations .3, 1, 3. Features are recomputed before labels are accessed.',
              'objective': 'Inner pooled out-of-fold AP minus .05 * sum((alpha-1/9)^2)/(1-1/9). Objective ties prefer the mixture closest to uniform.',
              'selection': 'Three shuffled task-group inner folds drawn ONLY from the outer training set. Same inner folds for all classes and candidates. Imputation/scaling fit on inner training only. The outer framework/task splits are unchanged.',
              'classifier': base_result['configuration']['model'],
              'features': 'Same 63 spectral-trajectory features plus 63 UNWEIGHTED layer-availability features as the existing average-graph model. Only adjacency mixing changes.',
              'reference_labels': 'Same 129 masked cells and 300 conversations as baseline.',
              'final_refit': 'After outer evaluation, independently select class weights with three task-group folds on all 300 conversations and refit. These weights are not used for held-out metrics.',
              'confidence_intervals': '200 paired task-group bootstrap resamples of fixed held-out predictions; omits retraining uncertainty.',
              'selection_limit': 'Finite candidate search, no guarantee of global or continuous optimum. Previously examined outer test sets make this follow-up exploratory, not an untouched confirmation set.',
              'no_new_api_requests': True}
    manifest = {'configuration': config, 'base_experiment': base.name, 'source_hashes_sha256': digest(hashes),
                'implementation_sha256': file_sha(__file__), 'classification_sha256': file_sha(classification.__file__),
                'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'sklearn': sklearn.__version__}}
    out = base.parent.parent / 'weighted-classification' / digest(manifest)[:16]
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'results.json').exists() and read(out / 'results.json').get('complete'):
        return out, read(out / 'results.json')
    save(out / 'protocol.json', config); save(out / 'manifest.json', manifest); save(out / 'source-hashes.json', hashes)
    started = time.monotonic()
    bank = feature_bank(base, weights, out)
    splits = read(base / 'splits.json')
    save(out / 'splits.json', splits)
    evaluations = {}
    for protocol_name, folds in splits['protocols'].items():
        prediction = np.full(y.shape, np.nan)
        summaries = []
        for fold_index, fold in enumerate(folds):
            train, test = fold['train'], fold['test']
            inner = inner_splits(train, groups, seed + fold_index)
            directory = out / 'models' / protocol_name / fold['name']
            directory.mkdir(parents=True, exist_ok=True)
            save(directory / 'inner-splits.json', inner)
            selected = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                work = [pool.submit(fit_class, j, bank, y, train, test, inner, weights, penalty, seed, directory) for j in range(len(LABELS))]
                for future in concurrent.futures.as_completed(work):
                    j, pred, choice = future.result()
                    prediction[test, j] = pred
                    selected[LABELS[j]] = {'candidate': choice['candidate'], 'weights': choice['weights'],
                                            'selected_inner_ap': choice['inner_ap'][choice['candidate']],
                                            'equal_inner_ap': choice['inner_ap'][0]}
            summaries.append({'name': fold['name'], 'selected_weights': selected, 'metrics': metrics(y[test], prediction[test])})
            print(json.dumps({'protocol': protocol_name, 'fold': fold['name'], 'nonuniform_classes': sum(v['candidate'] != 0 for v in selected.values())}), flush=True)
        if not np.isfinite(prediction).all():
            raise ValueError('Missing outer predictions')
        old = np.load(base / ('predictions-' + protocol_name + '.npz'))
        ev = {'methods': {name: base_result['evaluations'][protocol_name]['methods'][name] for name in BASELINES},
              'folds': summaries, 'comparisons': paired_intervals(y, prediction, old, groups, seed)}
        ev['methods']['class_weighted_graph'] = metrics(y, prediction)
        evaluations[protocol_name] = ev
        np.savez_compressed(out / ('predictions-' + protocol_name + '.npz'), probabilities=prediction, labels=y)
        jsonl(out / ('predictions-' + protocol_name + '.jsonl'), [
            {'conversation_id': m['conversation_id'], 'reference_labels': dict(zip(LABELS, [None if v<0 else int(v) for v in y[i]])),
             'class_weighted_predictions': dict(zip(LABELS, map(float, prediction[i])))} for i,m in enumerate(meta)])
    # Full-data refit is a separate model, never evaluated on its own training labels.
    train = list(range(len(meta))); inner = inner_splits(train, groups, seed)
    directory = out / 'final-models'; directory.mkdir(exist_ok=True)
    save(directory / 'inner-splits.json', inner)
    final_fit = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        work = [pool.submit(fit_class, j, bank, y, train, [0], inner, weights, penalty, seed, directory) for j in range(len(LABELS))]
        for future in concurrent.futures.as_completed(work):
            j, _, choice = future.result()
            final_fit[LABELS[j]] = {'candidate': choice['candidate'], 'weights': choice['weights'],
                                   'selection_ap': choice['inner_ap'][choice['candidate']]}
    result = {'experiment_id': out.name, 'base_experiment': base.name,
              'window_events': base_result['configuration']['window_events'], 'stride_events': base_result['configuration']['stride_events'],
              'evaluations': evaluations, 'final_fit': final_fit, 'complete': True,
              'elapsed_seconds': time.monotonic() - started,
              'limitations': [config['selection_limit'], config['confidence_intervals'],
                  'Source MAST labels are imperfect reference annotations, not independent human ground truth. Disputed/unknown cells remain masked.',
                  'Rare classes may have very few positive training examples; inspect fold-to-fold weight stability. A large weight is not a causal attribution.',
                  'Prior graph/evidence limitations remain: bounded scoring context, unconfirmed exposure links, unpaired events excluded and lossy spectral/temporal summaries.',
                  'Class weights are constant across time within a model. Availability renormalization can change the effective mixture on each edge.',
                  'Final refitted weights use the full dataset and are separate from the outer-fold models that produced reported metrics.']}
    write_reports(out, result)
    save(out / 'results.json', result)
    return out, result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('base_experiment'); p.add_argument('--workers', type=int, default=4)
    args = p.parse_args()
    out, result = run(args.base_experiment, workers=args.workers)
    print(json.dumps({'output': str(out), 'window': result['window_events'],
                      'metrics': {k: {'macro_ap': v['methods']['class_weighted_graph']['macro_ap'],
                                      'macro_f1': v['methods']['class_weighted_graph']['macro_f1'],
                                      'versus_equal': v['comparisons']['average_graph']}
                                  for k,v in result['evaluations'].items()}}), flush=True)


if __name__ == '__main__':
    main()
