import copy
import json
from pathlib import Path

import pytest

from examples.crewai import ai_village_research as sample
from swarm_lens import Framework
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.integrations.crewai.continuation import TraceCrewAIRuntime


def event(index, action, **data):
    return {'id': f'e{index}', 'eventIndex': index, 'villageId': sample.VILLAGE,
            'createdAt': f'2026-05-11T17:00:{index:02d}.000Z', 'data': {'actionType': action, **data}}


@pytest.fixture
def recording(tmp_path):
    village = {'villageGoals': [{'id': sample.GOAL, 'goal': sample.TITLE}],
               'agents': [{'id': a, 'name': a.upper(), 'modelString': 'original-model'} for a in ['a', 'b']],
               'chatRooms': [{'id': r, 'name': r} for r in ['best', 'rest']]}
    rows = [event(1, 'USER_TALK', roomId='best', content='Task', messageId='task', speakerName='Human'),
            event(2, 'AGENT_TALK', roomId='best', content='A sees this', speakerId='a', messageId='m1'),
            event(3, 'AGENT_TALK', roomId='rest', content='B private room', speakerId='b', messageId='m2'),
            event(4, 'ENTER_ROOM', agentId='a', roomId='rest', previousRoomId='best'),
            event(5, 'CONSOLIDATE', agentId='a', roomId='rest', nextSessionGoal='Use a computer later'),
            event(6, 'AGENT_TALK', roomId='rest', content='FUTURE_SENTINEL', speakerId='b', messageId='m3')]
    root = tmp_path/'sample'
    sample.save_sample(root, village, list(reversed(rows)), [], 1)
    return root


def test_import_preserves_events_and_dynamic_room_presence(recording, tmp_path):
    data = tmp_path/'workspace'
    branch = sample.import_sample(recording, data)
    fw = Framework(SQLiteHistory(data/'history.sqlite'))
    history = fw.history(branch.id)
    assert [e.source['event_index'] for e in history] == sorted(e.source['event_index'] for e in history)
    state = fw.state(branch.id)
    assert set(state.messages) == {'task', 'm1', 'm2', 'm3'}
    assert state.channels['best'].members == []
    assert state.channels['rest'].members == ['a', 'b']
    assert not state.tools and not state.memories  # A session notice is neither execution nor memory.
    assert sample.import_sample(recording, data).id == branch.id
    assert fw.history(branch.id) == history
    before_move = next(e.position for e in history if e.kind == 'message.created' and e.data['id'] == 'm2')
    state = fw.state(branch.id, before_move)
    adapter = TraceCrewAIRuntime(fw)
    prompt, sources = adapter.context(state, {'agent_id': 'a', 'phase': 'round_robin'}, history[:before_move])
    assert sources == ['task', 'm1']
    assert 'B private room' not in prompt and 'FUTURE_SENTINEL' not in prompt


def test_model_substitution_is_only_on_an_explicit_child(recording, tmp_path):
    data = tmp_path/'workspace'; parent = sample.import_sample(recording, data)
    fw = Framework(SQLiteHistory(data/'history.sqlite')); original = fw.history(parent.id)
    cursor = next(e.position for e in original if e.kind == 'observation.recorded' and e.data['type'] == 'CONSOLIDATE')
    child = sample.prepare_branch(data, parent.id, 'gpt-5.5', cursor)
    assert child.parent_id == parent.id and child.fork_position == cursor and child.run_id == parent.run_id
    assert all(a.model == 'gpt-5.5' for a in fw.state(child.id).agents.values())
    assert all(a.model == 'original-model' for a in fw.state(parent.id).agents.values())
    assert fw.history(parent.id) == original
    assert 'm3' not in fw.state(child.id).messages


def test_corrupt_sample_is_rejected_before_creating_workspace(recording, tmp_path):
    p = recording/'events.jsonl.gz'; p.write_bytes(p.read_bytes()+b'broken')
    with pytest.raises(ValueError, match='checksum'):
        sample.import_sample(recording, tmp_path/'workspace')
    assert not (tmp_path/'workspace').exists()


def test_five_day_fetch_never_downloads_dataset_tables(recording, tmp_path, monkeypatch):
    manifest, rows = sample.load_sample(recording)
    village = {'villageGoals': [manifest['goal']], 'agents': manifest['agents'], 'chatRooms': manifest['rooms']}
    calls = []
    def get(url, params=None):
        calls.append((url, params))
        if url.endswith(sample.VILLAGE): return village, b'roster'
        row = copy.deepcopy(rows[0]);row['id'] = params['date'];row['createdAt'] = params['date']+'T17:00:00Z'
        row['eventIndex'] = int(params['date'][-2:]);row['data']['messageId'] = row['id']
        return {'windowDate': params['date'], 'events': [row], 'hasMore': False}, b'events'
    monkeypatch.setattr(sample, 'read_json', get)
    result = sample.fetch(tmp_path/'download')
    assert result['event_count'] == 5
    assert len(calls) == 6 and [p['date'] for _, p in calls[1:]] == list(sample.DATES)
    assert all('/api/events' in u for u, _ in calls[1:])


def test_bundled_recording_is_complete_and_validates(tmp_path):
    manifest, rows = sample.load_sample(sample.SAMPLE)
    assert len(rows) == 3680
    assert len([e for e in rows if e['data']['actionType'] in {'AGENT_TALK', 'USER_TALK'}]) == 2222
    assert [p['date'] for p in manifest['pages']] == list(sample.DATES)
    assert all(not p['has_more'] for p in manifest['pages'])
    branch = sample.import_sample(sample.SAMPLE, tmp_path/'workspace')
    fw = Framework(SQLiteHistory(tmp_path/'workspace/history.sqlite'))
    assert len(fw.state(branch.id).messages) == 2222
    assert len(fw.state(branch.id).agents) == 15


def test_real_crewai_continues_saved_prefix_without_network(recording, tmp_path):
    pytest.importorskip('crewai')
    from crewai.llms.base_llm import BaseLLM
    from swarm_lens.live.service import Facts
    prompts = []
    class Offline(BaseLLM):
        def __init__(self): super().__init__(model='offline-research-fixture', temperature=0)
        def supports_function_calling(self): return False
        def call(self, messages, **kwargs):
            prompts.append(str(messages));return 'Final Answer: Next research step, with no fabricated result.'
    data = tmp_path/'workspace';parent = sample.import_sample(recording, data)
    fw = Framework(SQLiteHistory(data/'history.sqlite'));history = fw.history(parent.id)
    cursor = next(e.position for e in history if e.kind == 'observation.recorded' and e.data['type'] == 'CONSOLIDATE')
    child = sample.prepare_branch(data, parent.id, cursor=cursor)
    adapter = TraceCrewAIRuntime(fw, llm_factory=lambda _: Offline())
    assert adapter.describe(fw.state(child.id))['can_execute']
    adapter.execute(fw.state(child.id), 1, lambda facts: fw.ingest(child.id, Facts(facts)))
    assert len(prompts) == 1 and 'FUTURE_SENTINEL' not in prompts[0]
    assert 'B private room' in prompts[0]
    assert fw.history(parent.id) == history
    outputs = [e for e in fw.history(child.id)[child.head:] if e.kind == 'message.created']
    assert len(outputs) == 1 and outputs[0].source['origin'] == 'crewai_trace'
