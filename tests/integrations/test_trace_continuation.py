import json
import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault('OTEL_SDK_DISABLED', 'true')
os.environ.setdefault('CREWAI_TELEMETRY_DISABLED', 'true')
os.environ.setdefault('CREWAI_TRACING_ENABLED', 'false')
os.environ.setdefault('CREWAI_STORAGE_DIR', '/tmp/swarm-lens-crewai-tests')

import pytest
pytest.importorskip('crewai')
from crewai.llms.base_llm import BaseLLM
from fastapi.testclient import TestClient
from examples.aciarena.sample_source import ExampleSource, load_pair
from examples.crewai.demo import OfflineLLM, create_runtime, trace_tools
from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.core.models import DomainError, Fact, utc_now
from swarm_lens.integrations.crewai import TraceCrewAIRuntime
from swarm_lens.live.service import Facts, LiveService
from swarm_lens.web.api import create_app


class SequenceLLM(BaseLLM):
    def __init__(self, prompts):
        super().__init__(model='offline-continuation', temperature=0)
        self.prompts = prompts

    def supports_function_calling(self):
        return False

    def call(self, messages, **kwargs):
        self.prompts.append(str(messages))
        return f'Final Answer: new-response-{len(self.prompts)} violet'


def runtime(framework, prompts, **kwargs):
    return TraceCrewAIRuntime(framework, llm_factory=lambda _: SequenceLLM(prompts), **kwargs)


def aciarena(framework, tmp_path, condition):
    root = Path(__file__).parents[2]/'examples/aciarena/samples/llm-debate-pair-20261003'
    manifest, rows = load_pair(root)
    branch = framework.create_run('ACIArena '+condition, {'system': 'LLMDebate', 'max_turn': 20})
    framework.ingest(branch.id, ExampleSource(manifest, rows[condition], FileArtifacts(tmp_path/'artifacts')))
    return branch


@pytest.mark.parametrize('condition', ['control', 'injection'])
def test_aci_mid_round_fork_native_execution_and_exact_delivery(framework, tmp_path, condition):
    parent = aciarena(framework, tmp_path, condition)
    original = framework.history(parent.id)
    point = next(e for e in original if e.kind == 'message.created'
                 and e.data['metadata'].get('round') == 19 and e.data['sender_id'] == 'debater_0')
    prompts = []
    adapter = runtime(framework, prompts)
    live = LiveService(framework, tmp_path/'live.sqlite', (adapter, create_runtime()))
    before_count = len(framework.store.branches(parent.run_id))
    with TestClient(create_app(framework, live=live)) as client:
        url = f'/api/branches/{parent.id}'
        body = {'cursor': point.position, 'intervention': {'kind': 'environment.updated', 'data': {'goal': 'Include violet.'}}}
        plan = client.post(url+'/execution/preview', json=body).json()
        assert plan['can_execute'] and plan['policy'] == 'aciarena_debate'
        assert plan['remaining_tasks'] == 6
        assert plan['next_actor'] == 'Debater 1' and plan['next_round'] == 19
        assert plan['injection_agents'] == (['debater_0'] if condition == 'injection' else [])
        assert len(framework.store.branches(parent.run_id)) == before_count
        request = {**body, 'name': 'Continue debate', 'steps': 6, 'request_id': str(uuid4())}
        response = client.post(url+'/fork-execute', json=request)
        assert response.status_code == 202, response.text
        job = response.json()
        assert client.post(url+'/fork-execute', json=request).json()['id'] == job['id']
    assert live.job(job['id'])['status'] == 'completed', live.job(job['id'])
    child = framework.store.branch(job['branch_id'])
    assert child.run_id == parent.run_id and child.parent_id == parent.id and child.fork_position == point.position
    new = framework.history(child.id)[point.position:]
    outputs = [e for e in new if e.kind == 'message.created']
    assert [(e.data['sender_id'], e.data['metadata']['phase'], e.data['metadata']['round']) for e in outputs] == [
        ('debater_1', 'debate', 19), ('debater_2', 'debate', 19),
        ('debater_0', 'debate', 20), ('debater_1', 'debate', 20), ('debater_2', 'debate', 20),
        ('aggregator', 'aggregation', 21)]
    assert outputs[0].data['metadata']['sources'] == ['call-58', 'call-57']
    assert outputs[1].data['metadata']['sources'] == ['call-58', outputs[0].data['id']]
    assert outputs[-1].data['metadata']['sources'] == [e.data['id'] for e in outputs[2:5]]
    assert all('Include violet.' in p for p in prompts)
    assert all('call-59' not in p and 'call-64' not in p for p in prompts)
    assert 'new-response-1' in prompts[1] and 'new-response-2' not in prompts[0]
    assert ('mandatory name' in prompts[2]) == (condition == 'injection')
    assert framework.history(parent.id) == original
    assert framework.history(child.id)[:point.position] == original[:point.position]
    # Completed traces can continue through twenty further debate rounds, then aggregation.
    plan = adapter.describe(framework.state(child.id))
    assert plan['remaining_tasks'] == 61 and plan['next_round'] == 21


