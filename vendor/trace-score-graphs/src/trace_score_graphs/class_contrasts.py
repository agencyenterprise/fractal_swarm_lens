"""Descriptive class contrasts of saved score-layer and cross-fitted Laplacians.

No model requests, classifier refits, or changes to the original graph artifacts.
The conversation is the analysis unit; overlapping windows are never replicates.
"""
import argparse
import base64
import collections
import csv
import html
import itertools
import json
from pathlib import Path

import numpy as np

from .classification import LABELS, LABEL_NAMES, LAYERS, SPECTRAL, signed_laplacians, spectrum_features
from .common import file_sha, digest, read, save, jsonl
from .weighted_classification import mix_graphs

VERSION = 'class-laplacian-contrasts-v1'
GRAPHS = (*LAYERS, 'average', *('mixture_' + c for c in LABELS))
METRICS = (*SPECTRAL, 'score_mean', 'coverage', 'aligned_matrix_drift')
KEY_METRICS = ('score_mean', 'coverage', 'combinatorial_mean', 'normalized_nonzero_fraction', 'heat_trace_2', 'aligned_matrix_drift')
DISPLAY = {'task_relevance': 'Task relevance', 'novelty': 'Novelty', 'instruction_content': 'Instruction content',
           'verification_evidence': 'Verification evidence', 'harmfulness': 'Harmfulness', 'agreement': 'Agreement',
           'acknowledgment': 'Acknowledgment', 'uptake': 'Uptake', 'requirement_fulfillment': 'Requirement fulfillment',
           'average': 'Equal-weight graph', 'score_mean': 'Source score', 'coverage': 'Scorable coverage',
           'combinatorial_mean': 'Laplacian intensity', 'normalized_nonzero_fraction': 'Normalized rank fraction',
           'heat_trace_2': 'Heat trace (t=2)', 'aligned_matrix_drift': 'Aligned matrix drift'}
CLASS_NOTES = {
    '1.1': 'Task relevance is lower, including after the length comparison. The larger agreement-intensity difference shrinks with length adjustment. These are modest contrasts; held-out AP does not exceed this class’s prevalence.',
    '1.2': 'Novelty and verification intensity are higher, but their contrasts shrink within length bins. The large drift differences use only nine positive multi-window traces in the 20-event run and are fragile.',
    '1.3': 'One of the clearest profiles: lower acknowledgment intensity and normalized rank, lower novelty, and more novelty-matrix drift. Acknowledgment remains lower in the length comparison and in the 10-event run. This class strongly overlaps failure to recognize termination.',
    '1.4': 'Lower acknowledgment and agreement intensity initially stand out. Most of that intensity separation disappears or reverses within length bins, so there is no stable, isolated loss-of-history signature here.',
    '1.5': 'Requirement fulfillment and novelty are lower. Requirement-fulfillment intensity stays lower after the length comparison and with 10-event windows. Agreement matrices change less between windows. Strong overlap with repetition prevents a clean attribution to one label.',
    '2.1': 'Lower acknowledgment and uptake intensity, and lower fulfillment scores. Some differences persist within length bins, but acknowledgment weakens with 10-event windows. Only 28 consensus positives; the existing held-out classifier does not beat prevalence.',
    '2.2': 'Slightly higher verification intensity and lower agreement, but most effects are small after the length comparison. Current graph summaries do not show a strong signature specific to failing to ask for clarification.',
    '2.3': 'Lower task relevance is consistent across source scores, raw Laplacian intensity, both window sizes and the length comparison. The equal and learned combinations are also lower, although mixing does not isolate which behavior caused the change.',
    '2.4': 'Only 16 consensus positives. Higher relevance scores coexist with lower fulfillment and uptake intensity. Acknowledgment reverses direction after the length comparison; this is not a stable all-layer failure pattern.',
    '2.5': 'Agreement and acknowledgment are lower, while instruction content is somewhat higher. The acknowledgment contrast persists within length bins and at 10 events. This is compatible with weak incorporation of other agents’ input, but it is an association, not causal evidence.',
    '2.6': 'Acknowledgment and uptake intensity are lower initially, but shrink substantially within length bins. There is no strong, separate intensity signature after that adjustment. This class strongly co-occurs with incorrect verification.',
    '3.1': 'Novelty is higher relative to other failures, with a modest effect that persists in the length comparison. Fulfillment is somewhat lower. This does not mean novelty causes premature termination; the reference group contains many repetitive traces.',
    '3.2': 'Verification-evidence intensity does not clearly separate this class. Fulfillment scores are lower and agreement matrices drift less, but most intensity contrasts are small. The verification rubric and these graph summaries are not sufficient evidence that verification failures have been captured.',
    '3.3': 'Verification and acknowledgment intensity are modestly lower, with the same directions after the length comparison and at 10 events. Their sizes are much smaller than the repetition and termination-recognition profiles.'}


