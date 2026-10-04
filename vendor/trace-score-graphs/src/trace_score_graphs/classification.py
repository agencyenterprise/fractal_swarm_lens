"""Offline, conversation-level comparison of windowed score-graph Laplacians.

No API calls. Labels and source metadata never enter feature construction.
See protocol.json in each experiment for frozen mathematical choices.
"""
import argparse
import collections
import csv
import html
import json
import platform
import time
import warnings
from pathlib import Path

import numpy as np
import scipy
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

from .common import digest, file_sha, jsonl, read, save, text_sha
from .pipeline import current_run
from .rubrics import LAYERS, SPECS

VERSION = 'window-laplacian-comparison-v1'
LABELS = ('1.1', '1.2', '1.3', '1.4', '1.5', '2.1', '2.2', '2.3', '2.4', '2.5', '2.6', '3.1', '3.2', '3.3')
LABEL_NAMES = ('Disobey task specification', 'Disobey role specification', 'Step repetition',
               'Loss of conversation history', 'Unaware of termination conditions',
               'Conversation reset', 'Fail to ask for clarification', 'Task derailment',
               'Information withholding', "Ignored other agent's input", 'Reasoning-action mismatch',
               'Premature termination', 'No or incomplete verification', 'Incorrect verification')
SPECTRAL = ('normalized_nonzero_fraction', 'normalized_q25', 'normalized_median',
            'normalized_q75', 'normalized_max', 'heat_trace_0.5', 'heat_trace_2',
            'combinatorial_mean', 'combinatorial_max')
SCORE_STATS = ('mean', 'std', 'q90', 'observed_fraction')
TEMPORAL = ('mean', 'std', 'min', 'max', 'mean_absolute_step', 'first', 'last')
ACTIVITY = ('log_events', 'log_actors', 'log_pairs', 'pair_density', 'candidate_fraction',
            'log_tool_events', 'log_window_count')


def protocol(window, stride, seed):
    if window < 2 or stride < 1 or stride > window:
        raise ValueError('Require window >= 2 and 1 <= stride <= window')
    return {
        'version': VERSION, 'window_events': window, 'stride_events': stride, 'seed': seed,
        'window_unit': 'All normalized events, including prompts, messages, tools and artifacts; not rounds.',
        'windows': 'Trailing fixed-length windows; one partial window for short traces; always include final event.',
        'projection': 'Actor-to-response-actor graph on fully contained source-response pairs. Five message scores decorate the source; four interaction scores decorate the pair. Unpaired messages excluded equally from graph and score baselines.',
        'nodes': 'Actors present in the window; no future actors. Tools, users and environment retained. Same actor set in every layer.',
        'edge_aggregation': 'Mean of scorable weights per directed actor pair, per layer. Count and observation mask saved. Signed agreement retained in [-1,1]; other layers in [0,1].',
        'average_graph': 'Entrywise mean of observed layer adjacencies, ignoring unavailable layers. A measured zero remains observed; missing is not an observed zero.',
        'laplacian': 'Directed bipartite lift B=[[0,A],[A.T,0]], D=diag(sum(abs(B),axis=1)); L=D-B; normalized L=D^-1/2 L D^-1/2, with inverse zero for isolated vertices.',
        'spectral_limit': 'The lift retains directed adjacency in its matrix, but spectra are not complete graph invariants and can lose direction/sign distinctions. Both normalized and combinatorial descriptors are retained.',
        'spectral_descriptors': list(SPECTRAL), 'score_descriptors': list(SCORE_STATS),
        'temporal_summary': list(TEMPORAL),
        'temporal_limit': 'Window descriptors are summarized per conversation; this is not a sequence neural network or onset-localization evaluation.',
        'missingness': 'Graph rows without observed edges give isolated vertices. Per-layer coverage is included with spectral features and in score-only baseline. Missing measurement values are never declared observed zeros.',
        'labels': 'Use a label only if all duplicate-source annotations are binary and agree. Mask disagreements/unknowns per label, not by whole conversation. Never use cohort assignment as target.',
        'task_groups': 'SHA-256 of case-folded whitespace-normalized full extracted task; missing/abbreviated tasks use unique conversation IDs. Exact task matching cannot rule out paraphrased task overlap.',
        'splits': 'Five task-group folds with fixed seed; leave-one-parsed-framework-out additionally purges training traces with a task group in the held-out framework.',
        'model': {'family': 'one binary L2 logistic regression per label', 'C': 0.1,
                  'class_weight': 'balanced', 'solver': 'liblinear', 'max_iter': 5000,
                  'threshold': 0.5, 'scaler': 'training-fold StandardScaler only',
                  'tuning': 'None; fixed before evaluation',
                  'one_class_training': 'Constant training prevalence, recorded explicitly'},
        'metrics': 'Per-label AP (non-interpolated average precision, not trapezoidal PR-AUC), ROC-AUC, precision, recall and F1; macro AP/F1 and masked micro F1. Fixed-threshold outputs are not calibrated probabilities.',
        'bootstrap': '200 paired task-group bootstrap resamples of held-out predictions for macro-AP differences; conditional on fitted folds, not training-resampling uncertainty.',
        'no_new_model_requests': True,
    }


