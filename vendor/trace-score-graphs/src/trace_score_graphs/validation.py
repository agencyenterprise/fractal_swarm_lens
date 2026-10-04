"""Cross-file experiment integrity checks, independent of model judgments."""
import collections
import json
from pathlib import Path
from .common import read, save, text_sha
from .pipeline import current_run
from .rubrics import LAYERS, SPECS


def validate_conversation(conversation, raw):
    assert conversation['raw_sha256'] == text_sha(raw), 'Raw trajectory hash mismatch'
    events = conversation['events']
    assert len({e['id'] for e in events}) == len(events), 'Duplicate event IDs'
    previous = -1
    for i, event in enumerate(events):
        assert event['position'] == i, 'Non-contiguous event positions'
        assert event['actor'] in conversation['actors'], 'Unknown actor'
        assert all(r in conversation['actors'] for r in event['recipients']), 'Unknown recipient'
        span = event['source_span']
        assert 0 <= span['start'] < span['end'] <= len(raw), 'Invalid original source span'
        assert span['start'] >= previous, 'Source order changed'
        assert event['content'].strip(), 'Empty normalized event'
        previous = span['start']


def audit(root):
    root = Path(root)
    selection = read(root / 'selection.json')
    run = current_run(root)
    expected = selection['per_group'] * 15
    items = selection['conversations']
    checks, errors, measured = collections.Counter(), [], collections.Counter()
    assert len(items) == expected
    assert len({i['trajectory_sha256'] for i in items}) == expected
    assert set(collections.Counter(i['cohort'] for i in items).values()) == {selection['per_group']}
    for item in items:
        cid = item['conversation_id']
        try:
            raw = read(root / 'selected' / (cid + '.json'))
            assert text_sha(raw['trace']['trajectory']) == item['trajectory_sha256']
            if item['cohort'] == 'no_issue':
                assert all(all(v == 0 for v in x['labels'].values()) for x in item['all_source_annotations'])
            else:
                assert raw['mast_annotation'][item['cohort']] == 1
            conv = read(run / 'schemas' / (cid + '.json'))
            validate_conversation(conv, raw['trace']['trajectory'])
            positions = {e['id']: e['position'] for e in conv['events']}
            packet_list = [json.loads(s) for s in (run / 'evidence' / (cid + '.jsonl')).read_text().splitlines()]
            counts = collections.Counter(p['scope'] for p in packet_list)
            for p in packet_list:
                assert 'mast_annotation' not in p['state'] and 'cohort' not in p['state']
                assert p['source_position'] == positions[p['source_event']]
                if p['scope'] == 'message':
                    assert 'response' not in p['state']
                    assert p['available_at'] == p['source_position']
                else:
                    assert p['available_at'] == positions[p['response_event']] > p['source_position']
                for h in p['state']['history']:
                    assert h['position'] < p['source_position']
            for name in LAYERS:
                graph = read(run / 'graphs' / name / (cid + '.json'))
                assert graph['score_name'] == name and graph['conversation_id'] == cid
                assert graph['directed'] and graph['aggregation'] is None
                assert {n['id'] for n in graph['nodes']} == set(positions)
                scope = SPECS[name][0]
                rows = graph['node_measurements'] if scope == 'message' else graph['edge_measurements']
                saved = [json.loads(s) for s in (run / 'scores' / name / (cid + '.jsonl')).read_text().splitlines()]
                assert len(rows) == len(saved) == counts[scope], 'Missing per-layer observations'
                for observation in rows:
                    assert observation['source_event'] in positions
                    weight = observation['weight']
                    if observation['status'] == 'scorable':
                        assert weight is not None and (-1 if name == 'agreement' else 0) <= weight <= 1
                    else:
                        assert weight is None, 'Unavailable score converted into a number'
                    assert observation['status'] != 'missing_decision', 'API request unfinished'
                    assert observation['raw_answer']['probabilities'], 'Distribution lost'
                    assert observation['model'] == 'typesafe/jev-1.13-20260917', 'Unexpected model version'
                    assert (root / 'api-cache' / (observation['request_sha256'] + '.json')).exists()
                measured[name] += len(rows)
                checks['graphs'] += 1
            checks['conversations'] += 1
            checks['evidence_packets'] += len(packet_list)
        except (AssertionError, ValueError, OSError, KeyError) as error:
            errors.append({'conversation_id': cid, 'error': str(error)})
    report = {'passed': not errors and checks['conversations'] == expected,
              'checks': dict(checks), 'measurements_by_score': dict(measured),
              'total_numeric_measurements': sum(measured.values()), 'errors': errors}
    save(run / 'integrity-audit.json', report)
    return report
