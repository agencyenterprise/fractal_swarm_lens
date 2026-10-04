"""Independent checks of event preservation, kernels, splits and saved predictions."""
import argparse
import json
from pathlib import Path
import numpy as np
from sklearn.metrics import average_precision_score

from . import event_graphs as g
from .event_experiments import TARGETS,targets,replay_kernel
from .common import read,save,file_sha
from .weighted_classification import choose_candidate


def independent_laplacian(semantic,chronology,continuity):
    n=len(semantic);b=np.zeros((2*n,2*n))
    def edge(i,j,weight):
        b[i,j]+=weight;b[j,i]+=weight
    for i in range(n):
        edge(i,i+n,1+semantic[i,i])
        for j in range(i+1,n):
            if chronology[i,j] or continuity[i,j]:
                edge(i,j,1);edge(i+n,j+n,1)
    for i,j in zip(*np.nonzero(semantic)):
        if i!=j:edge(i,n+j,semantic[i,j])
    d=np.abs(b).sum(axis=1);l=np.diag(d)-b
    inv=1/np.sqrt(d)
    return l,inv[:,None]*l*inv[None,:]


def audit(out,root):
    out=Path(out);root=Path(root).resolve();meta=read(out/'metadata.json')['conversations'];y=targets(meta)
    source=next((root/'data/runs').glob('ad8a7238f9d58fff'))
    for p,h in read(out/'source-hashes.json').items():assert file_sha(root/p)==h,p
    dimensions=read(out/'graph-preservation-audit.json');width=dimensions['event_index_width'];maxw=dimensions['max_window_count']
    total_nodes,total_pairs,checked_matrices=0,0,0;rows=[]
    for i,m in enumerate(meta):
        cid=m['conversation_id'];conv,rr=g.load_conversation(source,cid);saved=read(out/'event-graphs'/(cid+'.json'))
        assert saved['nodes']==conv['events']
        assert {(x['source_event'],x['response_event'],x['record_id']) for x in rr['uptake']}=={(x['source_event'],x['response_event'],x['record_id']) for x in saved['interaction_links']}
        wins=g.load_windows(out/'windows'/cid)
        assert {k for w in wins for k in w['pair_ids']}=={r['record_id'] for r in rr['uptake']}
        assert {e for w in wins for e,active in zip(w['event_ids'],w['active']) if active}=={e['id'] for e in conv['events']}
        lookup={d:{r['record_id']:r for r in records} for d,records in rr.items()}
        node_lookup={d:{r['source_event']:r for r in records} for d,records in rr.items() if g.SPECS[d][0]=='message'}
        for w in wins:
            n=len(w['event_ids']);ix={k:j for j,k in enumerate(w['event_ids'])}
            for d,layer in enumerate(g.LAYERS):
                expected=np.zeros((3,n,n));mask=np.zeros_like(expected,dtype=bool)
                if g.SPECS[layer][0]=='message':
                    for j,eid in enumerate(w['event_ids']):
                        r=node_lookup[layer][eid]
                        if r['status']=='scorable' and r['weight'] is not None:expected[:,j,j]=r['weight'];mask[:,j,j]=True
                else:
                    for rid in w['pair_ids']:
                        r=lookup[layer][rid];assert r['available_at']<=w['end']
                        if r['status']=='scorable' and r['weight'] is not None:
                            q=g.channel(r['delivery_basis']);a,b=ix[r['source_event']],ix[r['response_event']]
                            expected[q,a,b]=r['weight'];mask[q,a,b]=True
                np.testing.assert_array_equal(w['values'][:,d],expected);np.testing.assert_array_equal(w['observed'][:,d],mask)
            # Independently build every equal-weight Laplacian from explicit edge contributions.
            average=g.mix(w['values'],w['observed'],np.ones(9)/9)
            for q in range(3):
                a,b=independent_laplacian(average[q],w['chronology'],w['continuity']);aa,bb=g.laplacians(average[q],w['chronology'],w['continuity'])
                np.testing.assert_allclose(a,aa,atol=1e-10);np.testing.assert_allclose(b,bb,atol=1e-10)
                assert np.linalg.eigvalsh(a).min()>-1e-8 and np.linalg.eigvalsh(b).max()<2+1e-8
                checked_matrices+=1
        total_nodes+=len(conv['events']);total_pairs+=len(rr['uptake'])
        if i%20==0:rows.append((i,g.graph_vector(wins,width,alpha=np.ones(9)/9),g.mask_vector(wins,width),g.graph_vector(wins,width,topology=True)))
    # Independently reconstruct selected Gram entries with dictionaries of individual matrix coordinates.
    kernels=np.load(out/'kernels.npz');bankfile=np.load(out/'mixture-kernels.npz');bank=bankfile['kernels'];weights=bankfile['weights']
    np.testing.assert_array_equal(kernels['average_graph'],bank[0])
    for i,a,mask,t in rows:
        for j,b,mb,tb in rows:
            val=0.
            for x,z in ((a,b),(mask,mb),(t,tb)):
                xd=dict(zip(*x));zd=dict(zip(*z));val+=sum(v*zd.get(k,0) for k,v in xd.items())
            np.testing.assert_allclose(kernels['average_graph'][i,j],val,atol=1e-8,rtol=1e-8)
    for k in bank:
        np.testing.assert_allclose(k,k.T,atol=1e-8)
        assert np.linalg.eigvalsh(k).min()>-1e-7
    splits=read(out/'splits.json');old=read(root/'data/classification/68d5eb7653a9641c/splits.json')
    assert splits==old,'Outer splits changed'
    checked_predictions=0;checked_selections=0
    for protocol,folds in splits['protocols'].items():
        saved=np.load(out/('predictions-'+protocol+'.npz'))
        for fold in folds:
            train,test=fold['train'],fold['test'];directory=out/'models'/protocol/fold['name']
            assert not {meta[i]['task_group'] for i in train}&{meta[i]['task_group'] for i in test}
            if protocol=='leave_framework_out':assert not {meta[i]['framework'] for i in train}&{meta[i]['framework'] for i in test}
            for method in kernels.files:
                models=read(directory/(method+'.json'))
                for j,label in enumerate(TARGETS):
                    np.testing.assert_allclose(replay_kernel(kernels[method],models[label],test),saved[method][test,j],atol=1e-9)
                    checked_predictions+=len(test)
            inner=read(directory/'inner-splits.json')
            for f in inner:
                assert set(f['train'])|set(f['validation'])<=set(train)
                assert not {meta[i]['task_group'] for i in f['train']}&{meta[i]['task_group'] for i in f['validation']}
            for j,label in enumerate(TARGETS):
                f=read(directory/'mixtures'/(label+'.json'));choice=f['selection'];data=np.load(directory/'mixtures'/(label+'-inner.npz'));ids=data['training_indices']
                np.testing.assert_array_equal(ids,train);known=y[ids,j]>=0;yy=y[ids,j][known]
                aps=[average_precision_score(yy,v[known]) for v in data['probabilities']] if 0<yy.sum()<len(yy) else np.zeros(len(weights))
                np.testing.assert_allclose(aps,choice['inner_ap'],atol=1e-12);candidate,_=choose_candidate(aps,weights,.05);assert candidate==choice['candidate']
                np.testing.assert_allclose(replay_kernel(bank[candidate],f['model'],test),saved['class_weighted_graph'][test,j],atol=1e-9)
                checked_predictions+=len(test);checked_selections+=1
    result={'passed':True,'events_checked':total_nodes,'unique_pairs_checked':total_pairs,'equal_window_channel_laplacians_independently_rebuilt':checked_matrices,
            'gram_entries_rebuilt':len(rows)**2,'outer_prediction_cells_reconstructed':checked_predictions,'nested_selections_recomputed':checked_selections,
            'all_source_hashes_unchanged':True,'all_outer_splits_unchanged':True,'audit_code_sha256':file_sha(__file__)}
    save(out/'independent-audit.json',result);print(json.dumps(result),flush=True)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('--root',type=Path,default=Path.cwd());a=p.parse_args();audit(a.run,a.root)


if __name__=='__main__':main()