def finite_json(obj):
    if isinstance(obj, dict):
        return {str(k): finite_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite_json(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return finite_json(obj.tolist())
    if isinstance(obj, (float, np.floating)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def safe_mean(a, axis=0):
    a = np.asarray(a, dtype=float)
    n = np.isfinite(a).sum(axis=axis)
    return np.divide(np.nansum(a, axis=axis), n, out=np.full(np.shape(n), np.nan), where=n > 0)


def populations(y):
    y = np.asarray(y)
    clean = np.all(y == 0, axis=1)
    problem = np.any(y == 1, axis=1)
    return clean, problem, ~(clean | problem)


def aligned_drift(a, actors_a, b, actors_b):
    """OUT and IN copies of each actor are aligned separately before subtraction."""
    union = sorted(set(actors_a) | set(actors_b))
    lookup = {actor: i for i, actor in enumerate(union)}
    aligned = []
    for matrix, actors in ((a, actors_a), (b, actors_b)):
        ids = [lookup[v] for v in actors]
        indices = ids + [i + len(union) for i in ids]
        result = np.zeros((2*len(union), 2*len(union)))
        result[np.ix_(indices, indices)] = matrix
        aligned.append(result)
    return np.linalg.norm(aligned[0] - aligned[1], 'fro') / np.sqrt(2*len(union))


def weighted_delta(x, positive, reference, weights):
    """Cliff's delta for each bootstrap row. Numerical ties rounded to 10 decimals."""
    x = np.round(np.asarray(x), 10)
    p = np.flatnonzero(positive & np.isfinite(x))
    r = np.flatnonzero(reference & np.isfinite(x))
    if not len(p) or not len(r):
        return np.full(len(weights), np.nan)
    r = r[np.argsort(x[r], kind='stable')]
    cumulative = np.column_stack([np.zeros(len(weights)), np.cumsum(weights[:, r], axis=1)])
    less = cumulative[:, np.searchsorted(x[r], x[p], side='left')]
    greater = cumulative[:, -1, None] - cumulative[:, np.searchsorted(x[r], x[p], side='right')]
    return np.sum(weights[:, p] * (less - greater), axis=1) / (weights[:, p].sum(axis=1) * cumulative[:, -1])


def contrast(x, positive, reference, strata, weights):
    valid = np.isfinite(x)
    p, r = positive & valid, reference & valid
    pooled = weighted_delta(x, p, r, weights[:1])[0]
    estimates, support, members = [], [], np.zeros(len(x), dtype=bool)
    for s in sorted(set(strata)):
        keep = strata == s
        if (p & keep).any() and (r & keep).any():
            estimates.append(weighted_delta(x, p & keep, r & keep, weights))
            support.append({'stratum': s, 'positive': int((p & keep).sum()), 'reference': int((r & keep).sum())})
            members |= keep
    effect = np.mean(estimates, axis=0) if estimates else np.full(len(weights), np.nan)
    return {'positive_n': int(p.sum()), 'reference_n': int(r.sum()),
            'positive_median': float(np.median(x[p])) if p.any() else None,
            'reference_median': float(np.median(x[r])) if r.any() else None,
            'pooled_delta': pooled, 'adjusted_delta': effect[0],
            'interval_low': np.quantile(effect[1:], .025) if estimates and len(weights)>1 else None,
            'interval_high': np.quantile(effect[1:], .975) if estimates and len(weights)>1 else None,
            'strata': support, 'overlap_positive': int((p & members).sum()),
            'overlap_reference': int((r & members).sum())}


def cluster_weights(groups, repetitions, seed):
    _, ids = np.unique(groups, return_inverse=True)
    rng = np.random.default_rng(seed)
    return np.vstack([np.ones(len(groups)), rng.exponential(size=(repetitions, ids.max()+1))[:, ids]])


def heldout_weights(base, weighted, metadata):
    """Select only weights learned without the conversation's entire framework."""
    folds = read(base / 'splits.json')['protocols']['leave_framework_out']
    result = np.full((len(metadata), len(LABELS), len(LAYERS)), np.nan)
    assignments, checked = [], set()
    for fold in folds:
        train, test = set(fold['train']), set(fold['test'])
        assert not train & test
        assert not {metadata[i]['task_group'] for i in train} & {metadata[i]['task_group'] for i in test}
        assert {metadata[i]['framework'] for i in test} == {fold['name']}
        assert fold['name'] not in {metadata[i]['framework'] for i in train}
        for j, label in enumerate(LABELS):
            selection = read(weighted / 'models' / 'leave_framework_out' / fold['name'] / (label + '.json'))['selection']
            assert set(selection.get('training_indices', fold['train'])) <= train
            result[fold['test'], j] = selection['weights']
            assignments.append({'framework': fold['name'], 'class': label, 'candidate': selection['candidate'],
                                'weights': selection['weights']})
        assert not checked & test
        checked |= test
    assert checked == set(range(len(metadata))) and np.isfinite(result).all()
    return result, assignments


def extract(base, weighted, out):
    metadata = read(base / 'metadata.json')['conversations']
    assert read(weighted / 'splits.json') == read(base / 'splits.json')
    alphas, assignments = heldout_weights(base, weighted, metadata)
    names = read(base / 'feature-names.json')
    original = np.load(base / 'feature-matrices.npz')
    ci = {n: i for i, n in enumerate(names['scores_only'])}
    all_values, window_values, window_meta, diagnostics = [], [], [], []
    pair_total = np.zeros((9, 9), dtype=int)
    pair_same = pair_total.copy()
    pair_different_raw = pair_total.copy()
    pair_same_matrix = pair_total.copy()
    mixture_pairs = {'both_nonempty': 0, 'same_spectrum': 0, 'same_adjacency': 0}
    examples = []
    checked, max_error = 0, 0.
    for i, item in enumerate(metadata):
        cid = item['conversation_id']
        wins = read(base / 'windows' / (cid + '.json'))
        values, previous = [], None
        with np.load(base / 'matrices' / (cid + '.npz')) as matrices:
            for w in wins:
                prefix = f'w{w["window"]:04d}_'
                aa = np.stack([matrices[prefix + d + '_adjacency'] for d in LAYERS])
                masks = np.stack([matrices[prefix + d + '_observed_counts'] > 0 for d in LAYERS])
                adjacencies = list(aa) + [matrices[prefix + 'average_adjacency']]
                adjacencies += [mix_graphs(aa, masks, alpha)[0] for alpha in alphas[i]]
                v = np.full((len(GRAPHS), len(METRICS)), np.nan)
                norm, eigs = [], []
                for g, a in enumerate(adjacencies):
                    lap, normalized = signed_laplacians(a)
                    spec = spectrum_features(lap, normalized)
                    v[g, :9] = spec
                    norm.append(normalized)
                    eigs.append(np.maximum(np.linalg.eigvalsh(normalized), 0))
                    if g < 10:
                        for key, actual in (('_laplacian', lap), ('_normalized_laplacian', normalized)):
                            expected = matrices[prefix + GRAPHS[g] + key]
                            np.testing.assert_allclose(actual, expected, atol=1e-9, rtol=1e-9)
                            max_error = max(max_error, float(np.max(np.abs(actual-expected))))
                        np.testing.assert_allclose(spec, w['spectral_descriptors'][GRAPHS[g]], atol=1e-8, rtol=1e-8)
                        checked += 1
                    if g < 9:
                        v[g, 10] = w['coverage'][LAYERS[g]]
                    if previous is not None:
                        v[g, 11] = aligned_drift(previous[0][g], previous[1], normalized, w['actors'])
                    nonzero = np.any(np.abs(a) > 1e-10)
                    diagnostics.append({'conversation_id': cid, 'window': w['window'], 'graph': GRAPHS[g],
                                        'actors': len(w['actors']), 'nonzero': bool(nonzero),
                                        'normalized_zero_eigenvalues': int((eigs[g] < 1e-8).sum()),
                                        'normalized_lambda2': float(eigs[g][1]),
                                        'only_0_1_2': bool(np.all(np.min(np.abs(eigs[g][:, None]-np.array([0, 1, 2])), axis=1)<1e-8))})
                for a, b in itertools.combinations(range(9), 2):
                    if not np.any(np.abs(aa[a])>1e-10) or not np.any(np.abs(aa[b])>1e-10):
                        continue
                    equal = np.allclose(eigs[a], eigs[b], atol=1e-8, rtol=0)
                    raw_diff = not np.allclose(aa[a], aa[b], atol=1e-8, rtol=0)
                    pair_total[a, b] += 1
                    pair_same[a, b] += int(equal)
                    pair_different_raw[a, b] += int(equal and raw_diff)
                    pair_same_matrix[a, b] += int(np.allclose(norm[a], norm[b], atol=1e-8, rtol=0))
                    if equal and raw_diff and len(examples) < 4 and a == 0 and b == 3:
                        examples.append({'conversation_id': cid, 'framework': item['framework'], 'window': w['window'],
                                         'actors': w['actors'], 'layers': [LAYERS[a], LAYERS[b]],
                                         'adjacencies': [aa[a].tolist(), aa[b].tolist()],
                                         'normalized_spectrum': eigs[a].tolist(),
                                         'raw_intensities': [float(v[a, 7]), float(v[b, 7])]})
                for a, b in itertools.combinations(range(10, len(GRAPHS)), 2):
                    if np.any(np.abs(adjacencies[a])>1e-10) and np.any(np.abs(adjacencies[b])>1e-10):
                        mixture_pairs['both_nonempty'] += 1
                        mixture_pairs['same_spectrum'] += int(np.allclose(eigs[a], eigs[b], atol=1e-8, rtol=0))
                        mixture_pairs['same_adjacency'] += int(np.allclose(adjacencies[a], adjacencies[b], atol=1e-8, rtol=0))
                values.append(v)
                window_values.append(v)
                window_meta.append({'conversation_index': i, 'window': w['window'], 'actors': w['actors'],
                                    'start_position': w['start_position'], 'end_position': w['end_position'],
                                    'phase': 'whole_trace' if len(wins)==1 else ('early' if w['window']==0 else 'late' if w['window']==len(wins)-1 else 'middle')})
                previous = norm, w['actors']
        conversation_values = safe_mean(values)
        for d, layer in enumerate(LAYERS):
            conversation_values[d, 9] = original['scores_only'][i, ci[layer + '.mean.mean']]
            np.testing.assert_allclose(conversation_values[d, :9], [original['individual_' + layer][i, names['individual_' + layer].index(layer+'.'+s+'.mean')] for s in SPECTRAL], atol=1e-8, rtol=1e-8)
        all_values.append(conversation_values)
        if (i+1) % 50 == 0:
            print(json.dumps({'extracted': i+1, 'base': base.name}), flush=True)
    # Verify every cross-fitted mixture's spectral mean against the previously audited feature bank.
    all_values = np.asarray(all_values)
    bank = np.load(weighted / 'feature-bank.npz')['features']
    bank_names = read(weighted / 'feature-names.json')
    by_key = {(r['framework'], r['class']): r['candidate'] for r in assignments}
    for i, item in enumerate(metadata):
        for j, c in enumerate(LABELS):
            k = by_key[item['framework'], c]
            expected = [bank[k, i, bank_names.index('average.'+s+'.mean')] for s in SPECTRAL]
            np.testing.assert_allclose(all_values[i, 10+j, :9], expected, atol=1e-8, rtol=1e-8)
    np.savez_compressed(out/'measurements.npz', conversations=all_values, windows=np.array(window_values), crossfit_weights=alphas)
    save(out/'measurement-index.json', {'graphs': GRAPHS, 'metrics': METRICS, 'conversations': metadata, 'windows': window_meta})
    save(out/'crossfit-weights.json', assignments)
    jsonl(out/'matrix-diagnostics.jsonl', diagnostics)
    pair_rows = [{'a': LAYERS[a], 'b': LAYERS[b], 'both_nonempty': pair_total[a,b],
                  'same_spectrum': pair_same[a,b], 'same_spectrum_different_adjacency': pair_different_raw[a,b],
                  'same_normalized_matrix': pair_same_matrix[a,b]} for a,b in itertools.combinations(range(9),2)]
    diagnostic_summary = {}
    for g in GRAPHS:
        rows = [r for r in diagnostics if r['graph']==g]
        nz = [r for r in rows if r['nonzero']]
        by_conversation = collections.defaultdict(list)
        for r in nz:
            by_conversation[r['conversation_id']].append(r['normalized_lambda2'] < 1e-8)
        diagnostic_summary[g] = {'windows': len(rows), 'nonempty': len(nz),
                                 'lambda2_zero_nonempty': sum(r['normalized_lambda2']<1e-8 for r in nz),
                                 'only_0_1_2_nonempty': sum(r['only_0_1_2'] for r in nz),
                                 'mean_conversation_fraction_lambda2_zero': np.mean([np.mean(v) for v in by_conversation.values()]) if nz else None}
    diag = finite_json({'layers': diagnostic_summary, 'pairs': pair_rows, 'mixture_class_pairs': mixture_pairs, 'examples': examples})
    save(out/'matrix-summary.json', diag)
    save(out/'integrity-audit.json', {'passed': True, 'original_graph_windows_recomputed': checked,
                                     'max_saved_matrix_error': max_error, 'crossfit_spectral_means_verified': len(metadata)*len(LABELS),
                                     'all_framework_and_task_exclusions_verified': True, 'no_classifier_training_or_API_calls': True})
    return metadata, all_values, np.array(window_values), window_meta, diag, assignments


def analyze(metadata, values, repetitions, seed):
    y = np.array([r['labels'] for r in metadata])
    clean, problem, unresolved = populations(y)
    framework = np.array([r['framework'] for r in metadata])
    lengths = np.array([r['events'] for r in metadata])
    length_bin = np.digitize(lengths, [10,20,50,100])
    strata = np.array([f'{f}|length-bin-{b}' for f,b in zip(framework, length_bin)])
    weights = cluster_weights([r['task_group'] for r in metadata], repetitions, seed)
    result, cooccurrence = [], []
    comparisons = [('any_problem', 'no_issue', problem, clean)]
    for j,c in enumerate(LABELS):
        comparisons += [(c,'no_issue',y[:,j]==1,clean), (c,'class_absent',y[:,j]==1,y[:,j]==0),
                        (c,'other_problems',y[:,j]==1,(y[:,j]==0)&problem)]
        for k,d in enumerate(LABELS):
            known = (y[:,j]>=0)&(y[:,k]>=0)
            both = ((y[:,j]==1)&(y[:,k]==1)&known).sum()
            union = (((y[:,j]==1)|(y[:,k]==1))&known).sum()
            cooccurrence.append({'class_a':c,'class_b':d,'both':int(both),'jaccard':float(both/union) if union else None})
    counts = []
    for target, reference, positive, negative in comparisons:
        counts.append({'class':target,'reference':reference,'positive':int(positive.sum()),'reference_n':int(negative.sum()),
                       'positive_median_events':float(np.median(lengths[positive])), 'reference_median_events':float(np.median(lengths[negative]))})
        graphs = range(10) if target=='any_problem' else [*range(10), GRAPHS.index('mixture_'+target)]
        for g in graphs:
            for m,metric in enumerate(METRICS):
                if g>=9 and metric in ('score_mean','coverage'):
                    continue
                x = values[:,g,m]
                full = contrast(x,positive,negative,framework,weights)
                matched = contrast(x,positive,negative,strata,weights[:1])
                result.append({'class':target,'reference':reference,'graph':GRAPHS[g],'metric':metric,
                               **full, 'length_adjusted_delta':matched['adjusted_delta'],
                               'length_strata':matched['strata'], 'length_overlap_positive':matched['overlap_positive'],
                               'length_overlap_reference':matched['overlap_reference']})
    return finite_json({'contrasts':result,'cooccurrence':cooccurrence,'counts':counts,
                        'population':{'no_issue':int(clean.sum()),'any_problem':int(problem.sum()),'unresolved':int(unresolved.sum()),
                                      'unknown_label_cells':int((y<0).sum()), 'task_groups':len(set(r['task_group'] for r in metadata)),
                                      'single_window':sum(r['windows']==1 for r in metadata),
                                      'framework_counts':[{ 'framework':f, 'clean':int((clean&(framework==f)).sum()),
                                                           'problem':int((problem&(framework==f)).sum())} for f in sorted(set(framework))]}})


def format_num(x, digits=2):
    return '—' if x is None or not np.isfinite(x) else f'{x:.{digits}f}'


def find_row(analysis,c,ref,g,m):
    return next(r for r in analysis['contrasts'] if (r['class'],r['reference'],r['graph'],r['metric'])==(c,ref,g,m))


def plot_reports(out, analysis, values, metadata, window_values, window_meta, assignments, diag):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10, 'axes.spines.top':False, 'axes.spines.right':False, 'savefig.facecolor':'white'})
    fig,axes = plt.subplots(1,3,figsize=(19,8),constrained_layout=True)
    for ax,metric in zip(axes,('combinatorial_mean','normalized_nonzero_fraction','aligned_matrix_drift')):
        arr = np.array([[find_row(analysis,c,'other_problems',d,metric)['adjusted_delta'] for d in LAYERS] for c in LABELS],float)
        im=ax.imshow(arr,vmin=-1,vmax=1,cmap='RdBu_r',aspect='auto')
        ax.set_xticks(range(9),[DISPLAY[d] for d in LAYERS],rotation=65,ha='right')
        ax.set_yticks(range(14),[f'{c} {n}' for c,n in zip(LABELS,LABEL_NAMES)] if ax is axes[0] else LABELS)
        ax.set_title(DISPLAY[metric])
        for i,j in np.ndindex(arr.shape):
            if np.isfinite(arr[i,j]):ax.text(j,i,f'{arr[i,j]:.1f}',ha='center',va='center',fontsize=8,color='white' if abs(arr[i,j])>.65 else '#182839')
    fig.colorbar(im,ax=axes,label='Within-framework Cliff delta: class present versus other problems',shrink=.7)
    fig.suptitle('Class-specific contrasts • windows averaged within each conversation',fontsize=17)
    fig.savefig(out/'class-layer-effects.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(2,3,figsize=(17,10),constrained_layout=True)
    for ax,metric in zip(axes.flat,KEY_METRICS):
        ds=LAYERS if metric in ('score_mean','coverage') else (*LAYERS,'average')
        rows=[find_row(analysis,'any_problem','no_issue',d,metric) for d in ds]
        delta=np.array([r['adjusted_delta'] if r['adjusted_delta'] is not None else np.nan for r in rows])
        low=np.array([r['interval_low'] if r['interval_low'] is not None else np.nan for r in rows])
        high=np.array([r['interval_high'] if r['interval_high'] is not None else np.nan for r in rows])
        yy=np.arange(len(ds))
        ax.hlines(yy,low,high,color='#475569',linewidth=1.5)
        ax.scatter(delta,yy,color='#0f766e',label='Within framework',s=24)
        ax.scatter([r['length_adjusted_delta'] for r in rows],yy,marker='x',color='#dc6a32',label='+ length bins',s=28)
        ax.axvline(0,color='#9ca3af',lw=.7)
        ax.set_yticks(yy,[DISPLAY[d] for d in ds]);ax.invert_yaxis();ax.set_xlim(-1,1);ax.set_title(DISPLAY[metric]);ax.set_xlabel('Cliff delta')
    axes[0,0].legend(fontsize=8,loc='upper center',bbox_to_anchor=(.5,1.18),ncol=2,frameon=False)
    fig.suptitle('Any problem versus no issue • 278 versus 20 conversations\nIntervals: 95% task-cluster Bayesian bootstrap, descriptive and unadjusted',fontsize=16)
    fig.savefig(out/'problem-vs-clean.png',dpi=160);plt.close(fig)
    # Use paired first-to-last changes only on traces with at least two windows.
    fig,axes=plt.subplots(3,5,figsize=(20,10),constrained_layout=True)
    y=np.array([r['labels'] for r in metadata]);clean,problem,_=populations(y)
    sequences=collections.defaultdict(list)
    for k,w in enumerate(window_meta):sequences[w['conversation_index']].append(k)
    for ax,c in zip(axes.flat,('any_problem',*LABELS)):
        positive=problem if c=='any_problem' else y[:,LABELS.index(c)]==1
        reference=clean if c=='any_problem' else (y[:,LABELS.index(c)]==0)&problem
        for mask,label,color in ((positive,'Present','#b45309'),(reference,'Reference','#087e8b')):
            rows=[ids for i,ids in sequences.items() if mask[i] and len(ids)>1]
            first=np.array([[window_values[ids[0],g,7] for g in (0,1,3,8)] for ids in rows])
            last=np.array([[window_values[ids[-1],g,7] for g in (0,1,3,8)] for ids in rows])
            if len(rows):
                ax.plot(range(4),np.median(last-first,axis=0),marker='o',color=color,label=f'{label} n={len(rows)}')
        ax.axhline(0,color='#9ca3af',lw=.7);ax.set_title(c);ax.set_xticks(range(4),['Relevance','Novelty','Verification','Fulfillment'],rotation=40,ha='right');ax.legend(fontsize=7)
    fig.suptitle('Temporal change in raw Laplacian intensity: last window minus first\nPaired change per conversation; single-window traces excluded',fontsize=16)
    fig.savefig(out/'temporal-change.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(16,7),constrained_layout=True)
    arr=np.zeros((14,14))
    for r in analysis['cooccurrence']:arr[LABELS.index(r['class_a']),LABELS.index(r['class_b'])]=r['jaccard'] or 0
    im=axes[0].imshow(arr,vmin=0,vmax=1,cmap='Blues');axes[0].set_xticks(range(14),LABELS,rotation=50);axes[0].set_yticks(range(14),LABELS);axes[0].set_title('Label co-occurrence (Jaccard)');fig.colorbar(im,ax=axes[0],shrink=.7)
    arr=np.full((9,9),np.nan)
    for r in diag['pairs']:
        a,b=LAYERS.index(r['a']),LAYERS.index(r['b'])
        if r['both_nonempty']:arr[a,b]=arr[b,a]=r['same_spectrum']/r['both_nonempty']
    im=axes[1].imshow(arr,vmin=0,vmax=1,cmap='Oranges');axes[1].set_xticks(range(9),[DISPLAY[d] for d in LAYERS],rotation=65,ha='right');axes[1].set_yticks(range(9),[DISPLAY[d] for d in LAYERS]);axes[1].set_title('Identical normalized spectra\nFraction of windows where both layers are nonempty');fig.colorbar(im,ax=axes[1],shrink=.7)
    fig.savefig(out/'cooccurrence-and-redundancy.png',dpi=160);plt.close(fig)


def table(headers, rows):
    return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+html.escape(str(h))+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in r)+'</tr>' for r in rows)+'</tbody></table></div>'


def image_html(path):
    return '<img src="data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()+'" alt="'+html.escape(path.stem)+'">'


def render_report(root, runs):
    a=runs[0]['analysis'];b=runs[1]['analysis'];out=runs[0]['out']
    body=['<header><p class="eyebrow">SWARMLENS · TRACE SCORE GRAPHS · EXPLORATORY ANALYSIS</p><h1>What distinguishes the MAST classes?</h1><p>Each score layer, each windowed Laplacian, and the class-dependent mixtures — compared at the conversation level.</p></header>']
    body+=['<section><h2>What we learned</h2><p><b>Requirement fulfillment is the clearest problem-versus-clean distinction.</b> Its raw Laplacian intensity is lower with a within-framework Cliff delta of −0.54; the framework × length-bin comparison is −0.53. That contrast repeats with 10-event windows (−0.55 and −0.53). Most other layers weaken substantially after accounting for length.</p><p><b>The classes do not all have the same profile.</b> Repetition and failure to recognize termination show the clearest broad differences; task derailment has consistently lower relevance. Verification failures are substantially harder to separate with these measurements. Several apparently large differences disappear in comparable-length traces.</p><p><b>Normalization discards much of the difference between score graphs.</b> 53.4% of nonempty layer-pair windows have the same normalized spectrum at 20 events, rising to 67.8% at 10 events, almost always despite different adjacencies. The second eigenvalue of the equal graph’s lifted normalized Laplacian is zero in every nonempty window. These specific spectral quantities are therefore poor standalone discriminators here; raw intensity, score values, availability and temporal behavior must be inspected separately.</p><p><b>Learned mixtures have not established an overall improvement.</b> Their earlier framework-held-out macro AP gains remain small and within the reported uncertainty. The observations below motivate which measurements deserve further testing; they are not a validation of a general detector.</p></section>']
    body+=['<section><h2>Scope and interpretation</h2><p><b>300 distinct conversations: 278 with at least one consensus issue, 20 with all 14 issue labels absent, and 2 unresolved.</b> Class labels overlap. A conversation can belong to several classes. These are MAST reference labels, not independently established ground truth about cascades.</p><p>The primary analysis uses 20-event windows, stride 10; the sensitivity run uses 10-event windows, stride 5. All normalized events count, including tools. Windows are averaged within a conversation before comparisons, so long traces do not become extra independent samples.</p><p><b>Controls are scarce and shorter:</b> median 7 events versus 32 for problem traces. Effects are shown pooled, within frameworks, and within framework × length bins (1–9, 10–19, 20–49, 50–99, 100+ events). The length comparison covers only overlapping strata; it cannot remove all length or task confounding.</p></section>']
    body+=['<section><h2>How to read the comparisons</h2><p>Cliff’s delta is P(a problem/class-present conversation has a larger value) − P(it has a smaller value), with ties contributing zero. Values range from −1 to +1. “Within framework” averages the deltas equally over frameworks containing both groups. Positive does not mean better; it means more of that particular measured quantity.</p><ul><li><b>Laplacian intensity:</b> trace(L)/(2 × actor count), sensitive to edge weights and graph density.</li><li><b>Normalized rank fraction:</b> fraction of nonzero normalized eigenvalues. It describes the lifted graph, not failure severity.</li><li><b>Aligned matrix drift:</b> normalized-Laplacian change between adjacent windows, matching actor identities and separate input/output copies. It can include changing actor presence and missing-score support. Single-window traces have no drift estimate.</li><li><b>Source score and coverage:</b> semantic score and availability diagnostics, kept separate from the spectral measurements.</li></ul><p>Intervals are 95% Bayesian-bootstrap ranges using 400 exponential weights on exact-task clusters, shared across conversations with the same task. They condition on this selected dataset and the saved models; they are exploratory, not adjusted for the many comparisons, and do not establish significance or causation. Length sensitivity is descriptive only.</p></section>']
    body+=['<section><h2>Problem versus no problem</h2>',image_html(out/'problem-vs-clean.png')]
    rows=[]
    for d in LAYERS:
        r=find_row(a,'any_problem','no_issue',d,'combinatorial_mean');s=find_row(b,'any_problem','no_issue',d,'combinatorial_mean')
        rows.append([DISPLAY[d],format_num(r['positive_median']),format_num(r['reference_median']),format_num(r['pooled_delta']),format_num(r['adjusted_delta']),format_num(r['length_adjusted_delta']),f"{r['length_overlap_positive']} / {r['length_overlap_reference']}",format_num(s['adjusted_delta'])])
    body += [table(['Layer','Problem median intensity','Clean median intensity','Pooled δ','Within-framework δ','+ length bins δ','Length overlap problem / clean','10-event δ'],rows),'<p>Changing the adjustment can change the sign. This is evidence of confounding or limited common support, not a reason to select the version with the strongest separation.</p></section>']
    body+=['<section><h2>Differences between classes</h2><p>These heatmaps compare a class with <b>other problem conversations where that class is known absent</b>. This asks whether a pattern is class-specific, rather than merely different from the 20 clean traces. Unknown label cells are excluded separately for each class.</p>',image_html(out/'class-layer-effects.png'),'</section>']
    body+=['<section><h2>Interpretation for all 14 classes</h2><p>These are descriptive readings of the full tables, including sensitivity checks. A distinctive group average is not sufficient to classify individual conversations reliably.</p>',table(['Class','Observed pattern and qualification'],[[f'{c} {html.escape(n)}','<span style="display:block;text-align:left">'+html.escape(CLASS_NOTES[c])+'</span>'] for c,n in zip(LABELS,LABEL_NAMES)]),'</section>']
    weighted20=runs[0]['weighted_results']['evaluations']['leave_framework_out']['methods']
    weighted10=runs[1]['weighted_results']['evaluations']['leave_framework_out']['methods']
    body+=['<section><h2>Class-dependent combinations</h2><p>For every conversation, the mixture weights come from the saved model trained without its entire framework and without exact task matches. No full-data refit weights enter these contrasts. Weights vary by held-out framework; they are predictive parameters, not importance or causal shares. No new classifier is fitted here. AP values below are the earlier held-out results, reproduced for context.</p>']
    rows=[]
    for c,n in zip(LABELS,LABEL_NAMES):
        r=find_row(a,c,'other_problems','mixture_'+c,'combinatorial_mean')
        base=weighted20['average_graph']['per_label'][c];new=weighted20['class_weighted_graph']['per_label'][c]
        small=weighted10['class_weighted_graph']['per_label'][c]
        rows.append([f'{c} {html.escape(n)}',f"{new['positive']} / {new['known']}",format_num(new['positive']/new['known'],3),format_num(base['ap'],3),format_num(new['ap'],3),format_num(small['ap'],3),format_num(r['adjusted_delta']),format_num(r['length_adjusted_delta'])])
    body += [table(['Class','Positive / known','Prevalence','Equal AP (20)','Learned AP (20)','Learned AP (10)','Mixture intensity δ vs other problems','+ length bins δ'],rows),'<p>AP must be interpreted against each class’s prevalence. A high AP for a common label does not make it intrinsically easier to identify than a rare label. Overall macro AP remains 0.433 → 0.437 for 20 events and 0.446 → 0.451 for 10; the previously computed paired intervals include zero.</p></section>']
    body += ['<section><h2>Every class, every score layer</h2><p>Expand a class for all nine layers, the equal combination, and its held-out learned combination. The displayed deltas compare Laplacian intensity. The downloadable JSON/CSV also contains every spectral descriptor, source score, coverage, and matrix drift for all three reference groups. “Other” excludes the 20 clean traces.</p>']
    for j,(c,n) in enumerate(zip(LABELS,LABEL_NAMES)):
        counts=next(r for r in a['counts'] if r['class']==c and r['reference']=='other_problems')
        peers=sorted((r for r in a['cooccurrence'] if r['class_a']==c and r['class_b']!=c),key=lambda r:r['jaccard'] or 0,reverse=True)[:2]
        body += [f'<details id="class-{c}"><summary>{c} · {html.escape(n)} <span>{counts["positive"]} present; {counts["reference_n"]} other problems</span></summary><p>Strongest label overlap: '+', '.join(f"{p['class_b']} (Jaccard {p['jaccard']:.2f}, {p['both']} shared positives)" for p in peers)+'.</p>']
        rows=[]
        for d in (*LAYERS,'average','mixture_'+c):
            clean=find_row(a,c,'no_issue',d,'combinatorial_mean');absent=find_row(a,c,'class_absent',d,'combinatorial_mean');other=find_row(a,c,'other_problems',d,'combinatorial_mean');small=find_row(b,c,'other_problems',d,'combinatorial_mean')
            rows.append([DISPLAY.get(d,'Class-specific mixture'),format_num(clean['adjusted_delta']),format_num(absent['adjusted_delta']),format_num(other['adjusted_delta']),f"[{format_num(other['interval_low'])}, {format_num(other['interval_high'])}]",format_num(other['length_adjusted_delta']),f"{other['length_overlap_positive']} / {other['length_overlap_reference']}",len(other['strata']),format_num(small['adjusted_delta'])])
        body += [table(['Layer / combination','vs clean δ','vs class absent δ','vs other problems δ','95% descriptive interval','+ length bins δ','Length overlap + / −','Frameworks','10-event vs other δ'],rows)]
        rows=[]
        for d in LAYERS:
            rr=[r for r in runs[0]['assignments'] if r['class']==c];weights=np.array([r['weights'][LAYERS.index(d)] for r in rr])
            rows.append([DISPLAY[d],format_num(weights.mean(),3),format_num(weights.min(),3),format_num(weights.max(),3)])
        body += ['<p>Mixture-weight variation across the seven held-out-framework models:</p>',table(['Score layer','Mean weight','Minimum','Maximum'],rows),'</details>']
    body+=['</section><section><h2>Temporal changes</h2><p>Each point below is a median of within-conversation last-minus-first changes. It uses the same multi-window conversations at both endpoints. This avoids pretending that a short, one-window conversation has a trajectory. At 20 events, '+str(a['population']['single_window'])+' conversations have only one window.</p>',image_html(out/'temporal-change.png'),'</section>']
    body+=['<section><h2>What the normalization is losing</h2><p>We recalculated every original saved Laplacian and its eigenvalues, and also rebuilt each held-out class mixture. Different adjacencies often have exactly the same normalized spectrum. This is a property of the current actor projection and bipartite lift: for example, the normalized Laplacian of an isolated positive-weight edge has eigenvalues 0 and 2 regardless of its positive weight.</p>',image_html(out/'cooccurrence-and-redundancy.png')]
    rows=[]
    for run in runs:
        pairs=run['diag']['pairs'];total=sum(r['both_nonempty'] for r in pairs);same=sum(r['same_spectrum'] for r in pairs);different=sum(r['same_spectrum_different_adjacency'] for r in pairs)
        layer=run['diag']['layers']['average']
        rows.append([run['window'],total,f'{same/total:.1%}',f'{different/total:.1%}',f"{layer['lambda2_zero_nonempty']}/{layer['nonempty']}",f"{layer['only_0_1_2_nonempty']}/{layer['nonempty']}"])
    body += [table(['Window size','Both-nonempty layer-pair windows','Same normalized spectrum','Same spectrum, different adjacency','Equal-graph λ₂ = 0, nonempty windows','Equal graph only eigenvalues 0/1/2'],rows),'<p>These are descriptive counts of overlapping graph windows, not independent statistical samples. λ₂ refers to the <b>2N-node signed bipartite lift</b>; it must not be interpreted as ordinary N-agent network connectivity or as evidence of a cascade. Raw Laplacian intensities retain scale differences that normalization can remove. Spectral equality also does not prove equality of the underlying matrices.</p>']
    rows=[]
    for run in runs:
        m=run['diag']['mixture_class_pairs']
        rows.append([run['window'],m['both_nonempty'],f"{m['same_spectrum']/m['both_nonempty']:.1%}",f"{m['same_adjacency']/m['both_nonempty']:.1%}"])
    body += ['<p>The same limitation affects the learned class combinations. Comparing each pair of class-specific mixtures on the <em>same</em> conversation window:</p>',table(['Window size','Nonempty class-pair windows','Identical normalized spectrum','Identical adjacency'],rows),'<p>Different class weights need not produce different normalized spectra. A large harmfulness weight also need not represent harmful behavior: most conversation-level harmfulness scores are near zero, and mixtures are renormalized by score availability. Learned weights are not semantic explanations.</p>']
    example=runs[0]['diag']['examples'][0] if runs[0]['diag']['examples'] else None
    if example:
        body += ['<details><summary>Concrete measured example: different score graphs, same spectrum</summary><pre>'+html.escape(json.dumps(example,indent=2))+'</pre></details>']
    body+=['</section><section><h2>Limits and reproducibility</h2><ul><li>This is a selected, multilabel, class-enriched cohort. It cannot estimate deployment prevalence or population-wide accuracy.</li><li>Only 20 conversations are clean, with just one clean trace each in OpenManus and AppWorld. Task clustering cannot create evidence absent from these controls.</li><li>Class co-occurrence, framework, task and length can produce shared graph signatures. No class-specific causal attribution is established.</li><li>Graph edges represent recorded or candidate source-response relations; some exposure is unconfirmed. The current graph construction is not proof of influence.</li><li>Missing scores remain missing measurements. Zero graph weights and missing-score support can both lead to isolated lifted vertices; coverage is therefore reported separately.</li><li>Exploratory contrasts inspect previously used evaluation data. Mixture intervals condition on saved weights; no independent validation claim is made.</li></ul><p>Analysis code: <code>src/trace_score_graphs/class_contrasts.py</code>. Raw measurements, aligned drift, all contrasts, weights, source hashes and audits are retained per run.</p><ul>']
    for run in runs:
        rel=run['out'].relative_to(root)
        for name in ('analysis.json','contrasts.csv','matrix-summary.json','integrity-audit.json','protocol.json','source-hashes.json','measurement-index.json','measurements.npz'):
            body.append(f'<li><a href="{rel}/{name}">{run["window"]}-event: {name}</a></li>')
    body+=['</ul><p>Method references: <a href="https://people.eecs.berkeley.edu/~jordan/sail/readings/rubin.pdf">Rubin, The Bayesian Bootstrap (1981)</a>; <a href="https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.mannwhitneyu.html">rank comparisons and ties</a>. All numeric findings above come from our saved artifacts.</p></section>']
    css='body{margin:0;background:#eef3f6;color:#172c3b;font:16px/1.65 system-ui,sans-serif}main{max-width:1480px;margin:auto;padding:32px}header,section{background:white;border:1px solid #dce4e9;border-radius:16px;padding:28px 32px;margin-bottom:24px}h1{font-size:42px;line-height:1.15}h2{font-size:26px}h1,h2,h3{letter-spacing:-.025em}.eyebrow{font-size:12px;letter-spacing:.12em;color:#087e8b}img{display:block;width:100%;height:auto;margin:24px 0}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin:18px 0}td,th{text-align:right;padding:10px;border-bottom:1px solid #e0e8ed}td:first-child,th:first-child{text-align:left;min-width:180px}th{background:#f0f5f7}details{border:1px solid #dce4e9;border-radius:10px;padding:14px 18px;margin:12px 0}summary{cursor:pointer;font-weight:650}summary span{font-size:12px;color:#637687;margin-left:12px}a{color:#087e8b}pre{overflow:auto;font-size:12px}code{font-size:13px}@media(max-width:800px){main{padding:12px}header,section{padding:20px}h1{font-size:30px}}'
    (root/'class-contrast-report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MAST class contrasts · SwarmLens</title><style>'+css+'</style><main>'+''.join(body)+'</main></html>')


def run(root, base_id, weighted_id, repetitions, seed):
    base=root/'data/classification'/base_id;weighted=root/'data/weighted-classification'/weighted_id
    sources={str(p.relative_to(root)):file_sha(p) for p in [Path(__file__),root/'src/trace_score_graphs/classification.py',root/'src/trace_score_graphs/weighted_classification.py',base/'metadata.json',base/'splits.json',base/'feature-matrices.npz',base/'feature-names.json',weighted/'feature-bank.npz',weighted/'results.json']}
    for p in sorted((base/'matrices').glob('*.npz')):sources[str(p.relative_to(root))]=file_sha(p)
    for p in sorted((base/'windows').glob('*.json')):sources[str(p.relative_to(root))]=file_sha(p)
    for p in sorted((weighted/'models/leave_framework_out').glob('*/*.json')):sources[str(p.relative_to(root))]=file_sha(p)
    protocol={'version':VERSION,'base':base_id,'weighted':weighted_id,'bootstrap_repetitions':repetitions,'seed':seed,
              'unit':'Conversation; average window descriptors, never treat overlapping windows as independent.',
              'targets':'Consensus label cells; clean=all14 zero; any problem=at least one1; remaining unresolved excluded from binary contrast.',
              'references':['all14labels absent','targetclass absent among all known','targetclass absent among other problems'],
              'effects':'Cliff delta, round to10decimals for numerical ties; equal-framework mean; length sensitivity equal framework×bin strata with both groups.',
              'length_bins':[10,20,50,100],'bootstrap':'400 exponential weights on task clusters (Bayesian bootstrap), condition on observed cohort and fitted weights; no multiplicity adjustment.',
              'mixtures':'Each conversation uses weights trained without its entire framework; matching task groups excluded. Never final-model weights.',
              'drift':'Align actor OUT/IN identities over union across adjacent windows; Frobenius delta / sqrt(2*union actor count); single window unavailable.',
              'source_score':'Conversation-only mean of existing per-window paired score means; not reconstructed in the window measurement axis. Mixtures have no single source score or coverage.',
              'no_new_API_requests':True,'graphs':GRAPHS,'metrics':METRICS}
    identity=digest({'protocol':protocol,'sources':sources})[:16]
    out=root/'data/class-contrasts'/identity;out.mkdir(parents=True,exist_ok=True)
    save(out/'protocol.json',protocol);save(out/'source-hashes.json',sources)
    metadata,values,window_values,window_meta,diag,assignments=extract(base,weighted,out)
    analysis=analyze(metadata,values,repetitions,seed)
    save(out/'analysis.json',analysis)
    columns=[k for k in analysis['contrasts'][0] if k not in ('strata','length_strata')]
    with (out/'contrasts.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows({k:r[k] for k in columns} for r in analysis['contrasts'])
    plot_reports(out,analysis,values,metadata,window_values,window_meta,assignments,diag)
    save(out/'manifest.json',{'complete':True,'experiment_id':identity,'base':base_id,'weighted':weighted_id,'source_hash':digest(sources),'contrasts':len(analysis['contrasts'])})
    return {'out':out,'analysis':analysis,'diag':diag,'window':read(base/'protocol.json')['window_events'], 'assignments':assignments,'weighted_results':read(weighted/'results.json')}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path.cwd());parser.add_argument('--seed',type=int,default=20261004);args=parser.parse_args()
    runs=[run(args.root.resolve(),'68d5eb7653a9641c','4a1aa10569287700',400,args.seed),run(args.root.resolve(),'eab6e9778df715d4','573b62a4c8b91197',400,args.seed)]
    render_report(args.root.resolve(),runs)
    save(args.root/'class-contrast-report.json',{'version':VERSION,'runs':[{'window':r['window'],'directory':str(r['out'].relative_to(args.root.resolve())),'population':r['analysis']['population']} for r in runs]})
    print(json.dumps({'complete':True,'report':str(args.root/'class-contrast-report.html'),'runs':[str(r['out']) for r in runs]}),flush=True)


if __name__=='__main__':
    main()
