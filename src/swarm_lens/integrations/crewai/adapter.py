"""CrewAI 1.15.23 hooks. No model invocation or orchestration is replaced."""
from copy import deepcopy
from importlib.metadata import version
import inspect
import json
from threading import RLock
import warnings
from weakref import WeakSet

from swarm_lens.core.models import DomainError, Fact, new_id, utc_now
from swarm_lens.live.service import digest

CREWAI_VERSION = '1.15.23'
ADAPTER_VERSION = '1.0.0'
_observers = WeakSet()
_registry_lock = RLock()
_installed = False


def check_version():
    if version('crewai') != CREWAI_VERSION:
        raise DomainError(f'This adapter requires crewai=={CREWAI_VERSION}')


def text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=lambda obj: f'<{type(obj).__name__}>')


def manifest(crew):
    from crewai.utilities.constants import NOT_SPECIFIED
    agents = {id(a): f'crew-agent-{i}' for i, a in enumerate(crew.agents)}
    tasks = {id(t): i for i, t in enumerate(crew.tasks)}
    return {
        'framework_version': CREWAI_VERSION,
        'process': str(crew.process.value),
        'agents': [{'id': agents[id(a)], 'role': a.role, 'goal': a.goal, 'backstory': a.backstory,
                    'model': getattr(a.llm, 'model', None), 'tools': [t.name for t in a.tools],
                    'allow_delegation': a.allow_delegation} for a in crew.agents],
        'tasks': [{'description': t.description, 'expected_output': t.expected_output,
                   'agent_id': agents.get(id(t.agent)), 'tools': [tool.name for tool in t.tools or []],
                   'context': 'previous' if t.context is NOT_SPECIFIED else [tasks.get(id(c)) for c in t.context or []],
                   'async_execution': t.async_execution} for t in crew.tasks],
    }


def replay_reason(crew):
    from crewai import Process, Task
    if crew.process != Process.sequential or crew.stream:
        return 'Branch execution currently supports sequential, non-streaming crews.'
    if crew.memory or crew.knowledge_sources or crew.planning:
        return 'This crew uses external memory, knowledge, or planning state that is not restorable by this adapter.'
    if crew.before_kickoff_callbacks or crew.after_kickoff_callbacks or crew.task_callback or crew.step_callback:
        return 'Crew callbacks need an application-specific restore adapter.'
    for agent in crew.agents:
        if agent.allow_delegation or agent.knowledge_sources or agent.memory or agent.step_callback:
            return 'Delegation, agent memory/knowledge, and custom callbacks need an application-specific restore adapter.'
    for task in crew.tasks:
        if type(task) is not Task or task.async_execution or task.human_input or task.callback or task.output_file or task.output_json or task.output_pydantic or task.guardrail or task.guardrails:
            return 'Branch execution requires synchronous text tasks without callbacks, guardrails, human input, or output files.'
    return None


def _dispatch(method, context):
    with _registry_lock:
        observers = list(_observers)
    for observer in observers:
        if id(getattr(context, 'agent', None)) in observer.agent_ids:
            observer.safe(method, context)
    # Returning None preserves the source application's request/result.


def _install_hooks():
    global _installed
    with _registry_lock:
        if _installed:
            return
        from crewai.hooks import (register_before_llm_call_hook, register_after_llm_call_hook,
                                 register_before_tool_call_hook, register_after_tool_call_hook)
        register_before_llm_call_hook(lambda ctx: _dispatch('before_llm', ctx))
        register_after_llm_call_hook(lambda ctx: _dispatch('after_llm', ctx))
        register_before_tool_call_hook(lambda ctx: _dispatch('before_tool', ctx))
        register_after_tool_call_hook(lambda ctx: _dispatch('after_tool', ctx))
        # Native memory notifications are observational only; they never create a replay checkpoint.
        from crewai.events import crewai_event_bus, MemorySaveCompletedEvent, MemoryRetrievalCompletedEvent
        def memory_event(source, event):
            with _registry_lock:
                observers = list(_observers)
            for observer in observers:
                if event.agent_id in observer.native_agents or event.task_id in observer.native_tasks:
                    observer.safe('native_memory', event)
        crewai_event_bus.on(MemorySaveCompletedEvent)(memory_event)
        crewai_event_bus.on(MemoryRetrievalCompletedEvent)(memory_event)
        _installed = True


