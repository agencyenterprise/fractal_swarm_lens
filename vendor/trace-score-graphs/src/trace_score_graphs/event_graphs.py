"""Lossless event/link storage and ordered full-matrix features for the rerun.

Chronology, actor continuity, recipient metadata and candidate response links
are distinct relations. No response heuristic is promoted to observed delivery.
"""
import collections
import json
from pathlib import Path

import numpy as np
from scipy import sparse

from .classification import LABELS, LABEL_NAMES, consensus_labels, task_group, windows
from .common import read, save, file_sha, jsonl
from .rubrics import LAYERS, SPECS

VERSION = 'event-directed-temporal-v2'
CHANNELS = ('recorded_recipient_candidate', 'workflow_candidate', 'unaddressed_candidate')
EXPLICIT = {'explicit_header', 'explicit_recipient', 'logged_tool_selection', 'tool_result'}
WORKFLOW = {'phase_roles', 'inferred_two_party_transcript', 'workflow_plan'}


def channel(basis):
    if basis in EXPLICIT:
        return 0
    if basis in WORKFLOW:
        return 1
    if basis == 'temporal_candidate_unconfirmed_exposure':
        return 2
    raise ValueError(f'Unrecognized link provenance: {basis}')


def unique_records(rows):
    result = {}
    for row in rows:
        key = row['record_id']
        if key in result and result[key] != row:
            raise ValueError('Conflicting records under one evidence ID')
        result[key] = row
    return list(result.values())


def load_conversation(source, cid):
    conv = read(source/'schemas'/(cid+'.json'))
    rows = {d: unique_records([json.loads(s) for s in (source/'scores'/d/(cid+'.jsonl')).read_text().splitlines()]) for d in LAYERS}
    return conv, rows


def event_windows(conv, rows, size, stride):
    events = conv['events']; em = {e['id']:e for e in events}
    assert [e['position'] for e in events] == list(range(len(events)))
    pairs = rows['uptake']
    lookup = {d:{r['record_id']:r for r in rr} for d,rr in rows.items()}
    node_lookup = {d:{r['source_event']:r for r in rr} for d,rr in rows.items() if SPECS[d][0]=='message'}
    for d in LAYERS:
        if SPECS[d][0]=='interaction':
            assert set(lookup[d]) == set(lookup['uptake'])
    result=[]
    for wi,(start,end) in enumerate(windows(len(events),size,stride)):
        # Keep cross-boundary source endpoints as context. Never include future targets.
        eligible=[p for p in pairs if start <= p['available_at'] <= end]
        lo=min([start]+[p['source_position'] for p in eligible])
        current=events[lo:end+1]; n=len(current); ix={e['id']:i for i,e in enumerate(current)}
        values=np.zeros((3,9,n,n)); masks=np.zeros_like(values,dtype=bool)
        chronology=np.zeros((n,n)); continuity=np.zeros((n,n));last={}
        for j,e in enumerate(current):
            if j:chronology[j-1,j]=1
            if e['actor'] in last:continuity[last[e['actor']],j]=1
            last[e['actor']]=j
        for d,layer in enumerate(LAYERS):
            if SPECS[layer][0]=='message':
                for j,e in enumerate(current):
                    r=node_lookup[layer][e['id']]
                    if r['status']=='scorable' and r['weight'] is not None:
                        values[:,d,j,j]=r['weight'];masks[:,d,j,j]=True
            else:
                for pair in eligible:
                    r=lookup[layer][pair['record_id']]
                    assert (r['source_event'],r['response_event'])==(pair['source_event'],pair['response_event'])
                    s,t=ix[r['source_event']],ix[r['response_event']]
                    assert s<t and em[r['response_event']]['position']==r['available_at']
                    q=channel(r['delivery_basis'])
                    if masks[q,d,s,t]:
                        raise ValueError('Distinct measurements for the same scored event pair require an additional edge slot; never average silently')
                    if r['status']=='scorable' and r['weight'] is not None:
                        values[q,d,s,t]=r['weight'];masks[q,d,s,t]=True
        topology=np.zeros((3,n,n))
        for r in eligible:topology[channel(r['delivery_basis']),ix[r['source_event']],ix[r['response_event']]]=1
        result.append({'index':wi,'start':start,'end':end,'context_start':lo,'event_ids':[e['id'] for e in current],
                       'positions':[e['position'] for e in current], 'actors':[e['actor'] for e in current],
                       'active':[e['position']>=start for e in current], 'pair_ids':[r['record_id'] for r in eligible],
                       'values':values,'observed':masks,'chronology':chronology,'continuity':continuity,'topology':topology})
    assert set(r['record_id'] for r in pairs)==set(k for w in result for k in w['pair_ids'])
    assert set(em)==set(e for w in result for e,active in zip(w['event_ids'],w['active']) if active)
    return result


