"""Rerun per-class/per-score contrasts and publish corrected experiment reports."""
import argparse
import base64
import collections
import csv
import html
import itertools
import json
from pathlib import Path

import numpy as np

from . import event_graphs as g
from .classification import LABELS,LABEL_NAMES,SPECTRAL,spectrum_features
from .class_contrasts import GRAPHS,METRICS,analyze,aligned_drift,safe_mean,finite_json,table
from .common import read,save,file_sha,jsonl
from .event_experiments import targets


def extract(out):
    meta=read(out/'metadata.json')['conversations'];splits=read(out/'splits.json')['protocols']['leave_framework_out']
    result=read(out/'results.json');alpha=np.full((300,14,9),np.nan);assignments=[]
    for fold in splits:
        assert not {meta[i]['task_group'] for i in fold['train']}&{meta[i]['task_group'] for i in fold['test']}
        for j,c in enumerate(LABELS):
            f=read(out/'models/leave_framework_out'/fold['name']/'mixtures'/(c+'.json'))['selection']
            assert set(f['training_indices'])==set(fold['train'])
            alpha[fold['test'],j]=f['weights'];assignments.append({'framework':fold['name'],'class':c,**f})
    values=[];window_values=[];window_index=[];pairs=collections.Counter();mixtures=collections.Counter();spectrum_counts=collections.Counter()
    lapdir=out/'laplacians';lapdir.mkdir(exist_ok=True)
    for i,m in enumerate(meta):
        cid=m['conversation_id'];wins=g.load_windows(out/'windows'/cid);graph=read(out/'event-graphs'/(cid+'.json'))
        sequence=[];previous=None;arrays={}
        for w in wins:
            sem=[w['values'][:,d] for d in range(9)]+[g.mix(w['values'],w['observed'],np.ones(9)/9)]+[g.mix(w['values'],w['observed'],a) for a in alpha[i]]
            v=np.full((3,len(GRAPHS),len(METRICS)),np.nan);current={};eig={}
            for d,name in enumerate(GRAPHS):
                raw_all=[];norm_all=[]
                for q in range(3):
                    raw,norm=g.laplacians(sem[d][q],w['chronology'],w['continuity']);v[q,d,:9]=spectrum_features(raw,norm)
                    current[q,d]=norm;eig[q,d]=np.linalg.eigvalsh(norm)
                    if previous is not None:v[q,d,11]=aligned_drift(previous[0][q,d],previous[1],norm,w['event_ids'])
                    raw_all.append(raw);norm_all.append(norm)
                    if name=='average':
                        spectrum_counts['equal_graph_channel_windows']+=1
                        spectrum_counts['equal_graph_lambda2_zero']+=int(eig[q,d][1]<1e-8)
                if d<10:
                    arrays[f'w{w["index"]:04d}_{name}_laplacian']=np.array(raw_all)
                    arrays[f'w{w["index"]:04d}_{name}_normalized_laplacian']=np.array(norm_all)
                else:
                    # Exact mixture adjacency plus saved chronology reconstructs both Laplacians.
                    arrays[f'w{w["index"]:04d}_{name}_semantic']=sem[d]
            for q in range(3):
                for a,b in itertools.combinations(range(9),2):
                    if np.any(np.abs(sem[a][q])>1e-10) and np.any(np.abs(sem[b][q])>1e-10):
                        pairs['both_nonzero_semantic']+=1
                        pairs['same_normalized_spectrum']+=int(np.allclose(eig[q,a],eig[q,b],atol=1e-8,rtol=0))
                        pairs['same_semantic_matrix']+=int(np.allclose(sem[a][q],sem[b][q],atol=1e-8,rtol=0))
                for a,b in itertools.combinations(range(10,len(GRAPHS)),2):
                    mixtures['class_pair_channel_windows']+=1
                    mixtures['same_normalized_spectrum']+=int(np.allclose(eig[q,a],eig[q,b],atol=1e-8,rtol=0))
                    mixtures['same_semantic_matrix']+=int(np.allclose(sem[a][q],sem[b][q],atol=1e-8,rtol=0))
            sequence.append(v);window_values.append(v);window_index.append({'conversation_index':i,'index':w['index'],'start':w['start'],'end':w['end'],'event_ids':w['event_ids']})
            previous=current,w['event_ids']
        vv=safe_mean(sequence)
        for d,layer in enumerate(g.LAYERS):
            for q in range(3):
                rr=[r for r in graph['score_layers'][layer] if g.SPECS[layer][0]=='message' or g.channel(r['delivery_basis'])==q]
                scores=[r['weight'] for r in rr if r['status']=='scorable' and r['weight'] is not None]
                vv[q,d,9]=np.mean(scores) if scores else np.nan;vv[q,d,10]=len(scores)/len(rr) if rr else np.nan
        values.append(vv);np.savez_compressed(lapdir/(cid+'.npz'),**arrays)
        if (i+1)%50==0:print(json.dumps({'descriptive_matrices':i+1,'window_size':result['configuration']['window_events']}),flush=True)
    values=np.array(values)
    np.savez_compressed(out/'descriptive-measurements.npz',conversations=values,windows=np.array(window_values),crossfit_weights=alpha)
    save(out/'descriptive-index.json',{'graphs':GRAPHS,'metrics':METRICS,'channels':g.CHANNELS,'windows':window_index,
                                      'source_score':'Mean of all unique original scores for the layer/channel, not the old paired-source subset.'})
    save(out/'crossfit-weights.json',assignments)
    save(out/'spectrum-diagnostics.json',{'layers':dict(pairs),'mixtures':dict(mixtures),'equal_graph':dict(spectrum_counts),
                                        'denominator_warning':'Layer comparisons require nonzero semantic matrices in both layers; mixture comparison includes structure-only channels. Structural temporal/identity links are always present.'})
    return meta,values


