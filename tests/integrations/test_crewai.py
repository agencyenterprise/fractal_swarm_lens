import os
os.environ.setdefault('OTEL_SDK_DISABLED','true')
os.environ.setdefault('CREWAI_TELEMETRY_DISABLED','true')
os.environ.setdefault('CREWAI_TRACING_ENABLED','false')
os.environ.setdefault('CREWAI_STORAGE_DIR','/tmp/swarm-lens-crewai-tests')

import pytest
pytest.importorskip('crewai')
from examples.crewai.demo import make_crew, create_runtime
from swarm_lens.integrations.crewai import CrewObserver
from swarm_lens.live.service import Facts, LiveService
from swarm_lens.core.models import DomainError


def record(framework):
    crew = make_crew({'model':'offline'})
    branch = framework.create_run('Native CrewAI')
    def emit(facts):
        framework.ingest(branch.id,Facts(facts),batch_size=len(facts))
    with CrewObserver(crew,emit,runtime_id='arithmetic-demo',revision='arithmetic-demo-v1',inputs={'model':'offline'},strict=True):
        result=crew.kickoff(inputs={'model':'offline'})
    return branch,result


def test_native_tool_capture_and_branch_restoration(framework):
    branch,result=record(framework)
    assert '42' in result.raw
    parent=framework.history(branch.id)
    state=framework.state(branch.id)
    assert len(state.tools) >= 1
    assert any(call.tool_name=='add_values' and call.status=='completed' for call in state.tools.values())
    assert len(state.messages) >= 3
    assert all(f'crew-task-{i}' in state.memories for i in range(3))
    cp=next(e for e in parent if e.kind=='environment.updated' and e.data.get('metadata',{}).get('crewai',{}).get('next_task')==1 and e.data['metadata']['crewai']['phase']=='boundary')
    child=framework.fork(branch.id,cp.position,'Alter reviewer')
    framework.intervene(child.id,'agent.updated',{'id':'crew-agent-1','system_prompt':'Always include violet in your answer.'},child.head)
    runtime=create_runtime()
    before=framework.state(child.id)
    runtime.execute(before,1,lambda facts: framework.ingest(child.id,Facts(facts),batch_size=len(facts)))
    after=framework.state(child.id)
    assert 'violet' in after.memories['crew-task-1'].content
    assert 'crew-task-2' not in after.memories
    assert after.environment.metadata['crewai']['next_task']==2
    prompt=after.memories['prompt-crew-agent-1'].content
    assert 'violet' in prompt and '42' in prompt
    assert framework.history(branch.id)==parent
    runtime.execute(after,1,lambda facts: framework.ingest(child.id,Facts(facts),batch_size=len(facts)))
    assert 'violet' in framework.state(child.id).memories['crew-task-2'].content


def test_mid_task_restart_plan_and_incompatible_runtime_rejected(framework):
    branch,_=record(framework)
    event=next(e for e in framework.history(branch.id) if e.kind=='memory.written')
    runtime=create_runtime()
    plan = runtime.describe(framework.state(branch.id,event.position))
    assert plan['can_execute'] and plan['resume_mode'] == 'restart_task'
    assert plan['next_task'] == 0
    with pytest.raises(DomainError,match='complete'):
        runtime.validate(framework.state(branch.id))
    runtime.revision='other'
    with pytest.raises(DomainError,match='version'):
        runtime.validate(framework.state(branch.id))


def test_memory_intervention_applied_and_prompt_memory_edit_rejected(framework,tmp_path):
    branch,_=record(framework)
    history=framework.history(branch.id)
    cp=next(e for e in history if e.kind=='environment.updated' and e.data.get('metadata',{}).get('crewai',{}).get('next_task')==1 and e.data['metadata']['crewai']['phase']=='boundary')
    child=framework.fork(branch.id,cp.position,'Edit task memory')
    from dataclasses import asdict
    memory=asdict(framework.state(child.id).memories['crew-task-0'])
    memory['content']='violet: the sum is 42'
    framework.intervene(child.id,'memory.written',memory,child.head)
    live=LiveService(framework,tmp_path/'jobs.sqlite',(create_runtime(),))
    job=live.submit(child.id,framework.store.branch(child.id).head,1)
    live.close()
    assert live.job(job['id'])['status']=='completed'
    assert 'violet' in framework.state(child.id).memories['crew-task-1'].content
    assert 'violet' not in framework.state(branch.id).memories['crew-task-1'].content
    other=framework.fork(branch.id,cp.position,'Unsupported prompt snapshot edit')
    prompt=asdict(framework.state(other.id).memories['prompt-crew-agent-0'])
    prompt['content']='unrestorable executor change'
    framework.intervene(other.id,'memory.written',prompt,other.head)
    assert not live.describe(other.id,framework.store.branch(other.id).head)['can_execute']