def windows(n, size, stride):
    if n < 1:
        raise ValueError('Conversation has no events')
    ends = list(range(min(size, n) - 1, n, stride))
    if ends[-1] != n - 1:
        ends.append(n - 1)
    return [(max(0, end - size + 1), end) for end in ends]


def signed_laplacians(adjacency):
    a = np.asarray(adjacency, dtype=float)
    if a.ndim != 2 or a.shape[0] != a.shape[1] or not np.isfinite(a).all():
        raise ValueError('Adjacency must be finite and square')
    z = np.zeros_like(a)
    b = np.block([[z, a], [a.T, z]])
    degree = np.abs(b).sum(axis=1)
    lap = np.diag(degree) - b
    inverse = np.zeros_like(degree)
    inverse[degree > 0] = 1 / np.sqrt(degree[degree > 0])
    return lap, inverse[:, None] * lap * inverse[None, :]


def spectrum_features(lap, normalized):
    eig = np.linalg.eigvalsh(normalized)
    raw = np.linalg.eigvalsh(lap)
    if eig.min() < -1e-8 or eig.max() > 2 + 1e-8 or raw.min() < -1e-8:
        raise ValueError('Invalid signed Laplacian spectrum')
    eig = np.maximum(eig, 0)
    raw = np.maximum(raw, 0)
    return np.array([np.mean(eig > 1e-8), *np.quantile(eig, [0.25, 0.5, 0.75]),
                     eig.max(), np.exp(-0.5 * eig).mean(), np.exp(-2 * eig).mean(),
                     raw.mean(), raw.max()])


def mean_graph(adjacencies, observed):
    a, mask = np.asarray(adjacencies), np.asarray(observed)
    counts = mask.sum(axis=0)
    return np.divide((a * mask).sum(axis=0), counts,
                     out=np.zeros_like(a[0]), where=counts > 0), counts


def summarize(sequence, names, prefix):
    x = np.asarray(sequence, dtype=float)
    if x.ndim != 2 or len(x) < 1:
        raise ValueError('Expected nonempty window descriptor sequence')
    result = {}
    for j, name in enumerate(names):
        values = x[:, j]
        finite = values[np.isfinite(values)]
        adjacent = np.isfinite(values[1:]) & np.isfinite(values[:-1])
        changes = np.abs(np.diff(values))[adjacent]
        stats = ([finite.mean(), finite.std(), finite.min(), finite.max(),
                  changes.mean() if len(changes) else np.nan, values[0], values[-1]]
                 if len(finite) else [np.nan] * len(TEMPORAL))
        for stat, val in zip(TEMPORAL, stats):
            result[f'{prefix}.{name}.{stat}'] = float(val) if np.isfinite(val) else None
    return result