def write_contrasts(out,meta,values):
    # Classification uses all channels and all windows separately. Only the descriptive overview averages channels.
    overview=analyze(meta,safe_mean(values,axis=1),400,20261004)
    save(out/'class-contrasts.json',overview)
    fields=[k for k in overview['contrasts'][0] if k not in ('strata','length_strata')]
    with (out/'class-contrasts.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:r[k] for k in fields} for r in overview['contrasts'])
    for q,channel in enumerate(g.CHANNELS):
        a=analyze(meta,values[:,q],0,20261004);save(out/('class-contrasts-'+channel+'.json'),a)
    save(out/'descriptive-protocol.json',{'unit':'Conversation, windows averaged only for descriptive comparisons; model input is every ordered matrix entry.',
                                        'overview':'Equal mean of three provenance-channel descriptors; node scores duplicated across channels; link scores remain channel-specific in the separate files.',
                                        'uncertainty':'400 task-cluster Bayesian bootstrap draws for overview; individual provenance channels descriptive point estimates only. No multiplicity adjustment.',
                                        'code_sha256':file_sha(__file__)})
    return overview


def plot(out,result,analysis):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    methods=['activity_only','scores_only','topology_only','scores_plus_topology','average_graph',*['individual_'+d for d in g.LAYERS],
             'all_laplacians','prediction_average','scores_plus_laplacians','class_weighted_graph']
    fig,ax=plt.subplots(figsize=(12,9),constrained_layout=True)
    for off,p,color,label in [(-.18,'grouped_5fold','#168c80','Task groups'),(.18,'leave_framework_out','#536eaf','Framework held out')]:
        ax.barh(np.arange(len(methods))+off,[result['evaluations'][p]['methods'][m]['macro_ap'] for m in methods],height=.34,color=color,label=label)
    ax.set_yticks(range(len(methods)),[m.replace('individual_','').replace('_',' ') for m in methods]);ax.invert_yaxis();ax.set_xlim(0,1);ax.legend();ax.set_xlabel('Macro average precision over 14 MAST labels')
    ax.set_title(f'Corrected event graphs · {result["configuration"]["window_events"]}-event windows\nFull ordered matrices; identical linear-kernel classifier for every variant')
    fig.savefig(out/'classification.png',dpi=150);plt.close(fig)
    fig,axs=plt.subplots(1,3,figsize=(18,9),constrained_layout=True)
    for ax,metric,title in zip(axs,('combinatorial_mean','normalized_nonzero_fraction','aligned_matrix_drift'),('Raw Laplacian intensity','Normalized rank fraction','Event-aligned matrix drift')):
        rows={(r['class'],r['graph']):r for r in analysis['contrasts'] if r['reference']=='other_problems' and r['metric']==metric}
        data=np.array([[rows[c,d]['adjusted_delta'] for d in g.LAYERS] for c in LABELS],float)
        im=ax.imshow(data,cmap='RdBu_r',vmin=-1,vmax=1,aspect='auto');ax.set_title(title)
        ax.set_xticks(range(9),[d.replace('_',' ') for d in g.LAYERS],rotation=60,ha='right')
        ax.set_yticks(range(14),[f'{c} {n}' for c,n in zip(LABELS,LABEL_NAMES)] if ax is axs[0] else LABELS)
        for i,j in np.ndindex(data.shape):
            if np.isfinite(data[i,j]):ax.text(j,i,f'{data[i,j]:.1f}',ha='center',va='center',fontsize=8,color='white' if abs(data[i,j])>.65 else '#193243')
    fig.colorbar(im,ax=axs,shrink=.7,label='Within-framework Cliff delta: class versus other problems')
    fig.suptitle('Descriptive class contrasts • provenance-channel means only for this overview',fontsize=16)
    fig.savefig(out/'class-contrasts.png',dpi=150);plt.close(fig)
    # Paired first-to-last changes: every contributing conversation has >=2 windows.
    meta=read(out/'metadata.json')['conversations'];y=np.array([m['labels'] for m in meta]);clean=np.all(y==0,axis=1);problem=np.any(y==1,axis=1)
    info=read(out/'descriptive-index.json');data=np.load(out/'descriptive-measurements.npz')['windows'];series=collections.defaultdict(list)
    for i,w in enumerate(info['windows']):series[w['conversation_index']].append(i)
    fig,axes=plt.subplots(3,5,figsize=(19,10),constrained_layout=True)
    for ax,c in zip(axes.flat,('any_problem',*LABELS)):
        pos=problem if c=='any_problem' else y[:,LABELS.index(c)]==1
        ref=clean if c=='any_problem' else (y[:,LABELS.index(c)]==0)&problem
        for mask,name,color in ((pos,'Present','#b65a14'),(ref,'Reference','#087e8b')):
            ids=[v for i,v in series.items() if mask[i] and len(v)>1]
            changes=[np.mean(data[v[-1],:,:,7]-data[v[0],:,:,7],axis=0)[[0,1,3,8]] for v in ids]
            if changes:ax.plot(range(4),np.median(changes,axis=0),marker='o',color=color,label=f'{name} n={len(ids)}')
        ax.axhline(0,color='#94a3b8',lw=.7);ax.set_title(c);ax.set_xticks(range(4),['Relevance','Novelty','Verification','Fulfillment'],rotation=40,ha='right');ax.legend(fontsize=7)
    fig.suptitle('Paired temporal change: last-window minus first-window raw Laplacian intensity\nThree-channel mean for display; single-window traces excluded',fontsize=15)
    fig.savefig(out/'temporal-change.png',dpi=150);plt.close(fig)


def fmt(v,n=3):return '—' if v is None else f'{v:.{n}f}'


def img(path):return '<img alt="'+html.escape(path.stem)+'" src="data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()+'">'


def publish(root,outs):
    results=[read(o/'results.json') for o in outs];analyses=[read(o/'class-contrasts.json') for o in outs]
    body=['<header><p class="eyebrow">SWARMLENS · CORRECTED EXPERIMENTS · EVENT GRAPHS V2</p><h1>Individual events, directed links, temporal order</h1><p>All baseline, individual-score, combined-score, nested-weight and class-contrast experiments rerun.</p></header>']
    body+=['<section><h2>What changed—and what did not</h2><p><b>All 13,069 events and 10,331 unique scored pairs are retained.</b> The 22 duplicated evidence records are removed once; no source conversation is dropped. Repeated interactions between the same agents remain separate event pairs. Five message scores remain on their own events, including events without a response. Four interaction scores use their exact evidence ID and source/target pair. Cross-boundary source events are carried into the relevant window.</p><p><b>The classifier uses every entry of the raw and normalized Laplacians in chronological window order.</b> Sparse padding aligns coordinates; there is no actor averaging, spectral-only feature extraction, window averaging, sequence resampling or truncation in model input. Descriptive tables average measurements across windows only after fitting, to compare conversations as the statistical unit.</p><p>The model is now a fixed <b>linear-kernel SVM</b> (C=1, balanced classes), so variable-length full matrices can be used without materializing huge dense feature vectors. Every variant uses this same model. Centering and scaling use training rows only. The kernel is an exact dot product, not an approximate embedding. This and the Laplacian encoding are methodological changes; old-to-new performance differences cannot be attributed solely to fixing aggregation.</p><p>The original Jev scores, conversations, reference labels and outer task/framework splits are unchanged. No new scorer calls were made. All inspected conversations remain development data; there is no untouched confirmation set in this rerun.</p></section>']
    body+=['<section><h2>Direct communication versus candidate replies</h2><p><b>A recorded recipient is not an explicit reply-to identifier.</b> The saved pairing procedure picked later responses heuristically. We retain those scored pairs as candidates in three separate matrix channels:</p>',table(['Channel','Unique pairs','Meaning'],[
        ['Recorded recipient candidate','1,613','Recipient/tool route is recorded; the chosen response endpoint remains heuristic.'],
        ['Workflow candidate','2,561','Recipient inferred from phase roles, two-party dialogue or workflow.'],
        ['Unaddressed candidate','6,157','Later different-actor event; exposure is unconfirmed.']]),
        '<p>Original recipient metadata, event chronology, actor continuity and candidate response links are stored as distinct relations. Nothing here claims that inferred links are observed causal influence. Exact unrecorded reply-to relations cannot be recovered by rerunning a classifier.</p></section>']
    rows=[]
    main=['activity_only','scores_only','topology_only','scores_plus_topology','average_graph','all_laplacians','scores_plus_laplacians','class_weighted_graph']
    for method in main:
        rows.append([method.replace('_',' ')]+[fmt(r['evaluations'][p]['methods'][method]['macro_ap']) for r in results for p in ('grouped_5fold','leave_framework_out')])
    body+=['<section><h2>Classification results</h2><p>Macro AP over the 14 issue labels; higher is better. Topology-only uses the temporal graph, candidate links and availability without numeric semantic scores. Scores-only retains the complete ordered score sequence and activity. Scores + topology is a stronger control for whether semantic graph weighting adds useful information.</p>',table(['Method','20: task groups','20: framework held out','10: task groups','10: framework held out'],rows)]
    for o,r in zip(outs,results):
        c=r['evaluations']['leave_framework_out']['comparisons']['class_weighted_graph_minus_average_graph']
        body.append(f'<p><b>{r["configuration"]["window_events"]} events:</b> learned-minus-equal macro AP {c["macro_ap_difference"]:+.4f}, paired task-bootstrap interval [{c["paired_task_bootstrap_95_interval"][0]:+.4f}, {c["paired_task_bootstrap_95_interval"][1]:+.4f}].</p>')
        body+=['<details><summary>All individual score models · '+str(r['configuration']['window_events'])+' events</summary>',img(o/'classification.png'),'</details>']
    body+=['<p><b>The corrected representation does not establish an overall benefit from learned score mixing.</b> Compare against both scores-only and topology-only; a graph beating scores-only while matching topology does not establish a contribution from the scorer’s edge weights.</p></section>']
    body+=['<section><h2>Problem versus no problem</h2><p>278 consensus problem traces, 20 fully clean traces, 2 unresolved traces excluded. Problem prevalence is 93.3%, so a high positive-class AP or raw accuracy alone is not persuasive. The table includes balanced accuracy, which gives the two classes equal weight. Ranking scores are sigmoid-transformed SVM decisions, not calibrated probabilities.</p>']
    rows=[]
    for o,r in zip(outs,results):
        pred=np.load(o/'predictions-leave_framework_out.npz');y=pred['labels'][:,-1];known=y>=0
        for method in main:
            p=pred[method][:,-1];yy=y[known];pp=p[known]>=.5
            balanced=(np.mean(pp[yy==1])+np.mean(~pp[yy==0]))/2
            m=r['evaluations']['leave_framework_out']['methods'][method]['any_problem']
            rows.append([r['configuration']['window_events'],method.replace('_',' '),fmt(m['ap']),fmt(m['roc_auc']),fmt(m['f1']),fmt(balanced),f'{int(np.sum(pp[yy==0]))}/20'])
    body+=[table(['Events','Method','Problem AP','ROC-AUC','Problem F1','Balanced accuracy','False positives among clean'],rows),'</section>']
    body+=['<section><h2>Every class and every score</h2><p>All class counts use consensus annotation cells, with unknown/disputed cells masked. Classes overlap. Descriptive effects below compare a class with <b>other problem traces where that class is known absent</b>. Each conversation contributes once. Positive Cliff delta means the measured quantity is larger; it does not mean more severe behavior.</p><p>The overview averages the three provenance-channel descriptors for display. Classifiers keep those channels separate. The downloadable files also include every channel separately and comparisons with clean traces and with all class-absent traces.</p>',img(outs[0]/'class-contrasts.png')]
    for j,(c,name) in enumerate(zip(LABELS,LABEL_NAMES)):
        m=results[0]['evaluations']['leave_framework_out']['methods']['average_graph']['per_label'][c]
        body.append(f'<details><summary>{c} · {html.escape(name)} <small>{m["positive"]}/{m["known"]} positive/known</small></summary>')
        rows=[]
        for d in (*g.LAYERS,'average','mixture_'+c):
            method='individual_'+d if d in g.LAYERS else 'average_graph' if d=='average' else 'class_weighted_graph'
            def find(a,metric,ref='other_problems'):
                return next(z for z in a['contrasts'] if (z['class'],z['reference'],z['graph'],z['metric'])==(c,ref,d,metric))
            a=find(analyses[0],'combinatorial_mean');b=find(analyses[1],'combinatorial_mean');rank=find(analyses[0],'normalized_nonzero_fraction');drift=find(analyses[0],'aligned_matrix_drift');clean=find(analyses[0],'combinatorial_mean','no_issue')
            rows.append([d.replace('_',' '),fmt(results[0]['evaluations']['leave_framework_out']['methods'][method]['per_label'][c]['ap']),
                         fmt(results[1]['evaluations']['leave_framework_out']['methods'][method]['per_label'][c]['ap']),fmt(a['adjusted_delta'],2),fmt(a['length_adjusted_delta'],2),
                         fmt(b['adjusted_delta'],2),fmt(clean['adjusted_delta'],2),fmt(rank['adjusted_delta'],2),fmt(drift['adjusted_delta'],2)])
        body+=[table(['Layer / combination','20-event AP','10-event AP','20 intensity δ vs other','+ length bins δ','10 intensity δ','20 intensity δ vs clean','20 rank δ','20 drift δ'],rows)]
        rows=[]
        for d,layer in enumerate(g.LAYERS):
            final=[r['final_fit'][c]['weights'][d] for r in results]
            foldweights=[read(outs[0]/'models/leave_framework_out'/f['name']/'mixtures'/(c+'.json'))['selection']['weights'][d] for f in read(outs[0]/'splits.json')['protocols']['leave_framework_out']]
            rows.append([layer.replace('_',' '),fmt(final[0]),fmt(final[1]),fmt(min(foldweights)),fmt(max(foldweights))])
        body+=['<p>Refit weights below use all conversations and do not generate the held-out predictions. Descriptive mixture contrasts use the model trained without each conversation’s framework. Weights are not causal importance; node and interaction supports differ, so between-support weight scales are not identifiable contribution shares.</p>',table(['Score','20-event refit','10-event refit','20-fold minimum','20-fold maximum'],rows),'</details>']
    body+=['</section><section><h2>Temporal changes</h2><p>These descriptive plots pair the first and last window of each multi-window conversation. Single-window traces are excluded. They are not onset labels or proof of propagation; intensity includes the shared temporal structure as well as semantic scores.</p>',img(outs[0]/'temporal-change.png'),'<details><summary>10-event sensitivity</summary>',img(outs[1]/'temporal-change.png'),'</details></section><section><h2>Spectral diagnostics after the correction</h2><p>Spectral equality no longer implies equal classifier inputs: classification uses full matrices, including their event coordinates. The temporal backbone joins chronological event copies so the old isolated-edge lift is not reused unchanged.</p>']
    rows=[]
    for o,r in zip(outs,results):
        d=read(o/'spectrum-diagnostics.json');p=d['layers'];m=d['mixtures'];e=d['equal_graph']
        rows.append([r['configuration']['window_events'],p['both_nonzero_semantic'],f"{p['same_normalized_spectrum']/p['both_nonzero_semantic']:.1%}",f"{p['same_semantic_matrix']/p['both_nonzero_semantic']:.1%}",f"{m['same_normalized_spectrum']/m['class_pair_channel_windows']:.1%}",f"{e['equal_graph_lambda2_zero']}/{e['equal_graph_channel_windows']}"])
    body+=[table(['Events','Nonzero semantic layer pairs × channel × window','Same normalized spectrum','Same semantic matrix','Class-mixture spectra equal (all channels)','Equal graph λ₂ = 0'],rows),'<p>Layer-pair comparisons require nonzero semantic matrices in both layers. Mixture comparisons include channels with no scored interaction, where common message attributes/structure can make graphs identical. These denominators and graph constructions differ from the superseded actor projection; percentages should not be read as a controlled before/after effect.</p></section>']
    body+=['<section><h2>Verification and reproducibility</h2><p>The audit checks original event/score correspondence, exact pair IDs, cross-window retention, independently constructed Laplacians, selected full-vector dot products, kernel validity, unchanged splits, reconstructed predictions and nested weight selection. Tests include one-to-many replies, direction reversal, window-order changes, missing versus zero scores, and future-event exclusion.</p><p>Descriptive intervals are conditional task-cluster Bayesian-bootstrap ranges. They are exploratory and unadjusted for multiple comparisons. Framework × length-bin comparisons change the overlap population. Twenty clean controls are insufficient for a broad validation claim.</p><ul>']
    for o,r in zip(outs,results):
        rel=o.relative_to(root)
        for name in ('results.json','protocol.json','per-label-metrics.csv','graph-preservation-audit.json','independent-audit.json','class-contrasts.json','class-contrasts.csv','descriptive-measurements.npz','spectrum-diagnostics.json',*['class-contrasts-'+q+'.json' for q in g.CHANNELS]):
            body.append(f'<li><a href="{rel}/{name}">{r["configuration"]["window_events"]} events · {name}</a></li>')
    body+=['</ul><p>The <code>event-graphs/</code> directory retains full nodes and typed links, <code>windows/</code> retains unaggregated semantic arrays and masks, and <code>laplacians/</code> retains raw/normalized matrices for each original layer and the equal mixture. Per-class mixture adjacencies and held-out weights reconstruct their Laplacians exactly. All prior experiments remain under their original run IDs.</p></section>']
    css='body{font:16px/1.65 system-ui,sans-serif;background:#edf2f5;color:#19303c;margin:0}main{max-width:1460px;margin:auto;padding:30px}header,section{background:white;padding:28px 32px;border:1px solid #dce5e9;border-radius:14px;margin-bottom:22px}h1{font-size:38px;line-height:1.2}h2{font-size:25px}.eyebrow{font-size:12px;color:#08796f;letter-spacing:.12em}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px;margin:20px 0}td,th{padding:10px;border-bottom:1px solid #dce5e9;text-align:right}td:first-child,th:first-child{text-align:left;min-width:150px}th{background:#edf5f4}img{width:100%;height:auto}details{padding:15px;border:1px solid #dce5e9;border-radius:8px;margin:14px 0}summary{cursor:pointer;font-weight:650}small{color:#647887;font-weight:400;margin-left:12px}a{color:#08796f}code{font-size:13px}@media(max-width:800px){main{padding:12px}section,header{padding:18px}h1{font-size:28px}}'
    document='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Corrected temporal event experiments · SwarmLens</title><style>'+css+'</style><main>'+''.join(body)+'</main></html>'
    (root/'event-experiment-report.html').write_text(document)
    # Preserve original top-level reports under stable historical filenames before replacing entry points.
    for name in ('classification-report','weighted-classification-report','class-contrast-report'):
        current=root/(name+'.html');old=root/(name+'-actor-projection-superseded.html')
        if current.exists() and not old.exists():
            warning='<div style="padding:18px;background:#fff0cd;color:#5c4200;font:16px system-ui"><b>Superseded actor-projection experiment.</b> This page does not evaluate the corrected event representation. <a href="event-experiment-report.html">Open the corrected rerun</a>.</div>'
            text=current.read_text();pos=text.find('<body>');old.write_text(text[:pos+6]+warning+text[pos+6:] if pos>=0 else warning+text)
        current.write_text(document)
    index={'version':'event-graph-rerun-v2','runs':[{'window':r['configuration']['window_events'],'path':str(o.relative_to(root))} for o,r in zip(outs,results)],
           'supersedes':['actor-projection classification','actor-projection weighted classification','actor-projection class contrasts'],
           'report_code_sha256':file_sha(__file__)}
    save(root/'event-experiment-report.json',index)
    for name in ('classification-report','weighted-classification-report','class-contrast-report'):
        current=root/(name+'.json');old=root/(name+'-actor-projection-superseded.json')
        if current.exists() and not old.exists():old.write_text(current.read_text())
        save(current,index)


def main():
    p=argparse.ArgumentParser();p.add_argument('runs',nargs='+',type=Path);p.add_argument('--root',type=Path,default=Path.cwd());a=p.parse_args();outs=sorted([o.resolve() for o in a.runs],key=lambda o:read(o/'results.json')['configuration']['window_events'],reverse=True)
    for out in outs:
        if not (out/'class-contrasts.json').exists():
            meta,values=extract(out);analysis=write_contrasts(out,meta,values)
        else:analysis=read(out/'class-contrasts.json')
        plot(out,read(out/'results.json'),analysis)
    publish(a.root.resolve(),outs)
    print(json.dumps({'report':str(a.root/'event-experiment-report.html'),'complete':True}),flush=True)


if __name__=='__main__':main()
