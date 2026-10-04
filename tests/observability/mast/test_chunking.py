import json

import pytest
from fastapi.testclient import TestClient

from swarm_lens import Fact
from swarm_lens.application.framework import IterableSource
from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.observability.mast import MastPlugin
from swarm_lens.observability.mast.chunking import (
    evaluate_chunks, parse_reconciliation, reconciliation_prompt, map_budget,
)
from swarm_lens.observability.mast.method import assets, history_document
from swarm_lens.observability.mast.trace import decode_trace
from swarm_lens.observability.mast.service import MastJobs, MastService
from swarm_lens.web.api import create_app
from swarm_lens.web.plugins.mast import mast_extension


class ChunkJudge:
    def __init__(self, limit=10**9, positive=False, fail_chunk=None, unknown_chunk=None):
        self.limit, self.positive = limit, positive
        self.fail_chunk, self.unknown_chunk = fail_chunk, unknown_chunk
        self.prompts, self.maps = [], 0

    def describe(self):
        return {'ready': True, 'model': 'offline-chunk-judge'}

    def input_budget(self, prompt):
        return {'can_analyze': len(prompt) <= self.limit, 'estimated_input_tokens': len(prompt),
                'input_token_limit': self.limit, 'reason': 'Synthetic token budget' if len(prompt) > self.limit else None}

    def complete(self, prompt):
        assert self.input_budget(prompt)['can_analyze'], 'Every provider request must fit'
        self.prompts.append(prompt)
        if prompt.startswith('Reconcile the chronological'):
            reports = json.loads(prompt.split('\nREPORTS:\n', 1)[1])
            first, last = reports[0]['boundary_events'][0], reports[-1]['boundary_events'][-1]
            occurrence = {'start_event_id': first['event_id'], 'end_event_id': last['event_id'],
                          'supporting_event_ids': [first['event_id']], 'counterevidence_event_ids': [],
                          'explanation': 'Evidence spans two chunks.'}
            payload = {'summary': 'Later clarification resolves the apparent repetition.', 'task_completed': True,
                       'labels': [{'code': c['code'], 'present': self.positive and c['code'] == '1.3',
                                   'explanation': 'Judged against the entire reported context.',
                                   'occurrences': [occurrence, occurrence] if self.positive and c['code'] == '1.3' else []}
                                  for c in assets()['categories']]}
            text = json.dumps(payload)
        elif prompt.startswith('Explain the supplied MAST judgments'):
            doc = decode_trace(json.loads(prompt.split('\nSAVED TRACE:\n', 1)[1]))
            event = doc['events'][0]
            occurrence = {'start_event_id': event['event_id'], 'end_event_id': event['event_id'],
                          'supporting_event_ids': [event['event_id']], 'counterevidence_event_ids': [],
                          'explanation': 'Possible repetition in this partial chunk.'}
            text = json.dumps({'traits': [{'code': c['code'], 'explanation': 'Partial chunk evidence.',
                                          'occurrences': [occurrence] if c['code'] == '1.3' else []}
                                         for c in assets()['categories']]})
        else:
            self.maps += 1
            if self.maps == self.fail_chunk:
                raise RuntimeError('PRIVATE_PROVIDER_DETAILS')
            text = 'A. Chunk-local apparent repetition.\nB. no\nC.\n' + '\n'.join(
                f"{c['code']} {'yes' if c['code'] == '1.3' else 'no'}" for c in assets()['categories'])
            if self.maps == self.unknown_chunk:
                text = text.replace('1.1 no', '1.1 unclear')
        return {'text': text, 'model': 'offline-chunk-judge', 'finish_reason': 'stop', 'usage': {'total_tokens': 1}}


def recording(framework, count=18, width=1800):
    branch = framework.create_run('Chunk test')
    facts = [Fact('environment.updated', {'goal': 'Resolve apparent repetition with later evidence'}, '2026-10-04T00:00:00Z')]
    for i in range(count):
        facts.append(Fact('observation.recorded', {'type': 'transcript', 'content': f'Event {i}: ' + str(i) * width + ' café 🐈'}, '2026-10-04T00:00:00Z'))
    framework.ingest(branch.id, IterableSource(facts))
    return framework.store.branch(branch.id)


