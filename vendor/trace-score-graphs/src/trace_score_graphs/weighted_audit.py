"""Audit nested graph-weight selection, models and class-weighted features."""
import argparse
import collections
from pathlib import Path

import numpy as np
from scipy.special import expit
from sklearn.metrics import average_precision_score

from .common import file_sha, read, save
from .classification import LABELS, LAYERS, SPECTRAL, signed_laplacians, spectrum_features, summarize
from .weighted_classification import concentration, mix_graphs, choose_candidate


def reconstruct(x, model):
    if 'constant' in model:
        return np.full(len(x), model['constant'])
    filled = np.where(np.isfinite(x), x, model['imputation_median'])
    extended = np.concatenate([filled, ~np.isfinite(x)], axis=1)
    z = (extended - model['scaler_mean']) / model['scaler_scale']
    z = np.zeros((len(x), 1)) if model['constant_design'] else z[:, model['kept_features']]
    return expit(z @ model['coef'] + model['intercept'])


def audit(out, base):
    out, base = Path(out), Path(base)
    result = read(out / 'results.json')
    config = read(out / 'protocol.json')
    assert result['complete'] and result['base_experiment'] == base.name
    for relative, expected in read(out / 'source-hashes.json').items():
        assert file_sha(base / relative) == expected, relative
    assert read(out / 'splits.json') == read(base / 'splits.json')
    meta = read(base / 'metadata.json')['conversations']
    y = np.array([m['labels'] for m in meta])
    cache = np.load(out / 'feature-bank.npz')
    bank, weights = cache['features'], cache['weights']
    np.testing.assert_array_equal(weights, config['candidates'])
    assert bank.shape[:2] == (65, 300)
    assert (weights >= 0).all()
    np.testing.assert_allclose(weights.sum(1), 1)
    original = np.load(base / 'feature-matrices.npz')['average_graph']
    np.testing.assert_array_equal(bank[0], original)
    folds = read(out / 'splits.json')['protocols']
    checks = collections.Counter(source_files=len(read(out / 'source-hashes.json')), conversations=len(meta))
    selected_ids = set()
    cases = [(p, f['name'], f['train'], f['test'], out/'models'/p/f['name']) for p, fs in folds.items() for f in fs]
    cases.append(('final_refit', 'all_data', list(range(300)), [], out / 'final-models'))
    for protocol_name, name, train, test, directory in cases:
        assert not set(train) & set(test)
        assert not {meta[i]['task_group'] for i in train} & {meta[i]['task_group'] for i in test}
        inner = read(directory / 'inner-splits.json')
        seen = []
        for split in inner:
            a, b = split['train'], split['validation']
            assert set(a) | set(b) == set(train)
            assert not set(a) & set(b)
            assert not {meta[i]['task_group'] for i in a} & {meta[i]['task_group'] for i in b}
            assert not (set(a) | set(b)) & set(test)
            seen.extend(b)
            checks['inner_folds'] += 1
        assert sorted(seen) == sorted(train)
        outer = None if protocol_name == 'final_refit' else np.load(out / ('predictions-' + protocol_name + '.npz'))['probabilities']
        for j, label in enumerate(LABELS):
            saved = read(directory / (label + '.json'))
            choice, model = saved['selection'], saved['model']
            k = choice['candidate']; selected_ids.add(k)
            np.testing.assert_array_equal(choice['weights'], weights[k])
            inner_file = np.load(directory / (label + '-inner.npz'))
            np.testing.assert_array_equal(inner_file['training_indices'], train)
            probabilities = inner_file['probabilities']
            target = y[train, j]
            known = target >= 0
            assert model['training_known'] == int(known.sum())
            assert model['training_positive'] == int(target[known].sum())
            if choice['inner_ap'][0] is not None:
                assert np.isfinite(probabilities).all()
                ap = np.array([average_precision_score(target[known], p[known]) for p in probabilities])
                np.testing.assert_allclose(ap, choice['inner_ap'], atol=1e-12)
                chosen, objective = choose_candidate(ap, weights, config['weight_penalty'])
                assert chosen == k
                np.testing.assert_allclose(objective, choice['objective'])
                assert objective[k] >= objective[0] - 1e-12
                checks['inner_candidate_scores'] += len(ap)
            else:
                assert k == 0
            if test:
                pred = reconstruct(bank[k, test], model)
                np.testing.assert_allclose(pred, outer[test, j], atol=1e-10)
                checks['outer_predictions_reconstructed'] += len(test)
            checks['class_models'] += 1
        checks['outer_or_refit_folds'] += 1
    # Recompute every selected mixture on a fixed, label-blind representative set
    # using the scalar reference implementation, not the batched feature builder.
    names = read(out / 'feature-names.json')
    selected_conversations = list(range(0, len(meta), 10))
    for i in selected_conversations:
        cid = meta[i]['conversation_id']
        wins = read(base / 'windows' / (cid + '.json'))
        with np.load(base / 'matrices' / (cid + '.npz')) as matrices:
            arrays = []
            for w in wins:
                prefix = f'w{w["window"]:04d}_'
                a = np.stack([matrices[prefix + layer + '_adjacency'] for layer in LAYERS])
                mask = np.stack([matrices[prefix + layer + '_observed_counts'] > 0 for layer in LAYERS])
                arrays.append((a, mask))
            for k in sorted(selected_ids):
                sequence = []
                for a, mask in arrays:
                    mixed, denominator = mix_graphs(a, mask, weights[k])
                    assert np.all(mixed[denominator == 0] == 0)
                    lap, norm = signed_laplacians(mixed)
                    sequence.append(spectrum_features(lap, norm))
                    checks['scalar_laplacian_pairs'] += 1
                expected = summarize(sequence, SPECTRAL, 'average')
                for column, key in enumerate(names):
                    value = expected[key] if key in expected else original[i, column]
                    value = np.nan if value is None else value
                    np.testing.assert_allclose(bank[k, i, column], value, equal_nan=True, atol=1e-8, rtol=1e-8)
                    checks['selected_feature_values_checked'] += 1
    report = {'passed': True, 'checks': dict(checks),
              'all_outer_models_and_inner_selections_audited': True,
              'feature_recomputation_sample': {'conversations': selected_conversations, 'selected_candidates': sorted(selected_ids)},
              'audit_code_sha256': file_sha(__file__), 'errors': []}
    save(out / 'weighted-audit.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('experiment'); p.add_argument('base_experiment')
    args = p.parse_args()
    print(__import__('json').dumps(audit(args.experiment, args.base_experiment), indent=2))


if __name__ == '__main__':
    main()