def build_features(conversation, layer_rows, window, stride):
    """Input deliberately has no labels or sampling metadata."""
    events = conversation['events']
    event_map = {e['id']: e for e in events}
    maps = {layer: {r['source_event']: r for r in rows} for layer, rows in layer_rows.items()}
    # Every interaction record exists in uptake, even when its score is unavailable.
    pairs = layer_rows['uptake']
    bounds = windows(len(events), window, stride)
    graph_seq = {layer: [] for layer in (*LAYERS, 'average')}
    score_seq = {layer: [] for layer in LAYERS}
    coverage_seq = {layer: [] for layer in LAYERS}
    activity_seq, window_records, arrays = [], [], {}
    paired_sources = set()
    for wi, (start, end) in enumerate(bounds):
        current = events[start:end + 1]
        actors = sorted({e['actor'] for e in current})
        lookup = {actor: i for i, actor in enumerate(actors)}
        eligible = [p for p in pairs if start <= p['source_position'] <= p['available_at'] <= end]
        n = len(actors)
        matrices, masks, descriptors, coverages = [], [], {}, {}
        for layer in LAYERS:
            sums, counts = np.zeros((n, n)), np.zeros((n, n), dtype=int)
            vals = []
            for pair in eligible:
                source, response = event_map[pair['source_event']], event_map[pair['response_event']]
                paired_sources.add(source['id'])
                row = maps[layer].get(source['id'])
                if row is None or row['available_at'] > end or row['status'] != 'scorable' or row['weight'] is None:
                    continue
                value = float(row['weight'])
                i, j = lookup[source['actor']], lookup[response['actor']]
                sums[i, j] += value
                counts[i, j] += 1
                vals.append(value)
            observed = counts > 0
            adjacency = np.divide(sums, counts, out=np.zeros_like(sums), where=observed)
            lap, normalized = signed_laplacians(adjacency)
            spec = spectrum_features(lap, normalized)
            coverage = len(vals) / len(eligible) if eligible else 0.0
            graph_seq[layer].append(spec)
            coverage_seq[layer].append([coverage])
            score_seq[layer].append([np.mean(vals), np.std(vals), np.quantile(vals, .9), coverage]
                                    if vals else [np.nan, np.nan, np.nan, coverage])
            matrices.append(adjacency)
            masks.append(observed)
            descriptors[layer], coverages[layer] = spec.tolist(), coverage
            prefix = f'w{wi:04d}_{layer}'
            arrays.update({prefix + '_adjacency': adjacency, prefix + '_observed_counts': counts,
                           prefix + '_laplacian': lap, prefix + '_normalized_laplacian': normalized})
        average, average_count = mean_graph(matrices, masks)
        lap, normalized = signed_laplacians(average)
        spec = spectrum_features(lap, normalized)
        graph_seq['average'].append(spec)
        descriptors['average'] = spec.tolist()
        prefix = f'w{wi:04d}_average'
        arrays.update({prefix + '_adjacency': average, prefix + '_observed_layers': average_count,
                       prefix + '_laplacian': lap, prefix + '_normalized_laplacian': normalized})
        support = {(event_map[p['source_event']]['actor'], event_map[p['response_event']]['actor']) for p in eligible}
        candidate = sum(p['delivery_basis'] == 'temporal_candidate_unconfirmed_exposure' for p in eligible)
        activity_seq.append([np.log1p(len(current)), np.log1p(n), np.log1p(len(eligible)),
                             len(support) / (n * n), candidate / len(eligible) if eligible else 0,
                             np.log1p(sum(e['kind'].startswith('tool') for e in current)), np.log1p(len(bounds))])
        window_records.append({'window': wi, 'start_position': start, 'end_position': end,
                               'actors': actors, 'pairs': len(eligible), 'unconfirmed_pairs': candidate,
                               'coverage': coverages, 'spectral_descriptors': descriptors})
    features = {}
    all_graph, all_scores, all_coverage = {}, {}, {}
    for layer in LAYERS:
        cov = summarize(coverage_seq[layer], ['observed_fraction'], layer)
        graph = summarize(graph_seq[layer], SPECTRAL, layer)
        scores = summarize(score_seq[layer], SCORE_STATS, layer)
        features['individual_' + layer] = {**graph, **cov}
        all_graph.update(graph)
        all_scores.update(scores)
        all_coverage.update(cov)
    features['average_graph'] = {**summarize(graph_seq['average'], SPECTRAL, 'average'), **all_coverage}
    features['all_laplacians'] = {**all_graph, **all_coverage}
    features['scores_only'] = all_scores
    features['scores_plus_laplacians'] = {**all_scores, **all_graph}
    features['activity_only'] = summarize(activity_seq, ACTIVITY, 'activity')
    return features, window_records, arrays, len(events) - len(paired_sources)


def consensus_labels(item):
    result = []
    for label in LABELS:
        vals = [a['labels'].get(label) for a in item['all_source_annotations']]
        result.append(int(vals[0]) if vals and all(v in (0, 1) for v in vals) and len(set(vals)) == 1 else -1)
    return result


def task_group(conversation):
    task = ' '.join(conversation.get('task', '').casefold().split())
    if task and 'task_missing_or_abbreviated' not in conversation['coverage']['flags']:
        return 'task-' + text_sha(task)
    return 'conversation-' + conversation['conversation_id']