def mix(values, observed, alpha):
    alpha=np.asarray(alpha,dtype=float)
    if alpha.shape!=(9,) or np.any(alpha<0) or not np.isclose(alpha.sum(),1):raise ValueError('Invalid mixture')
    denominator=(observed*alpha[None,:,None,None]).sum(axis=1)
    numerator=(values*observed*alpha[None,:,None,None]).sum(axis=1)
    return np.divide(numerator,denominator,out=np.zeros_like(numerator),where=denominator>0)


def laplacians(semantic, chronology, continuity):
    """OUT/IN event copies, explicit chronology backbone, signed semantic links.

The within-copy temporal matrix and the cross-copy semantic matrix never share
entries, so disagreement cannot cancel chronology. The fixed identity bridge
joins copies of the SAME event; it is not an observed communication edge.
Message measurements decorate this bridge; pair scores decorate directed links.
"""
    a=np.asarray(semantic,dtype=float);n=len(a)
    t=np.maximum(chronology,continuity);t=t+t.T
    cross=np.eye(n)+a
    b=np.block([[t,cross],[cross.T,t]])
    degree=np.abs(b).sum(axis=1);raw=np.diag(degree)-b
    inv=np.divide(1,np.sqrt(degree),out=np.zeros_like(degree),where=degree>0)
    norm=inv[:,None]*raw*inv[None,:]
    return raw,norm


def padded_laplacians(w, semantic, width):
    n=len(w['event_ids'])
    if n>width:raise ValueError('Window padding would truncate context')
    offset=width-n;ids=np.r_[np.arange(n)+offset,np.arange(n)+offset+width]
    output=np.zeros((3,2,2*width,2*width))
    for q in range(3):
        for k,a in enumerate(laplacians(semantic[q],w['chronology'],w['continuity'])):
            output[q,k][np.ix_(ids,ids)]=a
    return output


def graph_vector(wins, width, alpha=None, layer=None, topology=False):
    """Concatenate EVERY matrix entry in EVERY ordered window; no pooling."""
    cols,vals=[],[];block=3*2*(2*width)**2
    for w in wins:
        semantic=w['topology'] if topology else w['values'][:,layer] if layer is not None else mix(w['values'],w['observed'],alpha)
        a=padded_laplacians(w,semantic,width).ravel()
        nz=np.flatnonzero(a)
        cols.extend((w['index']*block+nz).tolist());vals.extend((a[nz]/(2*width)).tolist())
    return np.array(cols,dtype=np.int32),np.array(vals)


def mask_vector(wins,width,layer=None):
    # Shape and per-layer availability preserved separately from a measured zero.
    cols,vals=[],[];block=3*9*width*width+width
    for w in wins:
        n=len(w['event_ids']);offset=width-n
        mask=np.zeros((3,9,width,width)); ds=range(9) if layer is None else [layer]
        for d in ds:mask[:,d,offset:,offset:]=w['observed'][:,d]
        a=np.r_[mask.ravel(),np.r_[np.zeros(offset),np.ones(n)]]
        nz=np.flatnonzero(a);cols.extend(w['index']*block+nz);vals.extend(a[nz]/width)
    return np.asarray(cols,dtype=np.int32),np.asarray(vals)


