import unittest
import numpy as np

from trace_score_graphs.class_contrasts import aligned_drift, weighted_delta, contrast, populations, cluster_weights, safe_mean
from trace_score_graphs.classification import signed_laplacians


class ContrastTests(unittest.TestCase):
    def test_rank_direction_ties_and_missing(self):
        x=np.array([1.,2.,np.nan,0.,1.])
        pos=np.array([1,1,1,0,0],bool);ref=~pos
        self.assertAlmostEqual(weighted_delta(x,pos,ref,np.ones((1,5)))[0],.75)
        self.assertAlmostEqual(weighted_delta(x,ref,pos,np.ones((1,5)))[0],-.75)

    def test_stratification_resolves_simpson_confound(self):
        # In each framework positive values exceed reference; pooled values reverse.
        x=np.array([1]*9+[11]+[0]+[10]*9,dtype=float)
        p=np.array([1]*10+[0]*10,bool);r=~p
        strata=np.array(['a']*9+['b']+['a']+['b']*9)
        result=contrast(x,p,r,strata,np.ones((2,20)))
        self.assertLess(result['pooled_delta'],0)
        self.assertEqual(result['adjusted_delta'],1)

    def test_alignment_is_actor_permutation_invariant(self):
        _,a=signed_laplacians(np.array([[0,.2],[.7,0]]))
        perm=[1,0,3,2]
        b=a[np.ix_(perm,perm)]
        self.assertAlmostEqual(aligned_drift(a,['A','B'],b,['B','A']),0)

    def test_actor_arrival_is_aligned(self):
        _,a=signed_laplacians(np.array([[0,.2],[0,0]]))
        _,b=signed_laplacians(np.array([[0,0,.2],[0,0,0],[0,0,0]]))
        self.assertAlmostEqual(aligned_drift(a,['A','B'],b,['A','C','B']),0)

    def test_single_window_has_no_drift(self):
        vals=np.array([[[1.,np.nan]]])
        self.assertTrue(np.isnan(safe_mean(vals)[0,1]))

    def test_unresolved_is_neither_clean_nor_problem(self):
        clean,problem,unclear=populations([[0,0],[1,-1],[0,-1]])
        np.testing.assert_array_equal(clean,[1,0,0])
        np.testing.assert_array_equal(problem,[0,1,0])
        np.testing.assert_array_equal(unclear,[0,0,1])

    def test_cluster_members_share_bootstrap_weight(self):
        w=cluster_weights(['a','b','a'],40,42)
        np.testing.assert_array_equal(w[:,0],w[:,2])
        self.assertTrue((w>0).all())
        self.assertFalse(np.allclose(w[1:,0],w[1:,1]))

    def test_normalization_erases_isolated_edge_scale(self):
        a,an=signed_laplacians(np.array([[0,.2],[0,0]]))
        b,bn=signed_laplacians(np.array([[0,.8],[0,0]]))
        self.assertFalse(np.allclose(a,b))
        np.testing.assert_allclose(an,bn)

    def test_empty_overlap_remains_unavailable(self):
        result=contrast(np.array([1.,2.]),np.array([1,0],bool),np.array([0,1],bool),np.array(['a','b']),np.ones((1,2)))
        self.assertTrue(np.isnan(result['adjusted_delta']))
        self.assertEqual(result['overlap_positive'],0)


if __name__=='__main__':
    unittest.main()