def make_splits(groups, frameworks, seed):
    groups, frameworks = np.asarray(groups), np.asarray(frameworks)
    n = len(groups)
    folds = []
    splitter = GroupKFold(n_splits=5, shuffle=True, random_state=seed)
    for i, (train, test) in enumerate(splitter.split(np.zeros(n), groups=groups)):
        folds.append({'name': f'fold_{i + 1}', 'train': train.tolist(), 'test': test.tolist(), 'purged': []})
    lofo = []
    for framework in sorted(set(frameworks)):
        test = np.flatnonzero(frameworks == framework)
        others = np.flatnonzero(frameworks != framework)
        test_groups = set(groups[test])
        train = [int(i) for i in others if groups[i] not in test_groups]
        purged = [int(i) for i in others if groups[i] in test_groups]
        lofo.append({'name': str(framework), 'train': train, 'test': test.tolist(), 'purged': purged})
    result = {'grouped_5fold': folds, 'leave_framework_out': lofo}
    for name, splits in result.items():
        seen = []
        for split in splits:
            assert not set(groups[split['train']]) & set(groups[split['test']])
            assert not set(split['train']) & set(split['test'])
            seen.extend(split['test'])
        assert sorted(seen) == list(range(n)), name
    return result


def preprocess_fold(train, test):
    """Median imputation, missing indicators and scaling fitted on training only."""
    medians = np.array([np.median(v[np.isfinite(v)]) if np.isfinite(v).any() else 0.0 for v in train.T])
    a = np.concatenate([np.where(np.isfinite(train), train, medians), ~np.isfinite(train)], axis=1)
    b = np.concatenate([np.where(np.isfinite(test), test, medians), ~np.isfinite(test)], axis=1)
    scaler = StandardScaler().fit(a)
    keep = scaler.var_ > 1e-12
    if not keep.any():
        return np.zeros((len(train), 1)), np.zeros((len(test), 1)), medians, scaler, keep
    return scaler.transform(a)[:, keep], scaler.transform(b)[:, keep], medians, scaler, keep


def fit_predict(x, y, train, test, seed):
    probabilities = np.zeros((len(test), len(LABELS)))
    models = {}
    for j, label in enumerate(LABELS):
        known = np.array([i for i in train if y[i, j] >= 0], dtype=int)
        target = y[known, j]
        if not len(known) or len(np.unique(target)) < 2:
            probability = float(target.mean()) if len(target) else 0.5
            probabilities[:, j] = probability
            models[label] = {'constant': probability, 'training_known': len(known), 'training_positive': int(target.sum())}
            continue
        a, b, medians, scaler, keep = preprocess_fold(x[known], x[test])
        model = LogisticRegression(C=0.1, solver='liblinear', class_weight='balanced', max_iter=5000,
                                   random_state=seed)
        with warnings.catch_warnings():
            warnings.simplefilter('error', ConvergenceWarning)
            model.fit(a, target)
        probabilities[:, j] = model.predict_proba(b)[:, 1]
        models[label] = {'training_known': len(known), 'training_positive': int(target.sum()),
                         'imputation_median': medians.tolist(), 'scaler_mean': scaler.mean_.tolist(),
                         'scaler_scale': scaler.scale_.tolist(), 'kept_features': np.flatnonzero(keep).tolist(),
                         'constant_design': not bool(keep.any()),
                         'coef': model.coef_[0].tolist(), 'intercept': float(model.intercept_[0]),
                         'iterations': int(model.n_iter_[0])}
    return probabilities, models


def metrics(y, p):
    result = {}
    for j, label in enumerate(LABELS):
        mask = (y[:, j] >= 0) & np.isfinite(p[:, j])
        target, pred = y[mask, j], p[mask, j]
        positives, negatives = int(target.sum()), int(len(target) - target.sum())
        if not len(target):
            result[label] = {'known': 0, 'positive': 0, 'negative': 0, 'ap': None, 'roc_auc': None,
                             'precision': None, 'recall': None, 'f1': None}
            continue
        precision, recall, f1, _ = precision_recall_fscore_support(target, pred >= .5, average='binary', zero_division=0)
        result[label] = {'known': len(target), 'positive': positives, 'negative': negatives,
                         'ap': float(average_precision_score(target, pred)) if positives and negatives else None,
                         'roc_auc': float(roc_auc_score(target, pred)) if positives and negatives else None,
                         'precision': float(precision), 'recall': float(recall), 'f1': float(f1)}
    valid = (y >= 0) & np.isfinite(p)
    _, _, micro, _ = precision_recall_fscore_support(y[valid], p[valid] >= .5, average='binary', zero_division=0)
    macro = lambda key: float(np.mean([v[key] for v in result.values() if v[key] is not None]))
    return {'macro_ap': macro('ap'), 'macro_roc_auc': macro('roc_auc'), 'macro_f1': macro('f1'),
            'micro_f1': float(micro), 'per_label': result}