def make_service(framework, tmp_path, judge=None, limit=18_000, workers=1):
    return MastService(framework, MastPlugin(judge or ChunkJudge(), max_trace_characters=limit, workers=workers),
                       MastJobs(tmp_path / 'jobs.sqlite'), FileArtifacts(tmp_path / 'artifacts'))


def test_chunks_cover_every_event_and_overlap_without_future_leakage(framework):
    branch = recording(framework)
    history = framework.history(branch.id, branch.head - 1)
    plugin = MastPlugin(ChunkJudge(), max_trace_characters=14_000)
    prepared = plugin.prepare_analysis(history, {'completeness': 'complete'})
    assert prepared['summary']['strategy'] == 'chunked'
    assert prepared['summary']['can_analyze']
    observed = {}
    overlap = 0
    for chunk in prepared['chunks']:
        doc = decode_trace(json.loads(chunk['trace']))
        assert doc['trace_completeness'] == 'partial'
        assert doc['chunk_context']['source_completeness'] == 'complete'
        assert map_budget(plugin, chunk['trace'], chunk['prompt'])['can_analyze']
        overlap += chunk['overlap_events']
        for event in doc['events']:
            assert event['position'] <= branch.head - 1
            if event['event_id'] in observed:
                assert observed[event['event_id']] == event
            observed[event['event_id']] = event
    assert overlap > 0
    assert list(observed.values()) == history_document(history, 'complete')['events']
    assert not plugin.judge.prompts


def test_giant_single_event_is_split_losslessly_with_unicode_and_original_id(framework):
    branch = recording(framework, count=1, width=35_000)
    history = framework.history(branch.id)
    plugin = MastPlugin(ChunkJudge(), max_trace_characters=8000)
    prepared = plugin.prepare_analysis(history, {})
    slices = {}
    for chunk in prepared['chunks']:
        for event in decode_trace(json.loads(chunk['trace']))['events']:
            fragment = event['data'].get('mast_fragment')
            if fragment:
                assert event['event_id'] == history[-1].id
                slices[fragment['start']] = fragment
    position, parts = 0, []
    for start, fragment in sorted(slices.items()):
        assert start == position
        assert len(fragment['text']) == fragment['end'] - start
        position = fragment['end']
        parts.append(fragment['text'])
    assert json.loads(''.join(parts)) == history[-1].data
    assert position == max(f['total_characters'] for f in slices.values())


def test_token_budget_can_trigger_chunking_below_character_guard(framework):
    branch = recording(framework, count=35)
    judge = ChunkJudge(limit=120_000)
    plugin = MastPlugin(judge, max_trace_characters=4_000_000)
    prepared = plugin.prepare_analysis(framework.history(branch.id), {})
    assert prepared['summary']['prepared_characters'] < plugin.max_trace_characters
    assert prepared['summary']['strategy'] == 'chunked'
    assert all(judge.input_budget(c['prompt'])['can_analyze'] for c in prepared['chunks'])
    assert not judge.prompts


def test_api_reconciles_instead_of_or_and_preserves_stage_artifacts(framework, tmp_path):
    branch = recording(framework)
    service = make_service(framework, tmp_path)
    with TestClient(create_app(framework, service.artifacts, extensions=(mast_extension(service),))) as client:
        payload = {'branch_id': branch.id, 'cursor': branch.head, 'completeness': 'complete'}
        preview = client.post('/api/plugins/mast/preview', json=payload).json()
        assert preview['strategy'] == 'chunked' and preview['chunk_count'] > 1
        assert not service.plugin.judge.prompts and not service.jobs.list(branch.id)
        response = client.post('/api/plugins/mast/analyses', json=payload)
        assert response.status_code == 202
        job = client.get('/api/plugins/mast/analyses/' + response.json()['id']).json()
        assert job['status'] == 'completed', job
        output = job['analysis']['output']
        assert all(c['present'] is False for c in output['labels'])
        assert output['task_completed'] is True  # Not the last chunk's local "no".
        assert job['progress']['phase'] == 'finished'
        assert output['chunking']['chunk_count'] == preview['chunk_count']
        for stage in job['stages']:
            saved = client.get('/api/artifacts/' + stage['artifact']).json()
            assert saved['prompt'] and saved['trace'] and saved['output']['raw_response']
            if stage['phase'] == 'chunk':
                assert next(c for c in saved['output']['labels'] if c['code'] == '1.3')['present'] is True
        prompts = len(service.plugin.judge.prompts)
        service.execute(job['id'])
        assert len(service.plugin.judge.prompts) == prompts
        detail = client.get(f"/api/plugins/mast/analyses/{job['id']}/traits/1.3").json()
        assert detail['present'] is False and detail['occurrences'] == []