def test_prompt_intervention_replaces_the_system_prompt_recorded_in_conversation_memory(framework, tmp_path):
    parent = aciarena(framework, tmp_path, 'control')
    original = framework.history(parent.id)
    old_prompt = framework.state(parent.id).agents['debater_0'].system_prompt
    point = next(e for e in original if e.kind == 'message.created'
                 and e.data['metadata'].get('round') == 19 and e.data['sender_id'] == 'debater_0')
    prompts = []
    live = LiveService(framework, tmp_path/'live.sqlite', (runtime(framework, prompts),))
    with TestClient(create_app(framework, live=live)) as client:
        job = client.post(f'/api/branches/{parent.id}/fork-execute', json={
            'cursor': point.position, 'name': 'New prompt', 'steps': 6, 'request_id': str(uuid4()),
            'intervention': {'kind': 'agent.updated', 'data': {'id': 'debater_0', 'system_prompt': 'You are terse.'}}}).json()
    assert live.job(job['id'])['status'] == 'completed', live.job(job['id'])
    debater_0 = prompts[2]  # debater_1 and debater_2 finish round 19 first
    assert 'You are terse.' in debater_0
    assert max(old_prompt.splitlines(), key=len) not in debater_0
    memory = json.loads(framework.state(parent.id).memories['conversation-debater_0'].content)
    assert memory[0]['role'] == 'system'  # The recorded memory itself is unchanged


def test_generic_connections_memories_changes_and_tool_registry(framework, tmp_path):
    parent = framework.create_run('Imported application')
    facts = [Fact('environment.updated', {'task': 'Calculate 19 + 23', 'goal': 'Use add_values'}, utc_now())]
    for aid in ['a', 'b', 'private']:
        facts.append(Fact('agent.added', {'id': aid, 'name': aid, 'system_prompt': 'Use add_values',
                                        'metadata': {'tools': ['add_values']}}, utc_now()))
    facts += [Fact('channel.created', {'id': 'public', 'name': 'Shared', 'members': ['a', 'b']}, utc_now()),
              Fact('channel.created', {'id': 'private', 'name': 'Private', 'members': ['private']}, utc_now()),
              Fact('message.created', {'id': 'a-first', 'sender_id': 'a', 'channel_id': 'public', 'content': 'Visible history'}, utc_now()),
              Fact('message.created', {'id': 'secret', 'sender_id': 'private', 'channel_id': 'private', 'content': 'PRIVATE_SECRET'}, utc_now()),
              Fact('memory.written', {'id': 'private-m', 'owner_id': 'private', 'content': 'PRIVATE_MEMORY'}, utc_now()),
              Fact('memory.written', {'id': 'shared', 'scope': 'shared', 'content': 'violet shared memory'}, utc_now())]
    framework.ingest(parent.id, Facts(facts))
    cursor = framework.store.branch(parent.id).head
    # A later parent event must not enter previews or execution of the selected prefix.
    framework.ingest(parent.id, Facts([Fact('memory.written', {'id': 'future', 'scope': 'shared', 'content': 'FUTURE_SENTINEL'}, utc_now())]))
    unregistered = runtime(framework, [])
    assert 'add_values' in unregistered.describe(framework.state(parent.id, cursor))['reason']
    live = LiveService(framework, tmp_path/'jobs.sqlite', (unregistered,))
    with pytest.raises(DomainError, match='Register executable'):
        live.submit_fork(parent.id, cursor, 'Missing tool', 3, str(uuid4()))
    assert len(framework.store.branches(parent.run_id)) == 1
    live.close()
    adapter = TraceCrewAIRuntime(framework, tools=trace_tools(), llm_factory=lambda _: OfflineLLM())
    live = LiveService(framework, tmp_path/'jobs.sqlite', (adapter,))
    change = {'kind': 'agent.updated', 'data': {'id': 'b', 'system_prompt': 'Use add_values. Include violet.'}}
    preview = live.describe(parent.id, cursor, change)
    assert preview['can_execute'] and preview['remaining_tasks'] == 3
    assert preview['next_actor'] == 'a'
    job = live.submit_fork(parent.id, cursor, 'Tools and memories', 3, str(uuid4()), change)
    live.close()
    assert live.job(job['id'])['status'] == 'completed', live.job(job['id'])
    state = framework.state(job['branch_id'])
    assert set(state.agents) == {'a', 'b', 'private'}
    assert len(state.tools) == 3 and all(t.result == '42' for t in state.tools.values())
    a_prompt = next(m.content for m in state.memories.values() if m.owner_id == 'a' and m.metadata.get('type') == 'model_input')
    assert 'violet shared memory' in a_prompt and 'Visible history' in a_prompt
    assert all(secret not in a_prompt for secret in ['PRIVATE_SECRET', 'PRIVATE_MEMORY', 'FUTURE_SENTINEL'])
    assert 'future' not in state.memories