def fast_macro_ap(y, p, indices):
    scores = []
    yy, pp = y[indices], p[indices]
    for j in range(y.shape[1]):
        mask = yy[:, j] >= 0
        labels, preds = yy[mask, j], pp[mask, j]
        npos = labels.sum()
        if not 0 < npos < len(labels):
            continue
        order = np.argsort(-preds, kind='stable')
        labels, preds = labels[order], preds[order]
        ends = np.r_[np.flatnonzero(np.diff(preds)), len(preds) - 1]
        true_positive = np.cumsum(labels)[ends]
        recalls = true_positive / npos
        precision = true_positive / (ends + 1)
        scores.append(float(np.sum(np.diff(np.r_[0, recalls]) * precision)))
    return float(np.mean(scores))


def paired_bootstrap(y, predictions, groups, seed, repetitions=200):
    unique = sorted(set(groups))
    members = [np.flatnonzero(np.asarray(groups) == group) for group in unique]
    pairs = [('average_graph', 'scores_only'), ('all_laplacians', 'scores_only'),
             ('scores_plus_laplacians', 'scores_only'), ('all_laplacians', 'average_graph'),
             ('prediction_average', 'all_laplacians')]
    rng = np.random.default_rng(seed)
    values = {name: [] for name in {a for pair in pairs for a in pair}}
    for _ in range(repetitions):
        idx = np.concatenate([members[i] for i in rng.integers(len(members), size=len(members))])
        for name in values:
            values[name].append(fast_macro_ap(y, predictions[name], idx))
    result = {}
    for a, b in pairs:
        diff = np.asarray(values[a]) - np.asarray(values[b])
        actual = fast_macro_ap(y, predictions[a], np.arange(len(y))) - fast_macro_ap(y, predictions[b], np.arange(len(y)))
        result[a + '_minus_' + b] = {'macro_ap_difference': actual,
                                    'paired_group_bootstrap_95_interval': np.quantile(diff, [.025, .975]).tolist(),
                                    'resamples': repetitions}
    return result


def evaluate(out, features, metadata, seed):
    y = np.asarray([m['labels'] for m in metadata])
    groups, frameworks = [m['task_group'] for m in metadata], [m['framework'] for m in metadata]
    splits = make_splits(groups, frameworks, seed)
    save(out / 'splits.json', {'conversation_order': [m['conversation_id'] for m in metadata], 'protocols': splits})
    feature_matrices, feature_names = {}, {}
    for name in features[0]:
        names = sorted(features[0][name])
        assert all(set(f[name]) == set(names) for f in features)
        feature_names[name] = names
        feature_matrices[name] = np.array([[f[name][k] if f[name][k] is not None else np.nan for k in names] for f in features])
    save(out / 'feature-names.json', feature_names)
    np.savez_compressed(out / 'feature-matrices.npz', **feature_matrices, labels=y)
    summaries, all_predictions = {}, {}
    for split_name, folds in splits.items():
        predictions = {name: np.full(y.shape, np.nan) for name in feature_matrices}
        predictions['training_prevalence'] = np.full(y.shape, np.nan)
        fold_summaries, constants = [], []
        for fold in folds:
            train, test = np.array(fold['train']), np.array(fold['test'])
            for j in range(len(LABELS)):
                yy = y[train, j]
                yy = yy[yy >= 0]
                predictions['training_prevalence'][test, j] = yy.mean() if len(yy) else .5
            for name, x in feature_matrices.items():
                pred, models = fit_predict(x, y, train, test, seed)
                predictions[name][test] = pred
                save(out / 'models' / split_name / fold['name'] / (name + '.json'), models)
                constants.extend({'fold': fold['name'], 'model': name, 'label': label, **m}
                                 for label, m in models.items() if 'constant' in m)
            fold_summary = {'fold': fold['name'], 'training': len(train), 'test': len(test),
                            'purged_task_matches': len(fold['purged']),
                            'methods': {name: metrics(y[test], pred[test]) for name, pred in predictions.items()}}
            fold_summaries.append(fold_summary)
            print(json.dumps({'evaluation': split_name, 'fold': fold['name'], 'test_conversations': len(test)}), flush=True)
        predictions['prediction_average'] = np.mean([predictions['individual_' + layer] for layer in LAYERS], axis=0)
        for name, pred in predictions.items():
            if not np.isfinite(pred).all() or np.any((pred < 0) | (pred > 1)):
                raise ValueError(f'Incomplete or invalid predictions for {name}')
        summaries[split_name] = {'methods': {name: metrics(y, pred) for name, pred in predictions.items()},
                                'folds': fold_summaries, 'constant_training_cases': constants,
                                'comparisons': paired_bootstrap(y, predictions, groups, seed)}
        for fold, summary in zip(folds, fold_summaries):
            ids = fold['test']
            summary['methods']['prediction_average'] = metrics(y[ids], predictions['prediction_average'][ids])
        all_predictions[split_name] = predictions
        np.savez_compressed(out / ('predictions-' + split_name + '.npz'), **predictions, labels=y)
        records = []
        for i, m in enumerate(metadata):
            records.append({'conversation_id': m['conversation_id'], 'framework': m['framework'],
                            'reference_labels': dict(zip(LABELS, [None if v < 0 else int(v) for v in y[i]])),
                            'predictions': {name: dict(zip(LABELS, map(float, pred[i]))) for name, pred in predictions.items()}})
        jsonl(out / ('predictions-' + split_name + '.jsonl'), records)
    return summaries, feature_names


