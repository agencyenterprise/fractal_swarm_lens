import copy
import unittest
from trace_score_graphs.evidence import records
from trace_score_graphs.scoring_audit import expanded, alternate, assert_state, reference_check, comparison

class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.conv=dict(conversation_id='test',schema_version='test',actors=['a','b'],task='Do the task',coverage={'flags':[]},
            events=[dict(id=f'e{i:05d}',position=i,actor='a' if i%2==0 else 'b',kind='message',recipients=[],content=f'Message {i}: '+'x'*600) for i in range(9)])
        self.packet=next(p for p in records(self.conv) if p['scope']=='interaction' and p['source_position']==6)
    def test_expansion_keeps_focal_payloads_and_excludes_future(self):
        before=copy.deepcopy(self.packet);state,meta=expanded(self.conv,self.packet,[])
        self.assertEqual(before,self.packet)
        self.assertEqual(state['source'],before['state']['source'])
        self.assertEqual(state['response'],before['state']['response'])
        self.assertEqual(len(state['history']),6)
        self.assertTrue(all(not x['content']['truncated'] for x in state['history']))
        self.assertTrue(meta['complete_prior_context']);assert_state(state,6,7)
    def test_alternative_keeps_source_and_reconstructs_intervening(self):
        p=alternate(self.conv,self.packet,8)
        self.assertEqual(p['state']['source'],self.packet['state']['source'])
        self.assertEqual(p['state']['response']['position'],8)
        self.assertEqual([x['position'] for x in p['state']['intervening_events']],[7])
        assert_state(p['state'],6,8)
    def test_evaluation_is_rejected(self):
        s=copy.deepcopy(self.packet['state']);s['response']['content']['text']='result\nEvaluation\n{"success": false}'
        with self.assertRaises(AssertionError):assert_state(s,6,7)
    def test_future_history_is_rejected(self):
        s=copy.deepcopy(self.packet['state']);s['history'][0]['position']=8
        with self.assertRaises(AssertionError):assert_state(s,6,7)
    def test_missingness_is_not_numeric_zero(self):
        a={'x':{'raw':0,'status':'insufficient_evidence'}};b={'x':{'raw':4,'status':'scorable'}}
        c=comparison(a,b)
        self.assertEqual(c['both_scorable'],0);self.assertEqual(c['status_changes'],1);self.assertEqual(c['material_numeric_changes'],0)
    def test_reference_margin_and_status(self):
        refs={'x':{'status':'scorable','range':[0,1]}}
        self.assertTrue(reference_check({'x':{'raw':1.5,'status':'scorable'}},refs)['x']['compatible'])
        self.assertFalse(reference_check({'x':{'raw':1.51,'status':'scorable'}},refs)['x']['compatible'])
        self.assertFalse(reference_check({'x':{'raw':0,'status':'not_applicable'}},refs)['x']['compatible'])

if __name__=='__main__':unittest.main()
