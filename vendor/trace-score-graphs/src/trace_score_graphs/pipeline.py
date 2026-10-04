import collections
import concurrent.futures
import json
import time
from pathlib import Path
from . import parsers
from .common import digest, jsonl, read, save
from .evidence import POLICY, records
from .rubrics import LAYERS, SPECS, manifest, questions
from .client import Client


def prepare(root):
    root = Path(root)
    selection = read(root / 'selection.json')
    config = {'selection_sha256': digest(selection), 'rubric': manifest(), 'evidence': POLICY,
              'parser_sha256': digest(Path(parsers.__file__).read_text())}
    run_id = digest(config)[:16]
    run = root / 'runs' / run_id
    save(run / 'manifest.json', {**config, 'run_id': run_id})
    summary = {'conversations': 0, 'events': 0, 'message_packets': 0, 'interaction_packets': 0,
               'frameworks': {}, 'errors': [], 'truncated_packets': 0, 'unconfirmed_interactions': 0}
    for item in selection['conversations']:
        cid = item['conversation_id']
        try:
            conversation = parsers.normalize(read(root / 'selected' / (cid + '.json')), cid)
            packets = records(conversation)
            save(run / 'schemas' / (cid + '.json'), conversation)
            jsonl(run / 'evidence' / (cid + '.jsonl'), packets)
            summary['conversations'] += 1
            summary['events'] += len(conversation['events'])
            stats = summary['frameworks'].setdefault(conversation['adapter'], {'conversations': 0, 'events': 0})
            stats['conversations'] += 1
            stats['events'] += len(conversation['events'])
            for p in packets:
                summary[p['scope'] + '_packets'] += 1
                if '"truncated": true' in json.dumps(p['state']):
                    summary['truncated_packets'] += 1
                if p['delivery_basis'] == 'temporal_candidate_unconfirmed_exposure':
                    summary['unconfirmed_interactions'] += 1
        except (ValueError, KeyError) as error:
            summary['errors'].append({'conversation_id': cid, 'error': str(error)})
    save(run / 'preprocessing.json', summary)
    save(root / 'active-run.json', {'run_id': run_id})
    return run, summary


def current_run(root):
    root = Path(root)
    return root / 'runs' / read(root / 'active-run.json')['run_id']


def packets(run):
    for path in sorted((run / 'evidence').glob('*.jsonl')):
        for line in path.read_text().splitlines():
            yield json.loads(line)


def measure(packet, client, run):
    path = run / 'decisions' / (packet['record_id'] + '.json')
    # Client always validates the original request against its content-addressed cache.
    request_key, result = client.decide(packet['state'], questions(packet['scope']))
    answer = {'record_id': packet['record_id'], 'conversation_id': packet['conversation_id'],
              'scope': packet['scope'], 'source_event': packet['source_event'],
              'response_event': packet['response_event'], 'available_at': packet['available_at'],
              'source_position': packet['source_position'], 'delivery_basis': packet['delivery_basis'],
              'instrument_sha256': packet['instrument_sha256'], 'request_sha256': request_key,
              'model': result['response']['model'], 'provider': result['response'].get('provider'),
              'answers': result['response']['answers']}
    save(path, answer)
    return answer


def score(root, model, workers=8, max_requests=50000, max_cost=25.0, limit=None):
    run = current_run(root)
    if read(run / 'preprocessing.json')['errors']:
        raise ValueError('Resolve preprocessing errors before scoring the cohort')
    client = Client(Path(root) / 'api-cache', model=model, max_requests=max_requests, max_cost=max_cost)
    work = list(packets(run))
    if limit is not None:
        work = work[:limit]
    failures, done = [], 0
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(measure, p, client, run): p for p in work}
        for future in concurrent.futures.as_completed(pending):
            packet = pending[future]
            try:
                future.result()
            except Exception as error:
                failures.append({'record_id': packet['record_id'], 'conversation_id': packet['conversation_id'],
                                 'error': str(error)})
            done += 1
            if done % 100 == 0 or done == len(work):
                progress = {'done': done, 'total': len(work), 'failures': len(failures),
                            'requests': client.requests, 'cache_hits': client.hits,
                            'cost_usd_this_invocation': round(client.cost, 6),
                            'elapsed_seconds': round(time.time() - started)}
                save(run / 'progress.json', progress)
                print(json.dumps(progress), flush=True)
    report = {'requested_packets': len(work), 'completed_packets': done - len(failures),
              'failures': failures, 'requests': client.requests, 'cache_hits': client.hits,
              'cost_usd_this_invocation': client.cost, 'model_requested': model,
              'elapsed_seconds': time.time() - started}
    save(run / 'scoring.json', report)
    return report


