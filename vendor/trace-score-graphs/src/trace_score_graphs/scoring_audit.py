"""Small, frozen evidence audit. Never mutates source scores or classifier runs."""
import argparse
import collections
import concurrent.futures
import copy
import datetime
import html
import json
import random
import re
from pathlib import Path

from .client import Client, MODEL, ENDPOINT, validate
from .common import digest, file_sha, load_env, read, save
from .evidence import clip
from .rubrics import questions, SPECS

VERSION='jev-evidence-audit-v1'
EVALUATION=re.compile(r'\nEvaluation\s*\n\s*\{')

def request_size(state,scope):
    return len(json.dumps(dict(model=MODEL,state=state,questions=questions(scope)),ensure_ascii=False).encode())

def pack(conv,event,budget=10000000):
    aliases={a:f'Actor-{i+1}' for i,a in enumerate(conv['actors'])}
    return dict(id=event['id'],position=event['position'],actor=aliases[event['actor']],kind=event['kind'],content=clip(event['content'],budget))

def expanded(conv,packet,anchors):
    """Change history only; keep task and the exact focal payload(s) unchanged."""
    state=copy.deepcopy(packet['state']);start=packet['source_position'];end=packet['available_at']
    keep=set(range(start));between=list(range(start+1,end))
    protected=set(anchors)&keep | set(range(max(0,start-4),start))
    def build():
        state['history']=[pack(conv,conv['events'][i]) for i in sorted(keep)]
        if packet['scope']=='interaction':
            state['intervening_events']=[pack(conv,conv['events'][i]) for i in between]
            state['omitted_intervening_events']=0
        return request_size(state,packet['scope'])
    while build()>31800:
        removable=sorted(keep-protected)
        if not removable:raise ValueError('Protected evidence exceeds request bound; redesign the case explicitly')
        keep.remove(removable[0])
    assert state['task']==packet['state']['task'] and state['source']==packet['state']['source']
    if packet['scope']=='interaction':assert state['response']==packet['state']['response']
    return state,dict(history_ids=[conv['events'][i]['id'] for i in sorted(keep)],
        omitted_history_ids=[conv['events'][i]['id'] for i in range(start) if i not in keep],
        intervening_ids=[conv['events'][i]['id'] for i in between],
        complete_prior_context=len(keep)==start,extra_context_clipping=False,request_bytes=request_size(state,packet['scope']))

def alternate(conv,packet,target):
    assert packet['source_position']<target<len(conv['events'])
    p=copy.deepcopy(packet);p['available_at']=target;p['response_event']=conv['events'][target]['id']
    p['state']['response']=pack(conv,conv['events'][target],9000)
    between=conv['events'][packet['source_position']+1:target]
    p['state']['intervening_events']=[pack(conv,e,250) for e in between[-4:]]
    p['state']['omitted_intervening_events']=max(0,len(between)-4)
    return p

def assert_state(state,source,target):
    assert state['source']['position']==source
    assert all(e['position']<source for e in state['history'])
    if target is not None:
        assert state['response']['position']==target
        assert all(source<e['position']<target for e in state['intervening_events'])
    # Inspection revealed benchmark evaluation trailers; never admit them here.
    texts=[state['source']['content']['text'],*[e['content']['text'] for e in state['history']]]
    if target is not None:texts += [state['response']['content']['text'],*[e['content']['text'] for e in state['intervening_events']]]
    assert not any(EVALUATION.search(s) for s in texts),'Benchmark evaluation entered scoring evidence'