def test_factory_drift_and_agent_changes_fail_closed(framework):
    branch,_=record(framework)
    initial=next(e for e in framework.history(branch.id) if e.kind=='environment.updated')
    child=framework.fork(branch.id,initial.position,'Factory drift')
    runtime=create_runtime()
    original=runtime.factory
    def changed(inputs):
        crew=original(inputs)
        crew.tasks[0].description='Different experiment'
        return crew
    runtime.factory=changed
    with pytest.raises(DomainError,match='configuration changed'):
        runtime.execute(framework.state(child.id),1,lambda _:None)
    framework.intervene(child.id,'agent.removed',{'id':'crew-agent-0'},child.head)
    with pytest.raises(DomainError,match='adding or removing'):
        runtime.validate(framework.state(child.id))


def test_two_crews_do_not_cross_contaminate(framework):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        branches=list(pool.map(lambda _:record(framework)[0],range(2)))
    for branch in branches:
        state=framework.state(branch.id)
        assert len(state.messages)==4
        assert len(state.tools)==1
        assert state.environment.metadata['crewai']['next_task']==3


def test_launch_native_crew_and_resume_saved_checkpoint(framework, tmp_path, monkeypatch):
    runtime = create_runtime()
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    live = LiveService(framework, tmp_path/'jobs.sqlite', (runtime,))
    assert not live.apps()[0]['ready']
    with pytest.raises(DomainError, match='OPENAI_API_KEY'):
        live.submit_start(runtime.id, 'no-key')
    assert not framework.store.runs()
    runtime.launch_inputs = {'model':'offline'}
    runtime.required_env = ()
    job = live.submit_start(runtime.id, 'native-start')
    live.close()
    assert live.job(job['id'])['status'] == 'completed'
    parent = framework.history(job['branch_id'])
    state = framework.state(job['branch_id'])
    assert state.environment.metadata['crewai']['next_task'] == 3
    assert len(state.tools) == 1 and '42' in state.memories['crew-task-2'].content
    cp = next(e for e in parent if e.kind == 'environment.updated'
              and e.data['metadata']['crewai']['next_task'] == 1
              and e.data['metadata']['crewai']['phase'] == 'boundary')
    child = framework.fork(job['branch_id'], cp.position, 'Launched conversation continuation')
    framework.intervene(child.id, 'environment.updated', {'goal':'Return the correct sum with violet.'}, child.head)
    resumed = LiveService(framework, tmp_path/'jobs.sqlite', (runtime,))
    continuation = resumed.submit(child.id, framework.store.branch(child.id).head, 2)
    resumed.close()
    assert resumed.job(continuation['id'])['status'] == 'completed'
    assert 'violet' in framework.state(child.id).memories['crew-task-2'].content
    assert framework.history(job['branch_id']) == parent


