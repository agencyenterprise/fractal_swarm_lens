import unittest
import numpy as np

from trace_score_graphs.classification import mean_graph, signed_laplacians, spectrum_features
from trace_score_graphs.weighted_classification import (
    candidates, concentration, mix_graphs, batched_spectrum, inner_splits,
    choose_candidate, select_mixture,
)


class WeightedClassificationTests(unittest.TestCase):
    def test_candidates_are_deterministic_simplex_weights(self):
        w = candidates(42)
        self.assertEqual(w.shape, (65, 9))
        self.assertTrue((w >= 0).all())
        np.testing.assert_allclose(w.sum(1), 1)
        np.testing.assert_array_equal(w, candidates(42))
        self.assertAlmostEqual(concentration(w[0]), 0)
        self.assertAlmostEqual(concentration(np.eye(9)[0]), 1)

    def test_uniform_mixture_matches_existing_mean(self):
        rng = np.random.default_rng(4)
        a, masks = rng.uniform(-1,1,(9,3,3)), rng.random((9,3,3)) > .3
        old, _ = mean_graph(a, masks)
        actual, _ = mix_graphs(a, masks, np.ones(9)/9)
        np.testing.assert_allclose(actual, old)

    def test_missingness_renormalizes_without_losing_observed_zero(self):
        a, mask = np.zeros((9,2,2)), np.zeros((9,2,2), dtype=bool)
        a[0,0,1] = .8
        mask[0,0,1] = True
        w = np.zeros(9); w[:2] = [.2,.8]
        mixed, denom = mix_graphs(a, mask, w)
        self.assertAlmostEqual(mixed[0,1], .8)
        mask[1,0,1] = True  # This is a measured zero, not missing.
        mixed, denom = mix_graphs(a, mask, w)
        self.assertAlmostEqual(mixed[0,1], .16)
        self.assertEqual(denom[0,0], 0)
        self.assertEqual(mixed[0,0], 0)

    def test_one_hot_uses_only_selected_layer(self):
        rng = np.random.default_rng(9)
        a = rng.random((4,9,3,3))
        mask = np.ones_like(a, dtype=bool)
        mixed, _ = mix_graphs(a, mask, np.eye(9)[5])
        np.testing.assert_allclose(mixed, a[:,5])
        with self.assertRaises(ValueError):
            mix_graphs(a, mask, np.ones(9))

    def test_batched_signed_laplacians_match_scalar_reference(self):
        rng = np.random.default_rng(5)
        a = rng.uniform(-1,1,(7,4,4)); a[0] = 0
        expected = np.array([spectrum_features(*signed_laplacians(v)) for v in a])
        np.testing.assert_allclose(batched_spectrum(a), expected, atol=1e-12)

    def test_mixing_before_laplacian_differs_from_mixing_laplacians(self):
        a = np.zeros((9,3,3)); masks = np.ones_like(a,dtype=bool)
        a[0] = [[0,1,0],[0,0,0],[0,0,0]]
        a[1] = [[0,.1,0],[0,0,1],[0,0,0]]
        w = np.zeros(9); w[:2] = [.5,.5]
        mixed, _ = mix_graphs(a, masks, w)
        norm = signed_laplacians(mixed)[1]
        average_norm = (signed_laplacians(a[0])[1] + signed_laplacians(a[1])[1])/2
        self.assertFalse(np.allclose(norm, average_norm))

    def test_equal_score_or_small_gain_prefers_uniform(self):
        w = candidates(12)
        ap = np.full(65, .5)
        self.assertEqual(choose_candidate(ap,w,.05)[0],0)
        ap[28] = .51  # one-hot improvement smaller than concentration penalty.
        self.assertEqual(choose_candidate(ap,w,.05)[0],0)

    def test_weight_selection_uses_no_outer_test_labels(self):
        rng = np.random.default_rng(4)
        y = np.array([0,1]*18)
        train, test = list(range(30)), list(range(30,36))
        groups = [f'g{i//2}' for i in range(36)]
        folds = inner_splits(train,groups,42)
        for fold in folds:
            self.assertFalse(set(fold['train']) & set(test))
            self.assertFalse(set(fold['validation']) & set(test))
            self.assertFalse({groups[i] for i in fold['train']} & {groups[i] for i in fold['validation']})
        bank = rng.normal(size=(3,36,2))
        bank[1,:,0] = y * 3
        weights = np.stack([np.ones(9)/9,np.eye(9)[0],np.eye(9)[1]])
        selected, before = select_mixture(bank,y,train,folds,weights,.05,42)
        changed = y.copy(); changed[test] = 1-changed[test]
        repeated, after = select_mixture(bank,changed,train,folds,weights,.05,42)
        self.assertEqual(selected['candidate'],1)
        self.assertEqual(selected,repeated)
        np.testing.assert_array_equal(before,after)


if __name__ == '__main__':
    unittest.main()