def export(root):
    run = current_run(root)
    summary = {'conversations': 0, 'layers': {s: collections.Counter() for s in LAYERS},
               'missing_packets': 0, 'completed_packets': 0, 'graphs': 0,
               'cost_usd_unique_requests': 0.0, 'resolved_models': set(), 'requests': set()}
    for path in sorted((run / 'schemas').glob('*.json')):
        conv = read(path)
        cid = conv['conversation_id']
        layer_rows = {s: [] for s in (*LAYERS, 'compliance_mode')}
        for line in (run / 'evidence' / (cid + '.jsonl')).read_text().splitlines():
            packet = json.loads(line)
            decision_path = run / 'decisions' / (packet['record_id'] + '.json')
            decision = read(decision_path) if decision_path.exists() else None
            if decision:
                summary['completed_packets'] += 1
                summary['requests'].add(decision['request_sha256'])
                summary['resolved_models'].add(decision['model'])
            else:
                summary['missing_packets'] += 1
            for name in LAYERS:
                if SPECS[name][0] != packet['scope']:
                    continue
                answer = decision['answers'][name] if decision else None
                status_answer = decision['answers'][name + '__status'] if decision else None
                status = status_answer['choice'] if status_answer else 'missing_decision'
                weight = answer['score'] / 4 if answer and status == 'scorable' else None
                if name == 'agreement' and weight is not None:
                    weight = weight * 2 - 1
                row = {k: packet[k] for k in ('record_id', 'conversation_id', 'source_event',
                    'response_event', 'source_position', 'available_at', 'delivery_basis', 'instrument_sha256')}
                row.update({'score_name': name, 'status': status, 'weight': weight,
                            'raw_answer': answer, 'status_answer': status_answer,
                            'request_sha256': decision['request_sha256'] if decision else None,
                            'model': decision['model'] if decision else None})
                layer_rows[name].append(row)
                summary['layers'][name][status] += 1
            if decision and packet['scope'] == 'interaction':
                layer_rows['compliance_mode'].append({'record_id': packet['record_id'],
                    'source_event': packet['source_event'], 'response_event': packet['response_event'],
                    'available_at': packet['available_at'], 'raw_answer': decision['answers']['compliance_mode'],
                    'request_sha256': decision['request_sha256']})
        nodes = [{k: e[k] for k in ('id', 'position', 'actor', 'kind', 'source_span')} for e in conv['events']]
        temporal = [{'source': a['id'], 'target': b['id'], 'kind': 'temporal_next'}
                    for a, b in zip(conv['events'], conv['events'][1:])]
        for name, rows in layer_rows.items():
            jsonl(run / 'scores' / name / (cid + '.jsonl'), rows)
            if name == 'compliance_mode':
                continue
            scope = SPECS[name][0]
            graph = {'schema_version': 'score-graph-v1', 'conversation_id': cid, 'score_name': name,
                'scope': scope, 'directed': True, 'multigraph': True,
                'nodes': nodes, 'temporal_edges': temporal,
                'node_measurements': rows if scope == 'message' else [],
                'edge_measurements': [dict(r, source=r['source_event'], target=r['response_event'])
                                      for r in rows] if scope == 'interaction' else [],
                'weight_semantics': 'rubric expected level, signed for agreement; null means unavailable',
                'evidence_file': f'../../evidence/{cid}.jsonl',
                'conversation_file': f'../../schemas/{cid}.json',
                'aggregation': None}
            save(run / 'graphs' / name / (cid + '.json'), graph)
            summary['graphs'] += 1
        summary['conversations'] += 1
    for key in summary['requests']:
        cached = read(Path(root) / 'api-cache' / (key + '.json'))
        summary['cost_usd_unique_requests'] += cached['response']['usage']['cost']
    summary['unique_api_requests'] = len(summary.pop('requests'))
    summary['resolved_models'] = sorted(summary['resolved_models'])
    summary['complete'] = summary['missing_packets'] == 0 and summary['conversations'] == 300
    save(run / 'results.json', summary)
    return summary
