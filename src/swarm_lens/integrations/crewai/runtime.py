"""Rebuild a trusted sequential crew from a saved boundary or task prefix."""
from copy import deepcopy
from dataclasses import asdict
import json
import os

from swarm_lens.core.models import DomainError, Fact, utc_now
from swarm_lens.live.service import digest
from .adapter import CREWAI_VERSION, CrewObserver, check_version, manifest, replay_reason


class CrewAIRuntime:
    def __init__(self, id, factory, *, revision, launch_inputs=None, title=None,
                 description='', required_env=()):
        self.id, self.factory, self.revision = id, factory, revision
        # Launching is opt-in, using trusted startup inputs rather than HTTP-supplied code.
        self.launch_inputs = deepcopy(launch_inputs)
        self.title, self.description = title or id, description
        self.required_env = tuple(required_env)

    def launch_info(self):
        if self.launch_inputs is None:
            return None
        missing = [name for name in self.required_env if not os.environ.get(name, '').strip()]
        return {'id': self.id, 'title': self.title, 'description': self.description,
                'framework': 'crewai', 'framework_version': CREWAI_VERSION,
                'revision': self.revision, 'ready': not missing,
                'reason': 'Set '+', '.join(missing)+' in the server environment and restart.' if missing else None}

    def start(self, emit):
        info = self.launch_info()
        if not info or not info['ready']:
            raise DomainError(info['reason'] if info else 'This runtime has no launch configuration.')
        check_version()
        inputs = deepcopy(self.launch_inputs)
        crew = self.factory(inputs)
        with CrewObserver(crew, emit, runtime_id=self.id, revision=self.revision,
                          inputs=inputs, strict=True):
            crew.kickoff(inputs=inputs)

    def describe(self, state):
        try:
            self.validate(state)
        except DomainError as exc:
            return {'can_execute': False, 'reason': str(exc), 'runtime_id': self.id}
        cp = state.environment.metadata['crewai']
        return {'can_execute': True, 'runtime_id': self.id, 'reason': None, 'next_task': cp['next_task'],
                'remaining_tasks': cp['total_tasks']-cp['next_task'], 'revision': self.revision,
                'resume_mode': 'restart_task' if cp['phase'] == 'running' else 'task_boundary',
                'framework_version': CREWAI_VERSION}

    def validate(self, state):
        cp = state.environment.metadata.get('crewai', {})
        if cp.get('runtime_id') != self.id or cp.get('revision') != self.revision or cp.get('framework_version') != CREWAI_VERSION:
            raise DomainError('The saved runtime or CrewAI version does not match the registered factory.')
        if cp.get('replay_reason'):
            raise DomainError(cp['replay_reason'])
        if cp.get('phase') not in {'boundary', 'running'}:
            raise DomainError('This capture failed or is incomplete. Select an earlier saved event to run a branch.')
        if not 0 <= cp.get('next_task', -1) < cp.get('total_tasks', 0):
            raise DomainError('All tasks are complete. Select an earlier Resume point on the timeline to generate a different continuation.')
        if digest(cp.get('manifest')) != cp.get('manifest_digest'):
            raise DomainError('Checkpoint configuration does not match its recorded digest.')
        expected = {a['id'] for a in cp['manifest']['agents']}
        if set(state.agents) != expected or any(not a.active for a in state.agents.values()):
            raise DomainError('This runtime supports editing existing agents; adding or removing agents requires a new crew factory.')
        channel = state.channels.get('crew')
        if set(state.channels) != {'crew'} or not channel or set(channel.members) != expected:
            raise DomainError('Changed connections need an application-specific crew factory.')
        for index in range(cp['next_task']):
            if f'crew-task-{index}' not in state.memories:
                raise DomainError('A completed task output is missing from the checkpoint.')
        baseline = {a['id']: a for a in cp['manifest']['agents']}
        for aid,a in state.agents.items():
            if a.metadata.get('tools') != baseline[aid]['tools']:
                raise DomainError('Tool changes must be registered in the crew factory.')
        # Prompt snapshots are evidence, not a serialized executor. Do not silently ignore memory edits.
        for memory in state.memories.values():
            if memory.metadata.get('intervened') and memory.metadata.get('type') != 'task_output':
                raise DomainError('Only completed task-output memory can be edited for this runtime.')

    @staticmethod
    def task_prefix(state, cp):
        """Use only entities present at the fork; never consult later parent events."""
        index = cp['next_task']
        agent = cp['manifest']['tasks'][index]['agent_id']
        return {
            'input_cursor': state.cursor,
            'task_index': index,
            'messages': [asdict(m) for m in state.messages.values() if m.metadata.get('task_index') == index],
            'memories': [asdict(m) for m in state.memories.values()
                         if m.metadata.get('task_index') == index or
                         (m.metadata.get('type') != 'model_input' and m.owner_id in {agent, None})],
            'tools': [asdict(t) for t in state.tools.values() if t.agent_id == agent],
        }

    def execute(self, state, steps, emit):
        from crewai.tasks.task_output import TaskOutput
        from crewai.utilities.constants import NOT_SPECIFIED
        self.validate(state)
        check_version()
        cp = deepcopy(state.environment.metadata['crewai'])
        crew = self.factory(deepcopy(cp['inputs']))
        if replay_reason(crew) or digest(manifest(crew)) != cp['manifest_digest']:
            raise DomainError('The registered crew configuration changed since capture. Use the original factory revision.')
        start, end = cp['next_task'], min(cp['total_tasks'], cp['next_task']+steps)
        tasks = list(crew.tasks)
        for i,agent in enumerate(crew.agents):
            saved = state.agents[f'crew-agent-{i}']
            agent.role = saved.name
            agent.backstory = saved.system_prompt or ''
            if saved.model != getattr(agent.llm, 'model', None):
                from crewai import LLM
                agent.llm = LLM(model=saved.model)
        for i in range(start):
            memory = state.memories[f'crew-task-{i}']
            tasks[i].output = TaskOutput(description=tasks[i].description, raw=memory.content,
                                         agent=tasks[i].agent.role, expected_output=tasks[i].expected_output)
        for i in range(start, end):
            if tasks[i].context is NOT_SPECIFIED:
                tasks[i].context = tasks[:i]
            if state.environment.goal != cp['manifest']['tasks'][-1]['expected_output']:
                tasks[i].description += '\n\nShared goal for this branch:\n' + state.environment.goal
        if cp['phase'] == 'running':
            prefix = self.task_prefix(state, cp)
            tasks[start].description += (
                '\n\nThis is a new execution of the current task from a saved conversation prefix. '
                'Use the observations below as context, applying the shared goal and agent instructions for this branch. '
                'Earlier responses may need revision. Running tool calls belong to the original execution; '
                'their results are unknown. Do not claim an unsaved result or treat a recorded tool request as executed.\n'
                'Saved observations at the branch cursor:\n' + json.dumps(prefix, ensure_ascii=False)
            )
            emit([Fact('observation.recorded', {
                'type': 'crew.task.restarted', 'agent_id': cp['manifest']['tasks'][start]['agent_id'],
                'task_index': start, 'input_cursor': state.cursor, 'resume_mode': 'restart_task',
                'content': f'Task {start+1} restarted using the saved context at event {state.cursor}.',
            }, utc_now(), {'origin': 'crewai', 'runtime_id': self.id})])
        crew.tasks = tasks[start:end]
        with CrewObserver(crew, emit, config=cp, start=start, existing=True, strict=True) as observer:
            observer.metadata = deepcopy(state.environment.metadata)
            observer.metadata['crewai'] = observer.config
            crew.kickoff(inputs=deepcopy(cp['inputs']))
