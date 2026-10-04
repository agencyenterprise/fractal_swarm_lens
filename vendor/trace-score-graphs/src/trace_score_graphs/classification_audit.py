"""Verify saved classification artifacts, splits and prediction reconstruction."""
import argparse
import collections
import json
from pathlib import Path

import numpy as np
from scipy.special import expit

from .common import file_sha, read, save
from .classification import LABELS, LAYERS, consensus_labels


def audit_experiment(out, data):
    out, data = Path(out), Path(data)
    results = read(out / 'results.json')
    assert results['complete']
    for path, expected in read(out / 'source-hashes.json').items():
        assert file_sha(data / path) == expected, f'Source changed: {path}'
    meta = read(out / 'metadata.json')['conversations']
    ids = [m['conversation_id'] for m in meta]
    selection = {m['conversation_id']: m for m in read(data / 'selection.json')['conversations']}
    assert len(ids) == len(set(ids)) == len(selection) == 300
    assert set(ids) == set(selection)
    assert all(m['labels'] == consensus_labels(selection[m['conversation_id']]) for m in meta)
    splits = read(out / 'splits.json')
    assert splits['conversation_order'] == ids
    features = np.load(out / 'feature-matrices.npz')
    y = features['labels']
    np.testing.assert_array_equal(y, [m['labels'] for m in meta])
    checks = collections.Counter(conversations=len(ids), source_files=len(read(out / 'source-hashes.json')))
    for split_name, folds in splits['protocols'].items():
        predictions = np.load(out / ('predictions-' + split_name + '.npz'))
        seen = []
        for fold in folds:
            train, test = fold['train'], fold['test']
            assert not set(train) & set(test)
            assert not {meta[i]['task_group'] for i in train} & {meta[i]['task_group'] for i in test}
            if split_name == 'leave_framework_out':
                assert {meta[i]['framework'] for i in test} == {fold['name']}
                assert fold['name'] not in {meta[i]['framework'] for i in train}
            seen.extend(test)
            checks['folds'] += 1
            for method in features.files:
                if method == 'labels':
                    continue
                x = features[method][test]
                models = read(out / 'models' / split_name / fold['name'] / (method + '.json'))
                reconstructed = np.zeros((len(test), len(LABELS)))
                for j, label in enumerate(LABELS):
                    model = models[label]
                    target = y[train, j]
                    target = target[target >= 0]
                    assert model['training_known'] == len(target)
                    assert model['training_positive'] == int(target.sum())
                    if 'constant' in model:
                        reconstructed[:, j] = model['constant']
                    else:
                        filled = np.where(np.isfinite(x), x, model['imputation_median'])
                        extended = np.concatenate([filled, ~np.isfinite(x)], axis=1)
                        standardized = (extended - model['scaler_mean']) / model['scaler_scale']
                        z = np.zeros((len(test), 1)) if model['constant_design'] else standardized[:, model['kept_features']]
                        reconstructed[:, j] = expit(z @ np.array(model['coef']) + model['intercept'])
                    checks['label_models'] += 1
                np.testing.assert_allclose(reconstructed, predictions[method][test], atol=1e-10)
                checks['reconstructed_predictions'] += reconstructed.size
        assert sorted(seen) == list(range(len(ids)))
        expected = np.mean([predictions['individual_' + layer] for layer in LAYERS], axis=0)
        np.testing.assert_allclose(predictions['prediction_average'], expected)
        for line, m in zip((out / ('predictions-' + split_name + '.jsonl')).read_text().splitlines(), meta):
            record = json.loads(line)
            assert record['conversation_id'] == m['conversation_id']
            assert len(record['predictions']) == len(predictions.files) - 1
    for m in meta:
        cid = m['conversation_id']
        wins = read(out / 'windows' / (cid + '.json'))
        assert len(wins) == m['windows']
        assert wins[-1]['end_position'] == m['events'] - 1
        with np.load(out / 'matrices' / (cid + '.npz')) as matrices:
            assert len(matrices.files) == len(wins) * 10 * 4
            for win in wins:
                for layer in (*LAYERS, 'average'):
                    prefix = f'w{win["window"]:04d}_{layer}'
                    a = matrices[prefix + '_adjacency']
                    lap = matrices[prefix + '_laplacian']
                    norm = matrices[prefix + '_normalized_laplacian']
                    n = len(win['actors'])
                    assert a.shape == (n, n) and lap.shape == norm.shape == (2*n, 2*n)
                    assert np.isfinite(a).all() and np.isfinite(lap).all() and np.isfinite(norm).all()
                    np.testing.assert_allclose(lap, lap.T)
                    np.testing.assert_allclose(norm, norm.T)
                    np.testing.assert_allclose(lap[:n, n:], -a)
                    np.testing.assert_allclose(lap[n:, :n], -a.T)
                    np.testing.assert_allclose(np.diag(lap), np.r_[np.abs(a).sum(1), np.abs(a).sum(0)])
                    eig = np.linalg.eigvalsh(norm)
                    assert eig.min() >= -1e-8 and eig.max() <= 2 + 1e-8
                    assert np.linalg.eigvalsh(lap).min() >= -1e-8
                    counts = matrices[prefix + ('_observed_layers' if layer == 'average' else '_observed_counts')]
                    assert np.all(a[counts == 0] == 0)
                    checks['laplacian_pairs'] += 1
                checks['windows'] += 1
    assert checks['laplacian_pairs'] == results['totals']['laplacian_pairs']
    report = {'passed': True, 'checks': dict(checks), 'masked_label_cells': int((y < 0).sum()), 'errors': []}
    save(out / 'classification-audit.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('experiment')
    p.add_argument('--data', default='data')
    args = p.parse_args()
    print(json.dumps(audit_experiment(args.experiment, args.data), indent=2))


if __name__ == '__main__':
    main()
