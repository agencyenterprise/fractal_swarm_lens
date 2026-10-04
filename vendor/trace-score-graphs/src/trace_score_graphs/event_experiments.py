"""Rerun all graph classifiers and nested class mixtures on ordered event graphs."""
import argparse
import collections
import concurrent.futures
import csv
import json
import platform
import time
from pathlib import Path

import numpy as np
import scipy
import sklearn
from sklearn.svm import SVC
from sklearn.metrics import average_precision_score,roc_auc_score,precision_recall_fscore_support

from . import event_graphs
from .event_graphs import CHANNELS,LAYERS,LABELS,LABEL_NAMES
from .classification import make_splits,metrics,fast_macro_ap
from .weighted_classification import candidates,choose_candidate,inner_splits
from .common import read,save,file_sha,digest,jsonl
from .pipeline import current_run

TARGETS=(*LABELS,'any_problem')
VERSION='ordered-full-event-laplacian-experiment-v2'


def targets(metadata):
    y=np.array([m['labels'] for m in metadata]);binary=np.where(np.any(y==1,axis=1),1,np.where(np.all(y==0,axis=1),0,-1))
    return np.column_stack([y,binary])


def fit_kernel(k,y,train,test,serialize=False):
    train=np.asarray([i for i in train if y[i]>=0],dtype=int);test=np.asarray(test,dtype=int);yy=y[train]
    if not len(yy) or len(np.unique(yy))<2:
        p=float(yy.mean()) if len(yy) else .5
        return np.full(len(test),p),{'constant':p,'training_indices':train.tolist()}
    a=k[np.ix_(train,train)];col=a.mean(axis=0);mean=float(a.mean());a=a-col[None,:]-col[:,None]+mean
    scale=float(np.trace(a)/len(a))
    if scale<1e-12:
        p=float(yy.mean());return np.full(len(test),p),{'constant':p,'training_indices':train.tolist(),'reason':'zero centered kernel'}
    b=k[np.ix_(test,train)];b=(b-b.mean(axis=1)[:,None]-col[None,:]+mean)/scale
    model=SVC(C=1.,kernel='precomputed',class_weight='balanced',tol=1e-6,max_iter=100000,cache_size=128)
    model.fit(a/scale,yy)
    if model.fit_status_!=0:raise RuntimeError('Kernel classifier did not converge')
    decision=model.decision_function(b) if len(test) else np.empty(0);prob=1/(1+np.exp(-np.clip(decision,-30,30)))
    info={}
    if serialize:info={'training_indices':train.tolist(),'training_column_means':col.tolist(),'training_grand_mean':mean,
                       'kernel_scale':scale,'support_positions':model.support_.tolist(),'dual_coefficients':model.dual_coef_[0].tolist(),
                       'intercept':float(model.intercept_[0]),'iterations':model.n_iter_.tolist()}
    return prob,info


def replay_kernel(k,model,test):
    if 'constant' in model:return np.full(len(test),model['constant'])
    train=np.array(model['training_indices']);b=k[np.ix_(test,train)]
    b=(b-b.mean(axis=1)[:,None]-np.array(model['training_column_means'])[None,:]+model['training_grand_mean'])/model['kernel_scale']
    score=b[:,model['support_positions']]@np.array(model['dual_coefficients'])+model['intercept']
    return 1/(1+np.exp(-np.clip(score,-30,30)))


def report_metrics(y,p):
    result=metrics(y[:,:14],p[:,:14]);mask=y[:,14]>=0;yy=y[mask,14];pp=p[mask,14]
    pr,re,f1,_=precision_recall_fscore_support(yy,pp>=.5,average='binary',zero_division=0)
    result['any_problem']={'known':len(yy),'positive':int(yy.sum()),'negative':int(len(yy)-yy.sum()),
                           'ap':float(average_precision_score(yy,pp)) if 0<yy.sum()<len(yy) else None,
                           'roc_auc':float(roc_auc_score(yy,pp)) if 0<yy.sum()<len(yy) else None,
                           'precision':float(pr),'recall':float(re),'f1':float(f1)}
    return result