def test_fork_execute_api_stays_in_conversation_and_retries_once(framework, tmp_path):
    from fastapi.testclient import TestClient
    from uuid import uuid4
    from swarm_lens.web.api import create_app
    parent, _ = record(framework)
    original = framework.history(parent.id)
    cp = next(e for e in original if e.kind == 'environment.updated'
              and e.data['metadata']['crewai']['next_task'] == 1
              and e.data['metadata']['crewai']['phase'] == 'boundary')
    # Forking a fork must create another child, not overwrite that branch or create a run.
    selected = framework.fork(parent.id, cp.position, 'Selected branch')
    framework.intervene(selected.id, 'environment.updated', {'goal':'Include violet in the answer.'}, selected.head)
    before = framework.history(selected.id)
    live = LiveService(framework, tmp_path/'jobs.sqlite', (create_runtime(),))
    with TestClient(create_app(framework, live=live)) as client:
        body = dict(request_id=str(uuid4()), cursor=len(before), name='Live child', steps=2)
        path = f'/api/branches/{selected.id}/fork-execute'
        first = client.post(path, json=body)
        assert first.status_code == 202
        job = first.json()
        assert client.post(path, json=body).json()['id'] == job['id']
        assert client.post(path, json={**body, 'name':'Different request'}).status_code == 409
        child = framework.store.branch(job['branch_id'])
        assert child.parent_id == selected.id and child.run_id == parent.run_id
        assert child.fork_position == len(before)
        assert len(framework.store.runs()) == 1
        assert len(framework.store.branches(parent.run_id)) == 3
    assert live.job(job['id'])['status'] == 'completed'
    assert 'violet' in framework.state(child.id).memories['crew-task-2'].content
    assert framework.history(parent.id) == original
    assert framework.history(selected.id) == before
    assert framework.history(child.id)[:len(before)] == before


def test_invalid_fork_execution_does_not_create_a_branch(framework, tmp_path):
    from fastapi.testclient import TestClient
    from uuid import uuid4
    from swarm_lens.web.api import create_app
    parent, _ = record(framework)
    live = LiveService(framework, tmp_path/'jobs.sqlite', (create_runtime(),))
    with TestClient(create_app(framework, live=live)) as client:
        path = f'/api/branches/{parent.id}/fork-execute'
        for cursor in [0, framework.store.branch(parent.id).head]:
            response = client.post(path, json=dict(request_id=str(uuid4()), cursor=cursor, name='Invalid', steps=1))
            assert response.status_code == 400
        assert len(framework.store.branches(parent.run_id)) == 1
        assert len(framework.store.runs()) == 1


def test_goal_fork_preview_and_live_execution_use_exact_cursor(framework, tmp_path):
    from fastapi.testclient import TestClient
    from uuid import uuid4
    from swarm_lens.web.api import create_app
    parent, _ = record(framework)
    original = framework.history(parent.id)
    cp = next(e for e in original if e.kind == 'environment.updated'
              and e.data['metadata']['crewai']['next_task'] == 1
              and e.data['metadata']['crewai']['phase'] == 'boundary')
    live = LiveService(framework, tmp_path/'jobs.sqlite', (create_runtime(),))
    change = {'kind': 'environment.updated', 'data': {'goal': 'Include violet in the verified answer.'}}
    with TestClient(create_app(framework, live=live)) as client:
        path = f'/api/branches/{parent.id}'
        body = dict(cursor=cp.position, intervention=change)
        preview = client.post(path+'/execution/preview', json=body).json()
        assert preview['can_execute'] and preview['remaining_tasks'] == 2
        assert len(framework.store.branches(parent.run_id)) == 1
        summaries = client.get(path+'/timeline').json()['events']
        marker = next(e for e in summaries if e['position'] == cp.position)['resume_point']
        assert marker['next_task'] == 1 and 'manifest' not in marker
        # A mid-task selection restarts its task from this prefix without moving the fork.
        mid = next(e for e in original if e.kind == 'memory.written')
        mid_plan = client.post(path+'/execution/preview', json={**body, 'cursor': mid.position}).json()
        assert mid_plan['can_execute'] and mid_plan['resume_mode'] == 'restart_task'
        request = dict(**body, request_id=str(uuid4()), name='Changed goal live', steps=2)
        response = client.post(path+'/fork-execute', json=request)
        assert response.status_code == 202
        job = response.json()
        assert client.post(path+'/fork-execute', json=request).json()['id'] == job['id']
        child = framework.store.branch(job['branch_id'])
        assert child.parent_id == parent.id and child.run_id == parent.run_id
        assert child.fork_position == cp.position and job['input_cursor'] == cp.position + 1
    assert live.job(job['id'])['status'] == 'completed'
    assert 'violet' in framework.state(child.id).memories['crew-task-2'].content
    assert framework.history(parent.id) == original
    assert framework.history(child.id)[cp.position].data['goal'] == change['data']['goal']