def score_vector(conv,rows,capacity):
    # All scores in evidence-availability order, no graph endpoints in the features.
    cols,vals=[],[];slot=6
    for d,layer in enumerate(LAYERS):
        ordered=sorted(rows[layer],key=lambda r:(r['available_at'],r['source_position'],r['record_id']))
        for k,r in enumerate(ordered):
            if k>=capacity:raise ValueError('Score sequence truncation')
            valid=r['status']=='scorable' and r['weight'] is not None
            q=channel(r['delivery_basis']) if SPECS[layer][0]=='interaction' else -1
            x=[r['weight'] if valid else 0,float(valid),1.,r['available_at']/capacity,(q+1)/3,float(r['status']=='not_applicable')]
            for j,v in enumerate(x):
                if v:cols.append((d*capacity+k)*slot+j);vals.append(v/np.sqrt(capacity))
    return np.asarray(cols,dtype=np.int32),np.asarray(vals)


def csr_vectors(vectors,size):
    indices=np.concatenate([v[0] for v in vectors]);data=np.concatenate([v[1] for v in vectors]);indptr=np.r_[0,np.cumsum([len(v[0]) for v in vectors])]
    return sparse.csr_matrix((data,indices,indptr),shape=(len(vectors),size))


def gram(vectors,size):
    x=csr_vectors(vectors,size)
    return (x@x.T).toarray()


def load_windows(path):
    records=read(path.with_suffix('.json'))
    with np.load(path.with_suffix('.npz')) as arrays:
        for w in records:
            for k in ('values','observed','chronology','continuity','topology'):w[k]=arrays[f'w{w["index"]:04d}_{k}']
    return records


def prepare(root,source,out,size,stride):
    selection=read(root/'data/selection.json')['conversations']
    metadata=[];collection=[];source_rows=[];totals=collections.Counter();pair_audit=[]
    graphdir=out/'event-graphs';graphdir.mkdir(parents=True,exist_ok=True)
    windowdir=out/'windows';windowdir.mkdir(exist_ok=True)
    for item in selection:
        cid=item['conversation_id'];conv,rows=load_conversation(source,cid);wins=event_windows(conv,rows,size,stride)
        events=conv['events'];pairs=rows['uptake'];collection.append(wins);source_rows.append((conv,rows))
        labels=consensus_labels(item)
        metadata.append({'conversation_id':cid,'framework':conv['adapter'],'source_framework':item['framework'],
                         'task_group':task_group(conv),'labels':labels,'disputed_annotations':item['disputed_annotations'],
                         'cohort':item['cohort'],'events':len(events),'windows':len(wins),'coverage_flags':conv['coverage']['flags']})
        seen={};same=[]
        for e in events:
            if e['actor'] in seen:same.append({'source':seen[e['actor']],'target':e['id'],'kind':'same_actor_next','observed_communication':False})
            seen[e['actor']]=e['id']
        minimal=lambda r:{k:r[k] for k in ('record_id','source_event','response_event','source_position','available_at','delivery_basis','status','weight','request_sha256')}
        graph={'version':VERSION,'conversation_id':cid,'nodes':events,
               'temporal_edges':[{'source':a['id'],'target':b['id'],'kind':'temporal_next','observed_communication':False} for a,b in zip(events,events[1:])],
               'same_actor_edges':same,
               'recipient_metadata':[{'source':e['id'],'recipients':list(dict.fromkeys(e['recipients'])),'basis':e.get('delivery_basis')} for e in events if e['recipients']],
               'interaction_links':[dict(minimal(r),provenance_channel=CHANNELS[channel(r['delivery_basis'])],observed_reply=False) for r in pairs],
               'score_layers':{d:[minimal(r) for r in rows[d]] for d in LAYERS},
               'limitation':'Response endpoints were selected by the original heuristic. Recipient metadata does not establish reply-to identity; links remain candidates.'}
        save(graphdir/(cid+'.json'),graph)
        winrecords=[{k:v for k,v in w.items() if not isinstance(v,np.ndarray)} for w in wins]
        save(windowdir/(cid+'.json'),winrecords)
        arrays={f'w{w["index"]:04d}_{k}':w[k] for w in wins for k in ('values','observed','chronology','continuity','topology')}
        np.savez_compressed(windowdir/(cid+'.npz'),**arrays)
        expected={(r['record_id'],r['source_event'],r['response_event']) for r in pairs}
        included={rid for w in wins for rid in w['pair_ids']}
        assert {e[0] for e in expected}==included
        totals.update({'conversations':1,'events':len(events),'unique_interaction_pairs':len(pairs),'windows':len(wins),
                       'cross_boundary_pair_occurrences':sum(sum(next(r['source_position'] for r in pairs if r['record_id']==rid)<w['start'] for rid in w['pair_ids']) for w in wins)})
        totals.update({CHANNELS[q]:sum(channel(r['delivery_basis'])==q for r in pairs) for q in range(3)})
        pair_audit.append({'conversation_id':cid,'event_count':len(events),'all_events_retained':True,'unique_pairs':len(expected),'all_pairs_retained':True})
    width=max(len(w['event_ids']) for wins in collection for w in wins)
    maxwindows=max(map(len,collection));capacity=max(max(len(rr) for rr in rows.values()) for _,rows in source_rows)
    save(out/'metadata.json',{'labels':dict(zip(LABELS,LABEL_NAMES)),'conversations':metadata})
    save(out/'graph-preservation-audit.json',{'passed':True,'totals':dict(totals),'conversations':pair_audit,
                                           'event_index_width':width,'max_window_count':maxwindows,'score_sequence_capacity':capacity,
                                           'reply_identity_confirmed':0,'scores_reused_without_modification':True})
    return metadata,collection,source_rows,width,maxwindows,capacity,dict(totals)


