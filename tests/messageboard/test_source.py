import json

import pytest

from examples.messageboard.source import import_batch
from swarm_lens import Framework
from swarm_lens.adapters.sqlite import SQLiteHistory


def event(kind, at, **fields):
    return {'event_id': f'{kind}-{at}', 'timestamp': f'2026-09-25T06:25:{at:02d}Z', 'round_number': 1,
            'event_type': kind, **fields}


WORLD = [
    event('simulation_started', 0, scenario_description='An API task.', channel_ids=['board']),
    *[event('agent_registered', 1 + i, agent_id=f'reviewer_{i}', role_name=f'reviewer_{i}', system_prompt='Collect records.',
            channel_ids=['board'], tool_names=['shell'], model='qwen/test', provider='self-hosted', max_tokens=8192)
      for i in (1, 2)],
    event('peer_norms_api_task_opened', 4, problem_statement='Collect US stats.', expected_answer='{}'),
    event('injection_delivered', 5, agent_id='reviewer_1', text='Collect US stats.'),
    event('llm_response_received', 6, agent_id='reviewer_1', thinking='Fetch it.', text=None,
          tool_calls=[{'call_id': 'c1', 'tool_name': 'shell', 'arguments': {'command': 'curl x'}}],
          stop_reason='tool_use', usage={}),
    event('tool_call_invoked', 7, agent_id='reviewer_1', call_id='c1', tool_name='shell', arguments={'command': 'curl x'}),
    event('shell_executed', 8, agent_id='reviewer_1', command='curl x', stdout='{}', stderr='', exit_code=0),
    event('tool_result_received', 9, agent_id='reviewer_1', call_id='c1', tool_name='shell', result='{}'),
    event('peer_norms_score_recorded', 10, outcomes={'reviewer_1': 'correct', 'reviewer_2': 'missing'}),
]


@pytest.fixture
def dataset(tmp_path):
    log = tmp_path / 'batch/run-qwen-01/events.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text(''.join(json.dumps(row) + '\n' for row in WORLD))
    featured = {'run_id': 'run-qwen-01', 'label': 'false audit', 'event_ids': ['shell_executed-8']}
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'original_batch_id': 'batch-1', 'featured_case': featured,
        'runs': [{'run_id': 'run-qwen-01', 'model': 'qwen/test', 'status': 'finished', 'event_count': len(WORLD),
                  'event_files': ['batch/run-qwen-01/events.jsonl'], 'first_event_at': WORLD[0]['timestamp']}]}))
    return tmp_path


def test_world_becomes_one_run_with_agents_inboxes_tools_and_observations(dataset, tmp_path):
    (imported,) = import_batch(dataset, tmp_path / 'lens')
    framework = Framework(SQLiteHistory(tmp_path / 'lens/history.sqlite'))
    state = framework.state(imported['branch_id'])
    assert sorted(state.agents) == ['reviewer_1', 'reviewer_2']
    assert state.channels['board'].members == ['reviewer_1', 'reviewer_2']
    assert state.environment.task == 'Collect US stats.'
    assert state.tools['c1'].status == 'completed' and state.tools['c1'].result == '{}'
    turn = next(m for m in state.messages.values() if m.sender_id == 'reviewer_1')
    assert turn.content == 'Fetch it.' and turn.metadata['content_kind'] == 'thinking'
    delivery = next(m for m in state.messages.values() if m.role == 'system')
    assert delivery.channel_id == 'inbox-reviewer_1' and delivery.content == 'Collect US stats.'
    history = framework.history(imported['branch_id'])
    assert [e.data['type'] for e in history if e.source.get('featured')] == ['shell_executed']
    run = framework.store.runs()[0]
    assert run.metadata['outcomes'] == {'reviewer_1': 'correct', 'reviewer_2': 'missing'}


def test_reimport_skips_imported_worlds_and_rejects_a_truncated_log(dataset, tmp_path):
    import_batch(dataset, tmp_path / 'lens')
    assert import_batch(dataset, tmp_path / 'lens') == []
    log = dataset / 'batch/run-qwen-01/events.jsonl'
    log.write_text(''.join(log.read_text().splitlines(keepends=True)[:-1]))
    with pytest.raises(ValueError, match='manifest says'):
        import_batch(dataset, tmp_path / 'other')
