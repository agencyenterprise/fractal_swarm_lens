import unittest
import numpy as np
from sklearn.metrics import average_precision_score

from trace_score_graphs.classification import (
    LABELS, LAYERS, build_features, consensus_labels, fast_macro_ap, make_splits,
    mean_graph, preprocess_fold, signed_laplacians, spectrum_features, windows,
)
from trace_score_graphs.rubrics import SPECS


class ClassificationTests(unittest.TestCase):
    def test_windows_cover_final_event_without_tiny_tail(self):
        self.assertEqual(windows(3, 20, 10), [(0, 2)])
        self.assertEqual(windows(25, 20, 10), [(0, 19), (5, 24)])
        self.assertEqual(windows(40, 20, 10), [(0, 19), (10, 29), (20, 39)])

    def test_signed_laplacian_psd_direction_and_scaling(self):
        a = np.array([[0., -.4, .2], [.9, 0, 0], [0, .6, 0]])
        lap, norm = signed_laplacians(a)
        self.assertTrue(np.allclose(lap, lap.T))
        self.assertGreaterEqual(np.linalg.eigvalsh(lap).min(), -1e-10)
        self.assertLessEqual(np.linalg.eigvalsh(norm).max(), 2 + 1e-10)
        self.assertEqual(lap[0, 4], .4)  # Negative directed edge retained.
        other, norm_other = signed_laplacians(a * .1)
        np.testing.assert_allclose(norm, norm_other, atol=1e-12)
        np.testing.assert_allclose(other, lap * .1)
        self.assertAlmostEqual(spectrum_features(other, norm_other)[-1], spectrum_features(lap, norm)[-1] * .1)

    def test_isolated_vertices_have_zero_laplacian(self):
        lap, norm = signed_laplacians(np.zeros((2, 2)))
        self.assertEqual(np.count_nonzero(lap), 0)
        self.assertEqual(np.count_nonzero(norm), 0)
        self.assertTrue(np.isfinite(spectrum_features(lap, norm)).all())

    def test_graph_average_missing_is_not_measured_zero(self):
        a = np.array([[[.8, 0], [0, 0]], [[0, 0], [0, 0]]])
        masks = np.array([[[True, False], [False, False]], [[False, False], [False, False]]])
        avg, counts = mean_graph(a, masks)
        self.assertEqual(avg[0, 0], .8)
        masks[1, 0, 0] = True
        avg, counts = mean_graph(a, masks)
        self.assertEqual(avg[0, 0], .4)
        self.assertEqual(counts[0, 0], 2)

    def test_label_disagreement_is_masked_per_cell(self):
        first = dict.fromkeys(LABELS, 0)
        second = dict(first)
        second['2.4'] = 1
        first['3.3'] = second['3.3'] = 1
        item = {'all_source_annotations': [{'labels': first}, {'labels': second}]}
        y = dict(zip(LABELS, consensus_labels(item)))
        self.assertEqual(y['2.4'], -1)
        self.assertEqual(y['3.3'], 1)
        self.assertEqual(y['1.1'], 0)

    def test_grouped_and_framework_splits_purge_shared_tasks(self):
        groups = ['same', 'g1', 'g2', 'g3', 'g4', 'same', 'g5', 'g6', 'g7', 'g8']
        frameworks = ['A'] * 5 + ['B'] * 5
        splits = make_splits(groups, frameworks, 42)
        for folds in splits.values():
            for fold in folds:
                self.assertFalse({groups[i] for i in fold['train']} & {groups[i] for i in fold['test']})
        lofo_a = splits['leave_framework_out'][0]
        self.assertIn(5, lofo_a['purged'])
        self.assertNotIn(5, lofo_a['train'])

    def test_preprocessing_fits_only_training_values(self):
        train = np.array([[1., np.nan], [3., np.nan], [2., np.nan]])
        test = np.array([[10000., 20.]])
        a, b, median, scaler, keep = preprocess_fold(train, test)
        np.testing.assert_allclose(median, [2., 0.])
        self.assertEqual(scaler.mean_[0], 2.)
        self.assertTrue(np.isfinite(a).all() and np.isfinite(b).all())

    def test_fast_ap_matches_sklearn_with_ties_and_unknowns(self):
        y = np.array([[1, 0], [0, 1], [1, -1], [0, 1], [-1, 0]])
        p = np.array([[.8, .2], [.8, .9], [.1, .3], [.1, .2], [.7, .1]])
        expected = np.mean([average_precision_score(y[y[:, j] >= 0, j], p[y[:, j] >= 0, j]) for j in range(2)])
        self.assertAlmostEqual(fast_macro_ap(y, p, np.arange(len(y))), expected)

    def test_window_excludes_future_response_and_unifies_layer_support(self):
        events = [{'id': f'e{i}', 'position': i, 'actor': a, 'kind': 'message'}
                  for i, a in enumerate(['A', 'B', 'A', 'C'])]
        rows = {}
        for layer in LAYERS:
            interaction = SPECS[layer][0] == 'interaction'
            rows[layer] = [{'source_event': 'e0', 'response_event': 'e3' if interaction else None,
                            'source_position': 0, 'available_at': 3 if interaction else 0,
                            'status': 'scorable', 'weight': .8, 'delivery_basis': 'explicit_header'}]
        features, wins, arrays, _ = build_features({'events': events}, rows, 3, 1)
        self.assertEqual(wins[0]['pairs'], 0)
        self.assertNotIn('C', wins[0]['actors'])
        self.assertEqual(wins[1]['pairs'], 0)  # source is outside trailing window.
        features, wins, arrays, _ = build_features({'events': events}, rows, 4, 2)
        self.assertEqual(wins[0]['pairs'], 1)
        for layer in LAYERS:
            self.assertAlmostEqual(arrays[f'w0000_{layer}_adjacency'][0, 2], .8)
        self.assertIn('scores_plus_laplacians', features)


if __name__ == '__main__':
    unittest.main()