def select_class(j,bank,y,train,test,folds,weights,directory):
    train=np.array(train);lookup={int(i):k for k,i in enumerate(train)}
    inner=np.full((len(weights),len(train)),np.nan)
    for k in range(len(weights)):
        for fold in folds:
            assert set(fold['train'])|set(fold['validation'])<=set(train)
            p,_=fit_kernel(bank[k],y[:,j],fold['train'],fold['validation'])
            inner[k,[lookup[i] for i in fold['validation']]]=p
    assert np.isfinite(inner).all()
    known=y[train,j]>=0;yy=y[train,j][known]
    ap=np.array([average_precision_score(yy,p[known]) for p in inner]) if 0<yy.sum()<len(yy) else np.zeros(len(weights))
    k,objective=choose_candidate(ap,weights,.05)
    p,model=fit_kernel(bank[k],y[:,j],train,test,serialize=True)
    selection={'candidate':k,'weights':weights[k].tolist(),'inner_ap':ap.tolist(),'objective':objective.tolist(),'training_indices':train.tolist()}
    save(directory/(TARGETS[j]+'.json'),{'selection':selection,'model':model})
    np.savez_compressed(directory/(TARGETS[j]+'-inner.npz'),probabilities=inner,training_indices=train)
    return j,p,selection


def uncertainty(y,pred,groups,seed):
    comparisons=[('average_graph','scores_only'),('all_laplacians','scores_only'),('scores_plus_laplacians','scores_only'),
                 ('average_graph','scores_plus_topology'),('all_laplacians','scores_plus_topology'),
                 ('class_weighted_graph','average_graph'),('class_weighted_graph','scores_only'),('class_weighted_graph','scores_plus_topology')]
    members=[np.flatnonzero(np.array(groups)==g) for g in sorted(set(groups))];rng=np.random.default_rng(seed)
    vals={k:[] for pair in comparisons for k in pair}
    for _ in range(200):
        ids=np.concatenate([members[j] for j in rng.integers(len(members),size=len(members))])
        for k in vals:vals[k].append(fast_macro_ap(y[:,:14],pred[k][:,:14],ids))
    return {a+'_minus_'+b:{'macro_ap_difference':fast_macro_ap(y[:,:14],pred[a][:,:14],np.arange(len(y)))-fast_macro_ap(y[:,:14],pred[b][:,:14],np.arange(len(y))),
                           'paired_task_bootstrap_95_interval':np.quantile(np.array(vals[a])-vals[b],[.025,.975]).tolist()}
            for a,b in comparisons}