def prepare(root,spec):
    source=root/'data/runs'/spec['source_run'];jobs=[];cases=[];hashes={}
    for case in spec['cases']:
        case=copy.deepcopy(case);cid=case['conversation_id'];path=source/'schemas'/f'{cid}.json';conv=read(path)
        evidence=source/'evidence'/f'{cid}.jsonl';packets=[json.loads(s) for s in evidence.read_text().splitlines()]
        candidates=[p for p in packets if p['source_position']==case['source_position'] and
            (p['scope']=='message' if case['response_position'] is None else p['scope']=='interaction' and p['available_at']==case['response_position'])]
        assert len(candidates)==1;packet=candidates[0];scope=packet['scope'];decisionpath=source/'decisions'/f"{packet['record_id']}.json"
        decision=read(decisionpath);cachepath=root/'data/api-cache'/f"{decision['request_sha256']}.json";cached=read(cachepath)
        assert cached['request']==dict(model=MODEL,state=packet['state'],questions=questions(scope))
        assert cached['endpoint']==ENDPOINT;validate(cached['response'],questions(scope))
        for p in (path,evidence,decisionpath,cachepath):hashes[str(p.relative_to(root))]=file_sha(p)
        case.update(scope=scope,record_id=packet['record_id'],source_event=packet['source_event'],response_event=packet['response_event'],
            source_excerpt=conv['events'][case['source_position']]['content'],
            response_excerpt=conv['events'][case['response_position']]['content'] if case['response_position'] is not None else None,
            original_request_sha256=decision['request_sha256'],cached_response=cached['response'],
            source_flags=conv['coverage']['flags'],task=conv['task'])
        variants=[('fresh_original',packet['state'],None)]
        state,coverage=expanded(conv,packet,case['context_anchors']);variants.append(('expanded_same_endpoints',state,coverage))
        if case['alternative']:
            alt=alternate(conv,packet,case['alternative']['response_position'])
            variants.append(('alternative_short',alt['state'],None))
            state,coverage=expanded(conv,alt,case['context_anchors']);variants.append(('alternative_expanded',state,coverage))
            case['alternative']['response_excerpt']=conv['events'][case['alternative']['response_position']]['content']
        for condition,state,coverage in variants:
            target=case['alternative']['response_position'] if condition.startswith('alternative_') else case['response_position']
            assert_state(state,case['source_position'],target)
            assert request_size(state,scope)<32000
            jobs.append(dict(case_id=case['id'],condition=condition,state=state,scope=scope,coverage=coverage))
        cases.append(case)
    # Scan exact evaluation markers, not all uses of the ordinary word "evaluation".
    contamination=[]
    for p in sorted((source/'schemas').glob('*.json')):
        conv=read(p)
        bad=[e['id'] for e in conv['events'] if EVALUATION.search(e['content'])]
        if bad:
            packets=[json.loads(s) for s in (source/'evidence'/f"{conv['conversation_id']}.jsonl").read_text().splitlines()]
            affected=[]
            for packet in packets:
                states=packet['state'];texts=[states['source']['content']['text'],*[e['content']['text'] for e in states['history']]]
                if packet['scope']=='interaction':texts += [states['response']['content']['text'],*[e['content']['text'] for e in states['intervening_events']]]
                if any(EVALUATION.search(s) for s in texts):affected.append(packet['record_id'])
            contamination.append(dict(conversation_id=conv['conversation_id'],framework=conv['adapter'],event_ids=bad,affected_original_packet_ids=sorted(set(affected))))
    return cases,jobs,hashes,contamination

def features(response,scope):
    a=response['answers'];out={}
    for layer,(s,_,_) in SPECS.items():
        if s!=scope:continue
        out[layer]=dict(raw=a[layer]['score'],status=a[layer+'__status']['choice'],
            probabilities=a[layer]['probabilities'],confidence=a[layer].get('confidence'))
    return out

def comparison(a,b):
    common=[k for k in a if a[k]['status']==b[k]['status']=='scorable']
    return dict(measurements=len(a),both_scorable=len(common),status_changes=sum(a[k]['status']!=b[k]['status'] for k in a),
        material_numeric_changes=sum(abs(b[k]['raw']-a[k]['raw'])>=.5 for k in common),
        absolute_raw_changes=[abs(b[k]['raw']-a[k]['raw']) for k in common])

def reference_check(scores,refs):
    out={}
    for name,ref in refs.items():
        s=scores[name];status=s['status']==ref['status'];distance=None
        if status and ref['status']=='scorable':distance=max(ref['range'][0]-s['raw'],0,s['raw']-ref['range'][1])
        out[name]=dict(status_matches=status,distance_to_range=distance,
            compatible=status and (distance is None or distance<=.5))
    return out