class CrewObserver:
    def __init__(self, crew, emit, *, runtime_id=None, revision=None, inputs=None, config=None,
                 start=0, existing=False, strict=False):
        check_version()
        self.crew, self.emit, self.strict = crew, emit, strict
        self.lock = RLock()
        self.errors = []
        self.agent_ids = {id(a): f'crew-agent-{i}' for i,a in enumerate(crew.agents)}
        self.native_agents = {str(a.id): self.agent_ids[id(a)] for a in crew.agents}
        self.native_tasks = {str(t.id): start+i for i,t in enumerate(crew.tasks)}
        self.task_indices = {id(t): start+i for i,t in enumerate(crew.tasks)}
        self.tools, self.calls, self.last_messages = {}, {}, {}
        self.existing = existing
        self.config = deepcopy(config) if config else {
            'runtime_id': runtime_id, 'revision': revision, 'framework_version': CREWAI_VERSION,
            'adapter_version': ADAPTER_VERSION, 'manifest': manifest(crew),
            'inputs': deepcopy(inputs or {}), 'next_task': start, 'total_tasks': len(crew.tasks),
            'phase': 'boundary', 'replay_reason': replay_reason(crew),
        }
        self.config['manifest_digest'] = digest(self.config['manifest'])
        self.metadata = {'crewai': self.config}
        self.callbacks = []

    def fact(self, kind, data, *, at=None, source=None):
        return Fact(kind, data, at or utc_now(), {'origin': 'crewai', 'framework_version': CREWAI_VERSION,
                   'adapter_version': ADAPTER_VERSION, **(source or {})})

    def phase(self, phase, **changes):
        self.config.update(phase=phase, **changes)
        return self.fact('environment.updated', {'metadata': deepcopy(self.metadata)})

    def safe(self, method, *args):
        with self.lock:
            try:
                getattr(self, method)(*args)
            except Exception as exc:
                self.errors.append(type(exc).__name__)
                warnings.warn(f'SwarmLens capture incomplete ({type(exc).__name__}); agent execution continues.', RuntimeWarning)

    def __enter__(self):
        _install_hooks()
        if not self.existing:
            facts = [self.fact('agent.added', {'id': self.agent_ids[id(a)], 'name': a.role,
                     'model': getattr(a.llm, 'model', None), 'system_prompt': a.backstory,
                     'metadata': {'goal': a.goal, 'prompt_mapping': 'CrewAI backstory; full model input is recorded in memory',
                                  'tools': [t.name for t in a.tools]}}) for a in self.crew.agents]
            facts += [self.fact('channel.created', {'id': 'crew', 'name': 'CrewAI task exchange', 'members': list(self.agent_ids.values())}),
                      self.fact('environment.updated', {'task': self.crew.name or 'CrewAI execution',
                                'goal': self.crew.tasks[-1].expected_output if self.crew.tasks else '',
                                'metadata': deepcopy(self.metadata)})]
            self.emit(facts)
        with _registry_lock:
            if any(set(self.agent_ids) & set(o.agent_ids) for o in _observers):
                raise DomainError('This CrewAI instance is already being observed')
            _observers.add(self)
        for task in self.crew.tasks:
            old = task.callback
            def callback(output, task=task, old=old):
                if old:
                    result = old(output)
                    if inspect.isawaitable(result):
                        import asyncio
                        asyncio.run(result)
                self.safe('completed_task', task, output)
            self.callbacks.append((task, old))
            task.callback = callback
        return self

    def __exit__(self, exc_type, exc, tb):
        from crewai.events import crewai_event_bus
        if not crewai_event_bus.flush(timeout=5):
            self.errors.append('EventFlushTimeout')
        with _registry_lock:
            _observers.discard(self)
        for task, callback in self.callbacks:
            task.callback = callback
        with self.lock:
            if exc_type or self.errors:
                self.safe('failed', exc_type.__name__ if exc_type else 'CaptureIncomplete')
            else:
                self.emit([self.fact('observation.recorded', {'type': 'crew.completed', 'content': 'Crew execution finished'})])
        if not exc_type and self.errors and self.strict:
            raise DomainError('CrewAI capture failed; branch events may be incomplete')

    def failed(self, error_type):
        self.emit([self.phase('failed'), self.fact('observation.recorded', {'type': 'crew.failed', 'error_type': error_type,
                   'content': 'Execution or capture did not finish successfully'})])

    def before_llm(self, ctx):
        aid = self.agent_ids[id(ctx.agent)]
        call_id = new_id()
        self.calls[id(ctx.agent)] = call_id
        self.emit([self.phase('running'), self.fact('memory.written', {'id': f'prompt-{aid}', 'owner_id': aid,
                   'content': text(ctx.messages), 'metadata': {'type': 'model_input', 'call_id': call_id,
                   'task_index': self.task_indices.get(id(ctx.task)), 'model': getattr(ctx.llm, 'model', None)}})])

    def after_llm(self, ctx):
        aid = self.agent_ids[id(ctx.agent)]
        mid = new_id()
        self.last_messages[id(ctx.task)] = mid
        self.emit([self.fact('message.created', {'id': mid, 'channel_id': 'crew', 'sender_id': aid,
                   'content': text(ctx.response), 'metadata': {'type': 'model_response', 'call_id': self.calls.get(id(ctx.agent)),
                   'task_index': self.task_indices.get(id(ctx.task))}})])

    def before_tool(self, ctx):
        aid, tid = self.agent_ids[id(ctx.agent)], new_id()
        self.tools[(id(ctx.agent), ctx.tool_name)] = tid
        self.emit([self.phase('running'), self.fact('tool.started', {'id': tid, 'agent_id': aid,
                   'tool_name': ctx.tool_name, 'arguments': deepcopy(ctx.tool_input), 'status': 'running'})])

    def after_tool(self, ctx):
        tid = self.tools.pop((id(ctx.agent), ctx.tool_name), None)
        if tid:
            self.emit([self.fact('tool.completed', {'id': tid, 'status': 'completed', 'result': text(ctx.tool_result),
                       'metadata': {'outcome': 'returned; inspect result for application-level success'}})])

    def completed_task(self, task, output):
        index = self.task_indices[id(task)]
        aid = self.agent_ids[id(task.agent)]
        data = {'id': f'crew-task-{index}', 'owner_id': aid, 'content': output.raw,
                'metadata': {'type': 'task_output', 'task_index': index, 'description': task.description,
                             'message_id': self.last_messages.get(id(task))}}
        facts = [self.fact('memory.written', data), self.fact('observation.recorded', {
            'type': 'crew.task.completed', 'agent_id': aid, 'task_index': index, 'content': output.raw})]
        # Native callback runs synchronously, after the task result is assigned.
        # A boundary is its own event, so an earlier cursor cannot see this checkpoint.
        facts.append(self.phase('boundary', next_task=index+1))
        self.emit(facts)

    def native_memory(self, event):
        aid = self.native_agents.get(event.agent_id)
        content = getattr(event, 'value', None) or getattr(event, 'memory_content', '')
        self.emit([self.fact('memory.written', {'id': f'native-memory-{event.event_id}', 'owner_id': aid,
                   'scope': 'agent' if aid else 'shared', 'content': content,
                   'metadata': {'type': event.type, 'restorable': False}}, at=event.timestamp.isoformat(),
                   source={'source_event_id': event.event_id, 'emission_sequence': event.emission_sequence})])