def evaluate(out,kernels,bank,metadata,weights,seed,workers):
    y=targets(metadata);groups=[m['task_group'] for m in metadata];frameworks=[m['framework'] for m in metadata]
    splits=make_splits(groups,frameworks,seed);save(out/'splits.json',{'conversation_order':[m['conversation_id'] for m in metadata],'protocols':splits})
    evaluations={}
    for protocol,folds in splits.items():
        prediction={k:np.full(y.shape,np.nan) for k in kernels};prediction.update({k:np.full(y.shape,np.nan) for k in ('class_weighted_graph','training_prevalence')})
        summaries=[]
        for fi,fold in enumerate(folds):
            train,test=fold['train'],fold['test'];directory=out/'models'/protocol/fold['name'];directory.mkdir(parents=True,exist_ok=True)
            # Reuse complete fold files only under this source/configuration-addressed run.
            saved=directory/'fold-predictions.npz'
            if saved.exists():
                with np.load(saved) as p:
                    for k in prediction:prediction[k][test]=p[k]
                summaries.append(read(directory/'fold-summary.json'));continue
            for k,matrix in kernels.items():
                models={}
                for j,lab in enumerate(TARGETS):
                    prediction[k][test,j],models[lab]=fit_kernel(matrix,y[:,j],train,test,serialize=True)
                save(directory/(k+'.json'),models)
            for j in range(len(TARGETS)):
                yy=y[train,j];yy=yy[yy>=0];prediction['training_prevalence'][test,j]=yy.mean() if len(yy) else .5
            inner=inner_splits(train,groups,seed+fi);save(directory/'inner-splits.json',inner)
            selected={}
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                work=[pool.submit(select_class,j,bank,y,train,test,inner,weights,directory/'mixtures') for j in range(len(TARGETS))]
                for future in concurrent.futures.as_completed(work):
                    j,p,choice=future.result();prediction['class_weighted_graph'][test,j]=p;selected[TARGETS[j]]=choice['candidate']
            summary={'fold':fold['name'],'train':len(train),'test':len(test),'selected_candidates':selected}
            summaries.append(summary);save(directory/'fold-summary.json',summary)
            np.savez_compressed(saved,**{k:p[test] for k,p in prediction.items()})
            print(json.dumps({'evaluation':protocol,'fold':fold['name'],'completed':True}),flush=True)
        prediction['prediction_average']=np.mean([prediction['individual_'+d] for d in LAYERS],axis=0)
        assert all(np.isfinite(p).all() for p in prediction.values())
        np.savez_compressed(out/('predictions-'+protocol+'.npz'),**prediction,labels=y)
        evaluations[protocol]={'methods':{k:report_metrics(y,p) for k,p in prediction.items()},'folds':summaries,'comparisons':uncertainty(y,prediction,groups,seed)}
    directory=out/'final-models';train=list(range(len(y)));folds=inner_splits(train,groups,seed);save(directory/'inner-splits.json',folds)
    final={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        work=[pool.submit(select_class,j,bank,y,train,[],folds,weights,directory) for j in range(len(TARGETS))]
        for future in concurrent.futures.as_completed(work):
            j,p,choice=future.result();final[TARGETS[j]]=choice
    return evaluations,final


def protocol(size,stride,seed):
    return {'version':VERSION,'graph_version':event_graphs.VERSION,'window_events':size,'stride_events':stride,'seed':seed,
            'nodes':'Individual normalized events, stable IDs and chronological positions; no actor aggregation.',
            'links':'All unique scored source-response pairs retained by evidence record ID. Response identities are heuristic candidates, never claimed observed replies. Recipient metadata retained verbatim, duplicate recipients normalized only for display.',
            'provenance_channels':CHANNELS,'time':'All events retained. Direct temporal-next and same-actor-next relations saved separately. Window graphs use chronological and same-actor order among their events. Earlier scored source endpoints carried into windows when their response is active. No future endpoints.',
            'message_layers':'Five scores remain attributes of their source event, including unpaired events. In the Laplacian encoding they decorate the fixed OUT/IN bridge of that same event, never another event’s interaction.',
            'interaction_layers':'Four scores remain on their EXACT source-response pair, joined by record_id, with provenance separated. Missing scores are masked; no fabricated values.',
            'laplacian':'For each provenance channel, C=max(temporal_next,same_actor_next); T=C+C.T; B=[[T,I+S],[I+S.T,T]], D=diag(sum(abs(B))), L=D-B; N=D^-1/2 L D^-1/2. S diagonal=message attributes or directed off-diagonal=interaction scores. Temporal and semantic links occupy separate matrix blocks. I links copies of the same event, not communication. Signed agreement retained.',
            'model_input':'Every raw and normalized matrix entry, three provenance channels, all chronological windows concatenated by window index and event-relative position. Sparse right padding only, never pooling/resampling/truncating. Kernels are exact linear dot products of these vectors. Shared topology matrices preserve candidate links even when their scores are unavailable; availability masks remain separate.',
            'score_baseline':'All unique score records in availability order, value, availability, presence, availability time, provenance category, not-applicable status, plus activity features. No interaction endpoints. All events included.',
            'classifier':'Binary SVC for each MAST label plus any_problem; precomputed LINEAR kernel, C=1, balanced classes. Kernel centering and mean centered diagonal scaling fit ONLY on known-label training rows. Sigmoid(decision) saved as ranking score, not calibrated probability; decision0 threshold.',
            'mixtures':'Same 65 fixed nonnegative nine-score weight candidates as earlier; per-entry availability renormalization before Laplacians. Separate node/interaction supports mean absolute weights across support groups are not identifiable contribution shares.',
            'selection':'Three task-group inner folds, AP minus .05 concentration penalty. Outer grouped five-fold and framework-held-out splits fixed to previous experiment, with shared tasks purged. Final all-data refit excluded from evaluation.',
            'metrics':'Macro AP/F1/ROC-AUC over the14 MAST labels only; any_problem reported separately, unresolved references excluded. Fixed-threshold F1. 200 paired task-cluster bootstrap intervals conditional on saved fits.',
            'limitations':['The scored response endpoints still originate from bounded next-response heuristics; no experiment can recover unrecorded delivery or exact reply IDs from these scores.','Candidate relation channels are not proof of influence or cascade.','Source scores retain bounded-context truncation and original scorer uncertainty.','This selected cohort has20 clean controls and overlapping issue labels; all inspected300 are development data.','Windowing remains a modeling choice; boundary source endpoints are retained, full traces/links stored separately.','Full ordered matrices preserve the chosen representation, but a classifier need not exploit all its information. A linear-kernel learner can underfit.'],
            'no_new_API_calls':True,'previous_runs':'Superseded actor-averaged experiments retained as historical artifacts; their conclusions do not describe this representation.'}


def run(root,size,stride,seed=20261004,workers=4):
    root=Path(root).resolve();source=current_run(root/'data')
    configuration=protocol(size,stride,seed)
    paths=[root/'data/selection.json',*sorted((source/'schemas').glob('*.json')),*sorted((source/'scores').glob('*/*.jsonl'))]
    hashes={str(p.relative_to(root)):file_sha(p) for p in paths}
    implementations={p.name:file_sha(p) for p in [Path(__file__),Path(event_graphs.__file__)]}
    manifest={'protocol':configuration,'source_hashes':digest(hashes),'implementations':implementations,'versions':{'numpy':np.__version__,'scipy':scipy.__version__,'sklearn':sklearn.__version__,'python':platform.python_version()}}
    out=root/'data/event-experiments'/digest(manifest)[:16];out.mkdir(parents=True,exist_ok=True)
    if (out/'results.json').exists() and read(out/'results.json').get('complete'):return out
    save(out/'protocol.json',configuration);save(out/'source-hashes.json',hashes);save(out/'manifest.json',manifest)
    started=time.monotonic();weights=candidates(seed)
    if not (out/'mixture-kernels.npz').exists():
        meta,collection,source_rows,width,mw,cap,totals=event_graphs.prepare(root,source,out,size,stride)
        print(json.dumps({'prepared':size,'totals':totals,'matrix_width':width,'max_windows':mw}),flush=True)
        kernels,bank=event_graphs.build_kernels(collection,source_rows,width,mw,cap,weights,out)
        del collection,source_rows
    else:
        meta=read(out/'metadata.json')['conversations'];totals=read(out/'graph-preservation-audit.json')['totals']
        with np.load(out/'kernels.npz') as k:kernels={n:k[n] for n in k.files}
        bank=np.load(out/'mixture-kernels.npz')['kernels']
    ev,final=evaluate(out,kernels,bank,meta,weights,seed,workers)
    result={'complete':True,'experiment_id':out.name,'configuration':configuration,'totals':totals,'evaluations':ev,'final_fit':final,'elapsed_seconds':time.monotonic()-started}
    save(out/'results.json',result)
    with (out/'per-label-metrics.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['protocol','method','label','known','positive','negative','ap','roc_auc','precision','recall','f1']);writer.writeheader()
        for p,e in ev.items():
            for method,m in e['methods'].items():
                for lab,row in {**m['per_label'],'any_problem':m['any_problem']}.items():writer.writerow({'protocol':p,'method':method,'label':lab,**row})
    print(json.dumps({'complete':True,'run':str(out),'macro_ap':{p:{k:round(v['macro_ap'],4) for k,v in e['methods'].items()} for p,e in ev.items()}}),flush=True)
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--window',type=int,default=20);p.add_argument('--stride',type=int,default=10);p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    run(a.root,a.window,a.stride,workers=a.workers)


if __name__=='__main__':main()