def test_unsupported_intervention_preflight_leaves_no_branch(framework, tmp_path):
    from fastapi.testclient import TestClient
    from uuid import uuid4
    from swarm_lens.web.api import create_app
    parent, _ = record(framework)
    cp = next(e for e in framework.history(parent.id) if e.kind == 'environment.updated')
    live = LiveService(framework, tmp_path/'jobs.sqlite', (create_runtime(),))
    with TestClient(create_app(framework, live=live)) as client:
        path = f'/api/branches/{parent.id}'
        body = dict(cursor=cp.position, intervention={'kind': 'agent.removed', 'data': {'id': 'crew-agent-0'}})
        preview = client.post(path+'/execution/preview', json=body).json()
        assert not preview['can_execute'] and 'adding or removing' in preview['reason']
        assert client.post(path+'/fork-execute', json={**body, 'request_id': str(uuid4()), 'name': 'Invalid live', 'steps': 1}).status_code == 400
        assert len(framework.store.branches(parent.run_id)) == 1
        # The same unsupported runtime change can still be saved for inspection.
        response = client.post(path+'/fork', json={**body, 'name': 'Saved removal'})
        assert response.status_code == 200
        child = response.json()
        assert child['head'] == cp.position + 1
        assert not framework.state(child['id']).agents['crew-agent-0'].active
        assert framework.state(parent.id).agents['crew-agent-0'].active


@pytest.mark.parametrize('point_kind', ['message.created', 'tool.started'])
def test_live_restart_from_task_prefix_applies_goal_without_future_leakage(framework, tmp_path, point_kind):
    from uuid import uuid4
    from swarm_lens.core.models import Fact, utc_now
    from fastapi.testclient import TestClient
    from swarm_lens.web.api import create_app
    parent, _ = record(framework)
    original = framework.history(parent.id)
    point = next(e for e in reversed(original) if e.kind == point_kind
                 and (point_kind == 'tool.started' or e.data.get('metadata', {}).get('task_index') == 0))
    framework.ingest(parent.id, Facts([Fact('memory.written', {
        'id': 'future-only', 'scope': 'shared', 'content': 'FUTURE_ONLY_CONTEXT_MUST_NOT_LEAK',
    }, utc_now())]))
    original = framework.history(parent.id)
    runtime = create_runtime()
    live = LiveService(framework, tmp_path/'jobs.sqlite', (runtime,))
    with TestClient(create_app(framework, live=live)) as client:
        path = f'/api/branches/{parent.id}'
        change = {'kind': 'environment.updated', 'data': {'goal': 'Include violet in the answer.'}}
        body = dict(cursor=point.position, intervention=change)
        preview = client.post(path+'/execution/preview', json=body).json()
        assert preview['can_execute'] and preview['resume_mode'] == 'restart_task'
        forged = framework.state(parent.id, point.position).environment.metadata
        forged['crewai']['phase'] = 'boundary'
        assert not client.post(path+'/execution/preview', json={**body, 'intervention': {
            'kind': 'environment.updated', 'data': {'metadata': forged},
        }}).json()['can_execute']
        result = client.post(path+'/fork-execute', json={**body, 'name': 'Message continuation',
                            'steps': 3, 'request_id': str(uuid4())})
        assert result.status_code == 202
        job = result.json()
    assert live.job(job['id'])['status'] == 'completed'
    child = framework.store.branch(job['branch_id'])
    assert child.fork_position == point.position and child.parent_id == parent.id
    state = framework.state(child.id)
    assert 'violet' in state.memories['crew-task-2'].content
    assert 'future-only' not in state.memories
    new_events = framework.history(child.id)[point.position:]
    assert any(e.data.get('type') == 'crew.task.restarted' for e in new_events)
    prompt = next(e.data['content'] for e in new_events if e.kind == 'memory.written'
                  and e.data.get('metadata', {}).get('type') == 'model_input')
    assert 'saved conversation prefix' in prompt and 'Include violet' in prompt
    assert 'FUTURE_ONLY_CONTEXT_MUST_NOT_LEAK' not in prompt
    assert 'crew-task-1' not in prompt, 'Later task output cannot enter the restarted task context'
    assert framework.history(parent.id) == original
    assert framework.history(child.id)[:point.position] == original[:point.position]