def test_cross_chunk_evidence_is_deduplicated_and_resolves_in_full_prefix(framework, tmp_path):
    branch = recording(framework)
    service = make_service(framework, tmp_path, ChunkJudge(positive=True))
    job = service.submit(branch.id, branch.head, 'partial')
    service.execute(job['id'])
    result = service.jobs.get(job['id'])
    assert result['status'] == 'completed'
    from swarm_lens.web.plugins.mast.details import present_trait_details
    detail = present_trait_details(service, job['id'], '1.3')
    assert len(detail['occurrences']) == 1
    assert detail['occurrences'][0]['start_position'] == 1
    assert detail['occurrences'][0]['end_position'] == branch.head
    assert len(detail['occurrences'][0]['events']) == branch.head


def test_reconciliation_is_hierarchical_when_reports_do_not_fit(framework):
    branch = recording(framework, count=75, width=2400)
    plugin = MastPlugin(ChunkJudge(), max_trace_characters=18_000)
    prepared = plugin.prepare_analysis(framework.history(branch.id), {})
    progress = []
    result = evaluate_chunks(plugin, prepared['trace'], prepared['chunks'], lambda **p: progress.append(p))
    assert result['chunking']['reconciliation_steps'] > 1
    assert result['parse_status'] == 'complete'
    assert any(p.get('level', 0) > 0 for p in progress)
    json.dumps(result)  # Direct-library results must not contain a circular stage reference.


def test_failed_later_chunk_keeps_prior_results_without_combined_verdict(framework, tmp_path):
    branch = recording(framework)
    service = make_service(framework, tmp_path, ChunkJudge(fail_chunk=2))
    job = service.submit(branch.id, branch.head, 'unknown')
    service.execute(job['id'])
    result = service.jobs.get(job['id'])
    assert result['status'] == 'failed' and 'analysis' not in result
    assert len(result['stages']) == 1
    assert service.artifacts.get(result['stages'][0]['artifact'])
    assert 'PRIVATE_PROVIDER_DETAILS' not in json.dumps(result)
    assert not service.framework.store.analyses(branch.id, branch.head)
    service.jobs.recover_interrupted()
    assert service.jobs.get(job['id'])['stages'] == result['stages']


def test_unknown_chunk_cannot_turn_into_global_absence(framework, tmp_path):
    branch = recording(framework)
    service = make_service(framework, tmp_path, ChunkJudge(unknown_chunk=1))
    job = service.submit(branch.id, branch.head, 'unknown')
    service.execute(job['id'])
    result = service.jobs.get(job['id'])
    assert result['status'] == 'needs_review'
    assert next(c for c in result['analysis']['output']['labels'] if c['code'] == '1.1')['present'] is None


@pytest.mark.parametrize('response', [
    {'text': '{broken', 'finish_reason': 'stop'},
    {'text': '{}', 'finish_reason': 'length'},
])
def test_invalid_final_output_is_unknown_not_negative(response):
    result = parse_reconciliation(response, [], [], 'prompt', 'hash')
    assert result['parse_status'] == 'needs_review'
    assert result['task_completed'] is None
    assert all(c['present'] is None for c in result['labels'])


def test_saved_chunk_plan_excludes_later_branch_changes(framework, tmp_path):
    branch = recording(framework)
    service = make_service(framework, tmp_path)
    job = service.submit(branch.id, branch.head, 'partial')
    framework.intervene(branch.id, 'environment.updated', {'goal': 'FUTURE_PRIVATE_SENTINEL'}, branch.head)
    service.execute(job['id'])
    assert service.jobs.get(job['id'])['status'] == 'completed'
    assert all('FUTURE_PRIVATE_SENTINEL' not in p for p in service.plugin.judge.prompts)