def csv_results(out, results):
    with (out / 'per-label-metrics.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=['protocol', 'method', 'label', 'label_name', 'known', 'positive', 'negative', 'ap', 'roc_auc', 'precision', 'recall', 'f1'])
        writer.writeheader()
        for protocol_name, report in results['evaluations'].items():
            for method, metric in report['methods'].items():
                for label, name in zip(LABELS, LABEL_NAMES):
                    writer.writerow({'protocol': protocol_name, 'method': method, 'label': label,
                                     'label_name': name, **metric['per_label'][label]})


def write_html(out, results):
    esc = html.escape
    sections = []
    for protocol_name, evaluation in results['evaluations'].items():
        rows = []
        for method, m in evaluation['methods'].items():
            rows.append(f'<tr><td>{esc(method)}</td><td>{m["macro_ap"]:.3f}</td><td>{m["macro_f1"]:.3f}</td><td>{m["macro_roc_auc"]:.3f}</td></tr>')
        sections.append(f'<h2>{esc(protocol_name)}</h2><table><thead><tr><th>Method</th><th>Macro AP</th><th>Macro F1</th><th>Macro ROC-AUC</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
        names = ['scores_only', 'average_graph', 'all_laplacians', 'scores_plus_laplacians']
        rows = []
        for label, title in zip(LABELS, LABEL_NAMES):
            count = evaluation['methods']['scores_only']['per_label'][label]
            cells = ''.join(f'<td>{evaluation["methods"][name]["per_label"][label]["ap"]:.3f}</td>' for name in names)
            rows.append(f'<tr><td>{label} · {esc(title)}</td><td>{count["positive"]}/{count["known"]}</td>{cells}</tr>')
        sections.append('<h3>Per-label average precision</h3><table><thead><tr><th>Label</th><th>Positive / known</th>' + ''.join(f'<th>{esc(n)}</th>' for n in names) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>')
        sections.append('<details><summary>Paired uncertainty estimates</summary><pre>' + esc(json.dumps(evaluation['comparisons'], indent=2)) + '</pre></details>')
    config = results['configuration']
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MAST graph classification experiment</title><style>body{font:16px/1.55 system-ui,sans-serif;max-width:1120px;margin:48px auto;padding:0 24px;color:#182c36;background:#f7fafb}h1{font-size:30px}h2{margin-top:40px}table{border-collapse:collapse;width:100%;background:white;font-size:14px;margin:16px 0 28px}th,td{padding:9px 12px;border-bottom:1px solid #d8e3e7;text-align:right}td:first-child,th:first-child{text-align:left}th{background:#e7f0f0}a{color:#076f68}.note{border-left:4px solid #c99635;padding:12px 18px;background:#fff8e8}pre{white-space:pre-wrap;font-size:13px}img{max-width:100%}</style><body>'
    document += f'<h1>MAST · windowed graph classification</h1><p>300 conversations · {config["window_events"]}-event windows · {config["stride_events"]}-event step · nine score layers · saved Jev scores only.</p>'
    document += '<p class="note">Exploratory reference-label prediction, not evidence of causal propagation or human-validated failures. Disputed annotation cells are masked. All windows from a conversation remain together. Framework-held-out testing also removes exact task overlap from training.</p>'
    document += '<p><a href="protocol.json">Frozen protocol</a> · <a href="results.json">Full results</a> · <a href="per-label-metrics.csv">Per-label CSV</a> · <a href="metadata.json">Conversation and label coverage</a></p><img src="comparison.png" alt="Comparison of macro average precision and per-label graph-minus-score performance">'
    document += ''.join(sections)
    document += '<h2>Interpretation and limitations</h2><ul>' + ''.join('<li>' + esc(s) + '</li>' for s in results['limitations']) + '</ul><h2>Exact protocol</h2><pre>' + esc(json.dumps(config, indent=2)) + '</pre></body></html>'
    (out / 'report.html').write_text(document)


def plot_results(out, results):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    methods = ['training_prevalence', 'activity_only', 'scores_only', 'average_graph',
               *('individual_' + s for s in LAYERS), 'all_laplacians', 'prediction_average', 'scores_plus_laplacians']
    fig, axes = plt.subplots(1, 2, figsize=(15, 9), gridspec_kw={'width_ratios': [1.2, 1]})
    pos = np.arange(len(methods))
    for offset, (split, color, title) in zip([-.18, .18], [('grouped_5fold', '#159b8e', 'Task-group folds'), ('leave_framework_out', '#536cba', 'Framework held out')]):
        axes[0].barh(pos + offset, [results['evaluations'][split]['methods'][m]['macro_ap'] for m in methods], height=.34, color=color, label=title)
    axes[0].set_yticks(pos, [m.replace('individual_', '').replace('_', ' ') for m in methods])
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, 1)
    axes[0].set_xlabel('Macro average precision (higher is better)')
    axes[0].set_title('Held-out conversation classification')
    axes[0].legend(loc='lower right', fontsize=9)
    ev = results['evaluations']['leave_framework_out']['methods']
    compare = ['average_graph', 'all_laplacians', 'scores_plus_laplacians']
    delta = np.array([[ev[m]['per_label'][lab]['ap'] - ev['scores_only']['per_label'][lab]['ap'] for m in compare] for lab in LABELS])
    bound = max(.1, float(np.max(np.abs(delta))))
    im = axes[1].imshow(delta, cmap='RdBu', vmin=-bound, vmax=bound, aspect='auto')
    axes[1].set_yticks(range(len(LABELS)), LABELS)
    axes[1].set_xticks(range(3), ['Average\ngraph', 'All\nLaplacians', 'Scores +\nLaplacians'])
    axes[1].set_title('Framework held out: AP difference\nfrom scores-only baseline')
    for i in range(len(LABELS)):
        for j in range(3):
            axes[1].text(j, i, f'{delta[i,j]:+.2f}', ha='center', va='center', color='white' if abs(delta[i,j]) > bound*.65 else '#172b36', fontsize=10)
    fig.colorbar(im, ax=axes[1], fraction=.035, pad=.03)
    fig.suptitle(f'MAST · {results["configuration"]["window_events"]}-event windows / {results["configuration"]["stride_events"]}-event step', fontsize=16)
    fig.tight_layout()
    fig.savefig(out / 'comparison.png', dpi=160, bbox_inches='tight')
    plt.close(fig)


def run_experiment(root, window=20, stride=10, seed=20261004):
    root = Path(root)
    run = current_run(root)
    if not read(run / 'integrity-audit.json')['passed']:
        raise ValueError('Source scoring integrity audit did not pass')
    configuration = protocol(window, stride, seed)
    source_files = sorted([root / 'selection.json', *list((run / 'schemas').glob('*.json')),
                           *list((run / 'scores').glob('*/*.jsonl'))])
    hashes = {str(p.relative_to(root)): file_sha(p) for p in source_files}
    manifest = {'configuration': configuration, 'source_run': run.name,
                'source_hashes_sha256': digest(hashes), 'implementation_sha256': file_sha(__file__),
                'versions': {'python': platform.python_version(), 'numpy': np.__version__,
                             'scipy': scipy.__version__, 'sklearn': sklearn.__version__}}
    experiment_id = digest(manifest)[:16]
    out = root / 'classification' / experiment_id
    if (out / 'results.json').exists() and read(out / 'results.json').get('complete'):
        return out, read(out / 'results.json')
    out.mkdir(parents=True, exist_ok=True)
    save(out / 'protocol.json', configuration)  # Written before any fitting/results.
    save(out / 'manifest.json', manifest)
    save(out / 'source-hashes.json', hashes)
    items = read(root / 'selection.json')['conversations']
    metadata, features, feature_records = [], [], []
    totals = collections.Counter()
    started = time.monotonic()
    for i, item in enumerate(items):
        cid = item['conversation_id']
        conv = read(run / 'schemas' / (cid + '.json'))
        rows = {layer: [json.loads(line) for line in (run / 'scores' / layer / (cid + '.jsonl')).read_text().splitlines()] for layer in LAYERS}
        extracted, windows_data, arrays, unpaired = build_features(conv, rows, window, stride)
        features.append(extracted)
        feature_records.append({'conversation_id': cid, 'features': extracted})
        metadata.append({'conversation_id': cid, 'framework': conv['adapter'], 'source_framework': item['framework'],
                         'task_group': task_group(conv), 'labels': consensus_labels(item),
                         'disputed_annotations': item['disputed_annotations'], 'cohort': item['cohort'],
                         'events': len(conv['events']), 'windows': len(windows_data),
                         'unpaired_or_not_contained_events': unpaired, 'coverage_flags': conv['coverage']['flags']})
        save(out / 'windows' / (cid + '.json'), windows_data)
        matrix_dir = out / 'matrices'
        matrix_dir.mkdir(exist_ok=True)
        np.savez_compressed(matrix_dir / (cid + '.npz'), **arrays)
        totals.update({'conversations': 1, 'windows': len(windows_data), 'laplacian_pairs': len(windows_data) * 10,
                       'events': len(conv['events']), 'unpaired_or_not_contained_events': unpaired,
                       'short_conversations': int(len(conv['events']) < window)})
        if (i + 1) % 50 == 0:
            print(json.dumps({'features_completed': i + 1, 'windows': totals['windows']}), flush=True)
    save(out / 'metadata.json', {'labels': dict(zip(LABELS, LABEL_NAMES)), 'conversations': metadata})
    jsonl(out / 'features.jsonl', feature_records)
    evaluations, feature_names = evaluate(out, features, metadata, seed)
    y = np.array([m['labels'] for m in metadata])
    result = {'experiment_id': experiment_id, 'source_run': run.name, 'configuration': configuration,
              'totals': dict(totals), 'masked_label_cells': int((y < 0).sum()),
              'unique_task_groups': len({m['task_group'] for m in metadata}),
              'feature_dimensions': {name: len(names) for name, names in feature_names.items()},
              'evaluations': evaluations, 'complete': True,
              'elapsed_seconds': time.monotonic() - started,
              'limitations': [
                  'Exploratory sample selected for cohort coverage, not representative deployment prevalence. No classifier output is claimed calibrated.',
                  'Labels are source MAST annotations; disagreement/unknown cells are masked. Rare labels have few positives.',
                  'Task grouping detects exact normalized task text only. Missing, abbreviated or paraphrased task identities can conceal overlap.',
                  'Graphs include temporal candidate interactions whose exposure is unconfirmed. Correspondence is not proof of causal influence.',
                  'Five node-score layers are projected onto paired interactions using their source score; unpaired messages are excluded from both graph and score baselines.',
                  'Evidence was previously scored under bounded-context truncation; this experiment does not repair missing source context.',
                  'Fixed window summaries do not establish onset times; some conversations contain only one window.',
                  'Spectral descriptors are not complete graph invariants; the directed lift can have identical spectra for different directions or signs.',
                  'The graph variants include score-availability features, and framework/format can influence missingness. Activity and score-only baselines are included.',
                  'Bootstrap intervals condition on fitted folds and omit training-set uncertainty. Window sizes are reported separately; no test-selected best configuration.',
              ]}
    save(out / 'results.json', result)
    csv_results(out, result)
    plot_results(out, result)
    write_html(out, result)
    save(root / 'latest-classification.json', {'experiment_id': experiment_id, 'path': str(out)})
    return out, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='data')
    parser.add_argument('--window', type=int, default=20)
    parser.add_argument('--stride', type=int, default=10)
    parser.add_argument('--seed', type=int, default=20261004)
    args = parser.parse_args()
    out, result = run_experiment(args.data, args.window, args.stride, args.seed)
    print(json.dumps({'output': str(out), 'totals': result['totals'],
                      'metrics': {p: {m: {'macro_ap': v['macro_ap'], 'macro_f1': v['macro_f1']}
                                      for m, v in r['methods'].items()} for p, r in result['evaluations'].items()}}), flush=True)


if __name__ == '__main__':
    main()
