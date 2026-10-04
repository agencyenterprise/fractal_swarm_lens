"""Exploratory follow-up: bind the unchanged message rubrics to their focal event."""
import argparse
import concurrent.futures
import copy
import html
import json
import random
from pathlib import Path
from .client import Client, MODEL
from .common import digest, file_sha, load_env, read, save
from .rubrics import questions
from .scoring_audit import features, comparison, reference_check

FOCUS=' Focal event = source.content.text only. Task/history are context; do not count their directives, checks or harm as focal content. Novelty compares source with history.'

def scoped_state(state):
    result=copy.deepcopy(state);result['instructions']+=FOCUS
    return result

def run(root,base,execute=False):
    source=read(base/'results.json');requests=read(base/'requests.json');q=questions('message')
    out=base/'scope-followup';out.mkdir(exist_ok=True)
    manifest=dict(version='explicit-focal-scope-v1',planned_after_initial_audit=True,
        reason='Initial audit showed higher verification scores on message text that contains only assertions/plans. Test focal attribution explicitly.',
        code_sha256=file_sha(Path(__file__)),questions=q,state_instruction_suffix=FOCUS,reference_hash=digest(source['protocol']),base_results_sha256=file_sha(base/'results.json'),
        selection='All seven message cases, both original and expanded history; no outcome-based subset selection.',
        limits=dict(requests=20,cost_usd=1),interpretation='Exploratory prompt ablation on the same cases, not independent validation or a production rubric revision.')
    if (out/'manifest.json').exists():assert read(out/'manifest.json')==manifest
    else:save(out/'manifest.json',manifest)
    jobs=[j for j in requests if j['scope']=='message'];assert len(jobs)==14
    for job in jobs:
        job['state']=scoped_state(job['state'])
        assert len(json.dumps(dict(model=MODEL,state=job['state'],questions=q),ensure_ascii=False).encode())<=32000
    save(out/'requests.json',jobs)
    if not execute:
        print(json.dumps(dict(prepared_calls=len(jobs),network_calls=0,request_file=str(out/'requests.json'))));return
    load_env(root/'../../.env');client=Client(out/'api-cache',max_requests=20,max_cost=1)
    random.Random(20261005).shuffle(jobs)
    def call(job):
        key,r=client.decide(job['state'],q);save(out/'decisions'/f"{job['case_id']}--{job['condition']}.json",dict(request_sha256=key,response=r['response']))
        return dict(case=job['case_id'],condition=job['condition'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for i,v in enumerate(pool.map(call,jobs)):print(json.dumps(dict(done=i+1,total=14,**v)),flush=True)
    results=[];counts={k:0 for k in ('original_short','original_expanded','scoped_short','scoped_expanded')};refs=0
    for c in source['cases']:
        if c['scope']!='message':continue
        scores=dict(original_short=c['scores']['fresh_original'],original_expanded=c['scores']['expanded_same_endpoints'])
        for key,condition in [('scoped_short','fresh_original'),('scoped_expanded','expanded_same_endpoints')]:
            scores[key]=features(read(out/'decisions'/f"{c['id']}--{condition}.json")['response'],'message')
        checks={k:reference_check(v,c['references']) for k,v in scores.items()};refs+=len(c['references'])
        for k,v in checks.items():counts[k]+=sum(x['compatible'] for x in v.values())
        results.append(dict(case_id=c['id'],scores=scores,reference_checks=checks))
    result=dict(manifest=manifest,cases=results,summary=dict(reference_measurements=refs,reference_compatible=counts,requests=client.requests,cache_hits=client.hits,
        billed_cost_usd=sum(read(p)['response']['usage']['cost'] for p in (out/'decisions').glob('*.json'))))
    save(out/'results.json',result)
    esc=lambda v:html.escape(str(v));rows=[]
    for c in results:
        for dim in ['novelty','instruction_content','verification_evidence']:
            rows.append('<tr><td>'+esc(c['case_id'])+'</td><td>'+esc(dim)+'</td>'+''.join(f'<td>{c["scores"][k][dim]["raw"]:.3f}<small>{esc(c["scores"][k][dim]["status"])}</small></td>' for k in counts)+'</tr>')
    block=f'''<!-- scope-followup-start --><section><h2>Exploratory follow-up: explicit focal-event scope</h2><p>After the initial results, all seven message cases were rescored with both short and expanded history. The question battery is unchanged; a suffix in the state's instruction field explicitly binds each judgment to <code>state.source.content.text</code>. Prior activity must not be counted as activity in the focal message. References were kept fixed.</p>
    <p>Compatible with the {refs} frozen message references: original short {counts['original_short']}; original expanded {counts['original_expanded']}; scoped short {counts['scoped_short']}; scoped expanded {counts['scoped_expanded']}. These are diagnostic agreement counts, not classifier accuracy.</p>
    <div class="scroll"><table><tr><th>Case</th><th>Dimension</th><th>Original short</th><th>Original expanded</th><th>Scoped short</th><th>Scoped expanded</th></tr>{''.join(rows)}</table></div>
    <p><a href="{esc(str((out/'results.json').relative_to(root)))}">Scope follow-up data and exact questions</a></p></section><!-- scope-followup-end -->'''
    p=root/'jev-scoring-audit-report.html';body=p.read_text();start=body.find('<!-- scope-followup-start -->')
    if start>=0:body=body[:start]+body[body.index('<!-- scope-followup-end -->')+len('<!-- scope-followup-end -->'):]
    body=body.replace('</main>',block+'</main>');p.write_text(body)
    index=read(root/'jev-scoring-audit-report.json');index['scope_followup']=result['summary'];save(root/'jev-scoring-audit-report.json',index)
    print(json.dumps(result['summary']),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('base',type=Path);p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--execute',action='store_true');a=p.parse_args();run(a.root.resolve(),a.base.resolve(),a.execute)