def test_partial_execution_fork_uses_saved_schedule_and_rejects_forgery(framework, tmp_path):
    parent = aciarena(framework, tmp_path, 'control')
    point = next(e for e in framework.history(parent.id) if e.kind == 'message.created'
                 and e.data['metadata'].get('round') == 20 and e.data['sender_id'] == 'debater_0')
    prompts = []
    adapter = runtime(framework, prompts)
    child = framework.fork(parent.id, point.position, 'One turn')
    def emit(facts):
        framework.ingest(child.id, Facts(facts), batch_size=len(facts))
    adapter.execute(framework.state(child.id), 1, emit)
    after = framework.state(child.id)
    assert adapter.describe(after)['next_actor'] == 'Debater 2'
    # Fork before the response, while the CrewAI task is running.
    prompt = next(e for e in framework.history(child.id) if e.kind == 'memory.written' and e.data['metadata'].get('type') == 'model_input')
    mid = framework.fork(child.id, prompt.position, 'Restart current turn')
    assert adapter.describe(framework.state(mid.id))['next_actor'] == 'Debater 1'
    adapter.execute(framework.state(mid.id), 3, lambda f: framework.ingest(mid.id, Facts(f), batch_size=len(f)))
    assert framework.state(mid.id).environment.metadata['trace_continuation']['next_task'] == 3
    forged = after.environment.metadata
    forged['trace_continuation']['next_task'] = 0
    live = LiveService(framework, tmp_path/'live.sqlite', (adapter,))
    preview = live.describe(child.id, after.cursor, {'kind': 'environment.updated', 'data': {'metadata': forged}})
    assert not preview['can_execute'] and 'cannot be edited' in preview['reason']
    live.close()


def test_aci_first_event_bootstrap_and_boundary_plans(framework, tmp_path):
    parent = aciarena(framework, tmp_path, 'control')
    adapter = runtime(framework, [])
    history = framework.history(parent.id)
    ready = next(e for e in history if e.kind == 'channel.created')
    assert adapter.describe(framework.state(parent.id, ready.position))['remaining_tasks'] == 64
    first = next(e for e in history if e.kind == 'message.created' and e.data.get('sender_id') == 'debater_0')
    assert adapter.describe(framework.state(parent.id, first.position))['remaining_tasks'] == 63
    assert adapter.describe(framework.state(parent.id))['remaining_tasks'] == 61


def test_full_twenty_round_schedule_with_native_crewai(framework, tmp_path):
    parent = aciarena(framework, tmp_path, 'injection')
    point = next(e for e in framework.history(parent.id) if e.data.get('type') == 'instruction_injection')
    prompts = []
    adapter = runtime(framework, prompts)
    child = framework.fork(parent.id, point.position, 'Full reconstructed debate')
    adapter.execute(framework.state(child.id), 64, lambda f: framework.ingest(child.id, Facts(f), batch_size=len(f)))
    messages = [m for m in framework.state(child.id).messages.values() if m.metadata.get('type') == 'trace_task_output']
    assert len(prompts) == len(messages) == 64
    assert [(m.sender_id, m.metadata['round']) for m in messages[:3]] == [(f'debater_{i}', 0) for i in range(3)]
    assert [(m.sender_id, m.metadata['round']) for m in messages[3:-1]] == [
        (f'debater_{i}', turn) for turn in range(1, 21) for i in range(3)]
    assert messages[-1].sender_id == 'aggregator' and messages[-1].metadata['round'] == 21
    earlier = set()
    for message in messages:
        assert set(message.metadata['sources']) <= earlier
        earlier.add(message.id)
    assert all(('mandatory name' in prompts[i]) == (i % 3 == 0 and i < 63) for i in range(64))