def test_reconciliation_rejects_ids_not_supplied_by_its_inputs():
    events = [{'event_id': f'e{i}', 'position': i} for i in range(1, 4)]
    labels = [{'code': c['code'], 'present': False} for c in assets()['categories']]
    reports = [{'boundary_events': events[:2], 'evidence': {}, 'labels': labels,
                'parse_status': 'complete', 'warnings': []}]
    payload = {'summary': 'Invented reference.', 'task_completed': True, 'labels': [
        {'code': c['code'], 'present': c['code'] == '1.3', 'explanation': 'A claim.', 'occurrences': [{
            'start_event_id': 'e1', 'end_event_id': 'e3', 'supporting_event_ids': ['e3'],
            'counterevidence_event_ids': [], 'explanation': 'e3 exists but was never supplied.'
        }] if c['code'] == '1.3' else []} for c in assets()['categories']]}
    result = parse_reconciliation({'text': json.dumps(payload), 'finish_reason': 'stop'}, reports, events, 'prompt', 'hash')
    assert result['parse_status'] == 'needs_review'
    assert next(c for c in result['labels'] if c['code'] == '1.3')['present'] is None
    assert not result['evidence']['traits']['1.3']['occurrences']


def test_restart_keeps_completed_chunk_stages_without_replaying_calls(framework, tmp_path):
    branch = recording(framework)
    service = make_service(framework, tmp_path)
    job = service.submit(branch.id, branch.head, 'unknown')
    record = service.jobs.claim(job['id'])
    digest = service.artifacts.put(b'{"saved":"chunk result"}')
    record['stages'] = [{'phase': 'chunk', 'index': 1, 'artifact': digest}]
    service.jobs.update(record)
    service.jobs.recover_interrupted()
    service.execute(job['id'])
    interrupted = service.jobs.get(job['id'])
    assert interrupted['status'] == 'interrupted'
    assert interrupted['stages'] == record['stages']
    assert service.artifacts.get(digest)
    assert not service.plugin.judge.prompts


def test_parallel_chunks_save_out_of_order_but_reconcile_in_order(framework, tmp_path, monkeypatch):
    from threading import Barrier, Event, get_ident
    branch = recording(framework, count=35)
    service = make_service(framework, tmp_path, workers=3)
    job = service.submit(branch.id, branch.head, 'complete')
    chunks = json.loads(service.artifacts.get(job['chunk_plan_artifact']))
    assert len(chunks) > 3
    indices = {c['prompt']: c['index'] for c in chunks}
    rendezvous, release_first = Barrier(3), Event()
    original_evaluate, original_update = service.plugin.evaluate, service.jobs.update
    saves, progress, coordinator_threads = [], [], set()

    def evaluate(trace, prompt):
        index = indices[prompt]
        if index <= 3:
            rendezvous.wait(timeout=5)  # Sequential execution cannot pass this barrier.
        if index == 1:
            assert release_first.wait(5), 'Chunk 2 must be saved while chunk 1 is still running'
        return original_evaluate(trace, prompt)

    def update(record):
        coordinator_threads.add(get_ident())
        original_update(record)
        progress.append(json.loads(json.dumps(record.get('progress', {}))))
        for stage in record.get('stages', []):
            if stage['phase'] == 'chunk' and stage['index'] not in saves:
                saves.append(stage['index'])
        if 2 in saves:
            assert service.jobs.get(job['id'])['stages']
            release_first.set()

    monkeypatch.setattr(service.plugin, 'evaluate', evaluate)
    monkeypatch.setattr(service.jobs, 'update', update)
    service.execute(job['id'])
    result = service.jobs.get(job['id'])
    assert result['status'] == 'completed', result.get('error')
    assert saves.index(2) < saves.index(1)
    assert coordinator_threads == {get_ident()}
    assert any(len(p.get('active_chunks', [])) == 3 for p in progress)
    assert all(len(p.get('active_chunks', [])) <= 3 for p in progress)
    assert [s['index'] for s in result['stages'] if s['phase'] == 'chunk'] == list(range(1, len(chunks) + 1))
    reconciliations = [p for p in service.plugin.judge.prompts if p.startswith('Reconcile the chronological')]
    for prompt in reconciliations:
        reports = json.loads(prompt.split('\nREPORTS:\n', 1)[1])
        order = [i for r in reports for i in r['source_chunks']]
        assert order == sorted(order)
    assert order == [c['index'] for c in chunks]  # Final reconciliation covers every chunk.
    assert result['analysis']['output']['chunking']['workers'] == 3