def report(root,out,spec,cases,jobs,hashes,contamination):
    rows=[];models=set();cost=0;comparisons=collections.defaultdict(list);refcounts=collections.Counter();reftrans=collections.Counter()
    for c in cases:
        scores={'cached_original':features(c['cached_response'],c['scope'])};variants={}
        for job in [j for j in jobs if j['case_id']==c['id']]:
            r=read(out/'decisions'/f"{c['id']}--{job['condition']}.json")
            scores[job['condition']]=features(r['response'],c['scope']);models.add(r['response']['model']);cost+=r['response']['usage']['cost'];variants[job['condition']]=dict(request_sha256=r['request_sha256'],coverage=job['coverage'],request_bytes=request_size(job['state'],job['scope']))
        for key,a,b in [('repeat','cached_original','fresh_original'),('context','fresh_original','expanded_same_endpoints')]:comparisons[key].append(comparison(scores[a],scores[b]))
        refs={key:reference_check(scores[key],c['references']) for key in ('cached_original','fresh_original','expanded_same_endpoints')}
        for key,checks in refs.items():
            refcounts[key]+=sum(v['compatible'] for v in checks.values())
        for name in c['references']:
            a=refs['fresh_original'][name]['compatible'];b=refs['expanded_same_endpoints'][name]['compatible']
            reftrans['improved' if b and not a else 'worsened' if a and not b else 'unchanged']+=1
        if c['alternative']:
            for condition in ('alternative_short','alternative_expanded'):refs[condition]=reference_check(scores[condition],c['alternative']['references'])
        rows.append({**{k:v for k,v in c.items() if k!='cached_response'},'scores':scores,'reference_checks':refs,'variants':variants})
    aggregate={}
    for k,vals in comparisons.items():
        changes=[a for v in vals for a in v['absolute_raw_changes']]
        aggregate[k]={n:sum(v[n] for v in vals) for n in ('measurements','both_scorable','status_changes','material_numeric_changes')}
        aggregate[k]['mean_absolute_raw_change']=sum(changes)/len(changes) if changes else None
    assert all(file_sha(root/p)==h for p,h in hashes.items()),'Original artifact changed'
    result=dict(version=VERSION,cases=rows,summary=dict(conversations=len({c['conversation_id'] for c in cases}),focal_records=len(cases),
        new_requests=len(jobs),unique_request_hashes=len({r['request_sha256'] for row in rows for r in row['variants'].values()}),
        billed_cost_usd=cost,models=sorted(models),comparisons=aggregate,reference_measurements=sum(len(c['references']) for c in cases),
        reference_compatible=dict(refcounts),reference_transitions=dict(reftrans),original_files_unchanged=True,
        contamination_conversations=len(contamination),contamination_original_packets=sum(len(x['affected_original_packet_ids']) for x in contamination)),
        source_contamination=contamination,protocol=spec)
    save(out/'results.json',result);save(root/'jev-scoring-audit-report.json',dict(run=str(out.relative_to(root)),summary=result['summary']))
    esc=lambda v:html.escape(str(v));parts=[]
    for row in rows:
        conditions=list(row['scores']);tr=[]
        for layer in row['scores']['cached_original']:
            ref=row['references'].get(layer);label=esc(ref['status']+(' '+str(ref['range']) if 'range' in ref else '')) if ref else 'Not adjudicated'
            cells=[]
            for co in conditions:
                v=row['scores'][co][layer];flag=row['reference_checks'].get(co,{}).get(layer,{}).get('compatible')
                cls='ok' if flag else 'miss' if flag is False else ''
                cells.append(f'<td class="{cls}">{v["raw"]:.3f}<small>{esc(v["status"])}</small></td>')
            tr.append(f'<tr><th>{esc(layer)}</th><td>{label}</td>'+''.join(cells)+'</tr>')
        alt=''
        if row['alternative']:
            a=row['alternative'];alt=f'<p><b>Alternative endpoint e{a["response_position"]:05d}:</b> {esc(a["relation"])}</p><p>{esc(a["rationale"])}</p><p>Separate reference ranges: {esc(a["references"])}</p><details><summary>Alternative response evidence</summary><pre>{esc(a["response_excerpt"])}</pre></details>'
        coverage=row['variants']['expanded_same_endpoints']['coverage']
        parts.append(f'<section><h2>{esc(row["framework"])} · {esc(row["id"])}</h2><p>{esc(row["conversation_id"])} · {esc(row["source_event"])} → {esc(row["response_event"] or "message judgment")}</p><p>{esc(row["rationale"])}</p><p>Expanded history: {len(coverage["history_ids"])} complete events; {len(coverage["omitted_history_ids"])} omitted; full prefix: {coverage["complete_prior_context"]}.</p><div class="scroll"><table><thead><tr><th>Dimension</th><th>Review reference</th>'+''.join('<th>'+esc(x.replace('_',' '))+'</th>' for x in conditions)+'</tr></thead><tbody>'+''.join(tr)+'</tbody></table></div>'+alt+f'<details><summary>Inspect focal text and original task</summary><h3>Task</h3><pre>{esc(row["task"])}</pre><h3>Source</h3><pre>{esc(row["source_excerpt"])}</pre><h3>Original response</h3><pre>{esc(row["response_excerpt"] or "—")}</pre></details></section>')
    s=result['summary'];context=s['comparisons']['context'];repeat=s['comparisons']['repeat']
    header=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Jev evidence audit · SwarmLens</title>
    <style>body{{margin:0;background:#f4f6f8;color:#192736;font:16px/1.55 system-ui}}main{{max-width:1240px;margin:auto;padding:40px 24px}}h1{{font-size:38px;line-height:1.15}}h2{{font-size:22px}}section,.card{{background:white;border:1px solid #d9e2e8;border-radius:14px;padding:24px;margin:22px 0}}.warning{{border-left:5px solid #b94d23;background:#fff3e9}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #dce4e8}}small{{display:block;font-size:11px;color:#576978}}.ok{{background:#e8f4ee}}.miss{{background:#fff0e7}}pre{{white-space:pre-wrap;word-break:break-word;background:#f6f8fa;padding:16px;font-size:13px}}summary{{cursor:pointer}}a{{color:#086e67}}</style><main>
    <p>SWARMLENS · RESEARCH / MEASUREMENT AUDIT</p><h1>Does better evidence change Jev's scores?</h1>
    <p>{s['conversations']} purposively selected conversations · {s['focal_records']} focal records · nine unchanged rubrics · {s['new_requests']} new calls · ${s['billed_cost_usd']:.6f}</p>
    <div class="card warning"><b>Preprocessing finding affecting earlier experiments:</b> {s['contamination_conversations']} AppWorld traces retain benchmark evaluation trailers; {s['contamination_original_packets']} original scoring packets include that material. Earlier classification results require a cleaned rerun. Audit focal inputs are verified free of these trailers.</div>
    <div class="card"><h2>Same endpoints, expanded history</h2><p>{context['material_numeric_changes']} / {context['both_scorable']} jointly scorable dimensions changed by at least 0.5 on the original 0–4 scale; {context['status_changes']} availability decisions changed. Mean absolute change: {context['mean_absolute_raw_change']:.3f}.</p>
    <p>Fresh identical-request control: {repeat['material_numeric_changes']} / {repeat['both_scorable']} material changes; {repeat['status_changes']} availability changes; mean absolute change {repeat['mean_absolute_raw_change']:.3f}.</p>
    <p>Compatible with frozen review ranges/statuses: cached {s['reference_compatible']['cached_original']}/{s['reference_measurements']}; fresh {s['reference_compatible']['fresh_original']}/{s['reference_measurements']}; expanded {s['reference_compatible']['expanded_same_endpoints']}/{s['reference_measurements']}. Fresh → expanded: {s['reference_transitions']['improved']} improved, {s['reference_transitions']['worsened']} worsened.</p></div>
    <p>References are one assistant's evidence-based judgments, fixed before inspecting these Jev outputs. They are not human gold labels or classifier accuracy. Compatibility allows the prespecified ±0.5 margin around ordinal ranges; availability must match. Green/orange cells indicate compatibility/disagreement with those references. Alternative endpoints use separate ranges.</p>
    <p>Only history changes in the primary contrast. Task text, focal source/response, rubrics and requested model remain fixed. All newly added events are complete and occur before the target. Long prefixes retain frozen evidence anchors and the latest four events; omitted IDs are recorded. No additional truncation, summaries, reference answers or MAST labels enter new inputs.</p>
    <p>Alternative endpoints are a separate diagnostic: an immediate intermediate response and a later completion may both be correctly scored differently. A changed score is not automatically a correction.</p>
    <p><a href="{esc(str((out/'results.json').relative_to(root)))}">Full results and references</a> · <a href="{esc(str((out/'manifest.json').relative_to(root)))}">Frozen protocol and source hashes</a> · <a href="event-experiment-report.html">Prior graph experiments</a></p>'''
    (root/'jev-scoring-audit-report.html').write_text(header+''.join(parts)+'</main></html>')
    return result

def run(root,specpath,execute):
    spec=read(specpath);code_hash=file_sha(Path(__file__));runid=digest(dict(spec=spec,code=code_hash))[:16]
    out=root/'data/scoring-audits'/runid;cases,jobs,hashes,contamination=prepare(root,spec)
    out.mkdir(parents=True,exist_ok=True)
    manifest=dict(protocol=spec,code_sha256=code_hash,source_hashes=hashes,requested_model=MODEL,planned_calls=len(jobs),
        limits=dict(requests=60,cost_usd=1,workers=4),reference_frozen_before_new_calls=True)
    if (out/'manifest.json').exists():assert read(out/'manifest.json')==manifest
    else:save(out/'manifest.json',manifest)
    save(out/'requests.json',jobs);save(out/'source-contamination.json',contamination)
    print(json.dumps(dict(run=str(out),planned_calls=len(jobs),contamination_conversations=len(contamination),
        expanded_contexts=[dict(case=j['case_id'],**j['coverage']) for j in jobs if j['condition']=='expanded_same_endpoints'])),flush=True)
    if not execute:return
    load_env(root/'../../.env');clients={name:Client(out/'api-cache'/name,max_requests=60,max_cost=1) for name in {j['condition'] for j in jobs}}
    # Cross-condition budget is enforced before scheduling; at most four calls are in flight.
    random.Random(20261004).shuffle(jobs)
    failures=[];done=0
    def call(job):
        client=clients[job['condition']]
        if sum(c.requests for c in clients.values())>=60 or sum(c.cost for c in clients.values())>=1:raise RuntimeError('Audit budget stop')
        key,r=client.decide(job['state'],questions(job['scope']))
        save(out/'decisions'/f"{job['case_id']}--{job['condition']}.json",dict(request_sha256=key,response=r['response']))
        return key
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending={pool.submit(call,j):j for j in jobs}
        for f in concurrent.futures.as_completed(pending):
            j=pending[f];done+=1
            try:f.result()
            except Exception as e:failures.append(dict(case=j['case_id'],condition=j['condition'],error=str(e)))
            print(json.dumps(dict(done=done,total=len(jobs),failures=len(failures),cost_usd=sum(c.cost for c in clients.values()))),flush=True)
    save(out/'execution.json',dict(failures=failures,requests=sum(c.requests for c in clients.values()),cache_hits=sum(c.hits for c in clients.values()),cost_usd=sum(c.cost for c in clients.values())))
    if failures:raise RuntimeError('Audit has failed calls; inspect execution.json')
    result=report(root,out,spec,cases,jobs,hashes,contamination);print(json.dumps(result['summary']),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--spec',type=Path,default=Path('experiments/jev-evidence-audit-v1.json'));p.add_argument('--execute',action='store_true');a=p.parse_args();run(a.root.resolve(),a.spec,a.execute)