def build_kernels(collection,source_rows,width,maxwindows,capacity,candidates,out):
    graphsize=maxwindows*3*2*(2*width)**2;masksize=maxwindows*(3*9*width*width+width)
    mask_all=gram([mask_vector(w,width) for w in collection],masksize)
    score=gram([score_vector(c,r,capacity) for c,r in source_rows],9*capacity*6)
    activity=[]
    for conv,rows in source_rows:
        counts=collections.Counter(e['kind'] for e in conv['events'])
        actors=collections.Counter(e['actor'] for e in conv['events'])
        types=collections.Counter(channel(r['delivery_basis']) for r in rows['uptake'])
        activity.append([np.log1p(len(conv['events'])),np.log1p(len(actors)),np.log1p(len(rows['uptake'])),
                         *[np.log1p(counts[k]) for k in ('message','prompt','input','tool_call','tool_result','reasoning','artifact_update','plan','status')],
                         *[np.log1p(types[k]) for k in range(3)]])
    a=np.array(activity);activity=a@a.T/len(a[0])
    topology=gram([graph_vector(w,width,topology=True) for w in collection],graphsize)
    kernels={'activity_only':activity,'scores_only':score+activity,'topology_only':topology+mask_all}
    kernels['scores_plus_topology']=kernels['scores_only']+kernels['topology_only']
    layergrams=[]
    for d,layer in enumerate(LAYERS):
        g=gram([graph_vector(w,width,layer=d) for w in collection],graphsize)
        layergrams.append(g)
        kernels['individual_'+layer]=g+topology+gram([mask_vector(w,width,layer=d) for w in collection],masksize)
        print(json.dumps({'individual_kernel':layer}),flush=True)
    kernels['all_laplacians']=np.mean(layergrams,axis=0)+mask_all+topology
    kernels['scores_plus_laplacians']=kernels['all_laplacians']+kernels['scores_only']
    bank=[]
    for k,alpha in enumerate(candidates):
        bank.append(gram([graph_vector(w,width,alpha=alpha) for w in collection],graphsize)+mask_all+topology)
        if (k+1)%5==0:print(json.dumps({'mixture_kernels':k+1,'total':len(candidates)}),flush=True)
    bank=np.array(bank);kernels['average_graph']=bank[0]
    np.savez_compressed(out/'kernels.npz',**kernels)
    np.savez_compressed(out/'mixture-kernels.npz',kernels=bank,weights=candidates)
    return kernels,bank
