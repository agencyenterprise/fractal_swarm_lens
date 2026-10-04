import copy
import unittest
import numpy as np
from trace_score_graphs.event_graphs import event_windows,laplacians,graph_vector,mask_vector,mix,unique_records,channel
from trace_score_graphs.event_experiments import fit_kernel,replay_kernel,targets
from trace_score_graphs.rubrics import LAYERS,SPECS


def fixture():
    events=[{'id':f'e{i}','position':i,'actor':['A','B'][i%2],'kind':'message','recipients':[],'content':str(i)} for i in range(6)]
    conv={'conversation_id':'test','events':events}
    rows={}
    for d in LAYERS:
        message=SPECS[d][0]=='message'
        pairs=[(i,None) for i in range(6)] if message else [(0,2),(0,4),(2,5)]
        rows[d]=[{'record_id':f'{"m" if message else "p"}{s}-{t}','source_event':f'e{s}','response_event':f'e{t}' if t is not None else None,
                  'source_position':s,'available_at':s if t is None else t,'weight':(.2 if t!=4 else .8),'status':'scorable',
                  'delivery_basis':None if message else 'explicit_header' if t==2 else 'temporal_candidate_unconfirmed_exposure'} for s,t in pairs]
    return conv,rows


class EventGraphTests(unittest.TestCase):
    def test_events_and_cross_boundary_links_not_merged(self):
        c,r=fixture();w=event_windows(c,r,3,3)
        self.assertEqual(w[1]['context_start'],0)
        self.assertEqual(w[1]['event_ids'],[f'e{i}' for i in range(6)])
        self.assertIn('p0-4',w[1]['pair_ids'])
        d=LAYERS.index('uptake');self.assertEqual(w[1]['values'][2,d,0,4],.8)
        self.assertEqual(w[0]['values'][0,d,0,2],.2)
        self.assertEqual(len(set(k for z in w for k in z['pair_ids'])),3)

    def test_one_to_many_uses_exact_pair_id(self):
        c,r=fixture();w=event_windows(c,r,6,6)[0];d=LAYERS.index('agreement')
        self.assertEqual(w['values'][0,d,0,2],.2)
        self.assertEqual(w['values'][2,d,0,4],.8)

    def test_unpaired_node_scores_retained(self):
        c,r=fixture();w=event_windows(c,r,6,6)[0];d=LAYERS.index('task_relevance')
        self.assertEqual(w['values'][0,d,5,5],.2)
        self.assertTrue(w['observed'][0,d,5,5])

    def test_future_never_enters_window(self):
        c,r=fixture();w=event_windows(c,r,3,3)[0]
        self.assertNotIn('p0-4',w['pair_ids']);self.assertEqual(max(w['positions']),2)

    def test_missing_is_not_observed_zero(self):
        c,r=fixture();r['uptake'][0]['weight']=0
        a=event_windows(c,r,6,6);r['uptake'][0].update(weight=None,status='insufficient_evidence');b=event_windows(c,r,6,6)
        d=LAYERS.index('uptake');np.testing.assert_array_equal(a[0]['values'],b[0]['values'])
        self.assertNotEqual(len(mask_vector(a,6,d)[0]),len(mask_vector(b,6,d)[0]))

    def test_chronology_cannot_be_cancelled_by_disagreement(self):
        c,r=fixture();w=event_windows(c,r,6,6)[0];a=np.zeros((6,6));a[0,1]=-1
        raw,norm=laplacians(a,w['chronology'],w['continuity'])
        self.assertEqual(raw[0,1],-1)
        self.assertEqual(raw[0,7],1)
        self.assertGreaterEqual(np.linalg.eigvalsh(raw).min(),-1e-9)

    def test_full_matrix_features_preserve_direction(self):
        c,r=fixture();w=event_windows(c,r,6,6)[0];a=np.zeros((6,6));a[0,4]=.7
        x,_=laplacians(a,w['chronology'],w['continuity']);z,_=laplacians(a.T,w['chronology'],w['continuity'])
        self.assertFalse(np.allclose(x,z))

    def test_window_order_not_pooled(self):
        c,r=fixture();w=event_windows(c,r,3,3);before=graph_vector(w,6,layer=0)
        changed=copy.deepcopy(w);changed[0]['index']=1;changed[1]['index']=0
        after=graph_vector(changed,6,layer=0)
        a=dict(zip(*before));b=dict(zip(*after));self.assertNotEqual(a,b)

    def test_recorded_recipient_not_merged_with_unknown(self):
        self.assertNotEqual(channel('explicit_header'),channel('temporal_candidate_unconfirmed_exposure'))
        self.assertNotEqual(channel('phase_roles'),channel('explicit_header'))

    def test_dedup_exact_only(self):
        c,r=fixture();rows=r['uptake'];self.assertEqual(len(unique_records(rows+rows[:1])),3)
        broken=copy.deepcopy(rows[0]);broken['weight']=.1
        with self.assertRaises(ValueError):unique_records(rows+[broken])

    def test_mixture_uniform_and_zero_preserves_mask_semantics(self):
        c,r=fixture();w=event_windows(c,r,6,6)[0]
        got=mix(w['values'],w['observed'],np.ones(9)/9)
        self.assertAlmostEqual(got[0,0,0],.2)
        self.assertAlmostEqual(got[2,0,4],.8)

    def test_kernel_test_values_do_not_affect_training(self):
        x=np.array([[0,1],[1,0],[2,0],[0,2],[1,1.]],float);k=x@x.T;y=np.array([0,1,1,0,1])
        p,m=fit_kernel(k,y,[0,1,2,3],[4],True)
        np.testing.assert_allclose(p,replay_kernel(k,m,[4]))
        k[4,4]=1000;p2,m2=fit_kernel(k,y,[0,1,2,3],[4],True)
        self.assertEqual(m,m2);np.testing.assert_allclose(p,p2)

    def test_unknown_binary_is_not_clean(self):
        y=targets([{'labels':[0]*14},{'labels':[-1]+[0]*13},{'labels':[1,-1]+[0]*12}])
        np.testing.assert_array_equal(y[:,-1],[0,-1,1])


if __name__=='__main__':unittest.main()