def test_failure_stops_new_chunks_and_saves_successful_inflight_work(framework, tmp_path, monkeypatch):
    from threading import Barrier, Event, Lock
    branch = recording(framework, count=35)
    service = make_service(framework, tmp_path, workers=2)
    job = service.submit(branch.id, branch.head, 'complete')
    chunks = json.loads(service.artifacts.get(job['chunk_plan_artifact']))
    assert len(chunks) > 2
    indices = {c['prompt']: c['index'] for c in chunks}
    rendezvous, draining, lock = Barrier(2), Event(), Lock()
    started = []
    original_evaluate, original_update = service.plugin.evaluate, service.jobs.update

    def evaluate(trace, prompt):
        index = indices[prompt]
        with lock:
            started.append(index)
        rendezvous.wait(timeout=5)
        if index == 1:
            raise RuntimeError('PRIVATE_PROVIDER_DETAILS')
        assert draining.wait(5), 'The coordinator must enter drain mode'
        return original_evaluate(trace, prompt)

    def update(record):
        original_update(record)
        if record.get('progress', {}).get('stopping'):
            draining.set()

    monkeypatch.setattr(service.plugin, 'evaluate', evaluate)
    monkeypatch.setattr(service.jobs, 'update', update)
    service.execute(job['id'])
    result = service.jobs.get(job['id'])
    assert result['status'] == 'failed' and 'analysis' not in result
    assert sorted(started) == [1, 2]
    assert [s['index'] for s in result['stages']] == [2]
    assert result['progress']['completed_chunks'] == 1
    assert result['progress']['failed_chunks'] == [1]
    assert result['progress']['active_chunks'] == []
    assert not any(p.startswith('Reconcile the chronological') for p in service.plugin.judge.prompts)
    assert 'PRIVATE_PROVIDER_DETAILS' not in json.dumps(result)


def test_provider_concurrency_limit_is_shared_across_jobs(framework, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    import time

    class ConcurrentJudge(ChunkJudge):
        def __init__(self):
            super().__init__()
            self.lock, self.active, self.peak = Lock(), 0, 0

        def complete(self, prompt):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(.025)  # Simulated provider I/O; no external calls.
                with self.lock:
                    return super().complete(prompt)
            finally:
                with self.lock:
                    self.active -= 1

    judge = ConcurrentJudge()
    service = make_service(framework, tmp_path, judge, workers=3)
    branch = recording(framework, count=35)
    jobs = [service.submit(branch.id, branch.head, 'complete') for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda j: service.execute(j['id']), jobs))
    assert judge.peak == 3  # Two jobs must not create six simultaneous provider calls.
    assert judge.active == 0
    assert all(service.jobs.get(j['id'])['status'] == 'completed' for j in jobs)


def test_worker_configuration_and_preview(framework, monkeypatch):
    monkeypatch.delenv('MAST_WORKERS', raising=False)
    assert MastPlugin(ChunkJudge()).workers == 3
    monkeypatch.setenv('MAST_WORKERS', '2')
    plugin = MastPlugin(ChunkJudge(), max_trace_characters=18_000)
    branch = recording(framework)
    assert plugin.prepare_analysis(framework.history(branch.id), {})['summary']['workers'] == 2
    assert MastPlugin(ChunkJudge(), workers=1).workers == 1
    for bad in [0, 9, -1]:
        with pytest.raises(ValueError, match='MAST_WORKERS'):
            MastPlugin(ChunkJudge(), workers=bad)
