"""Reconstruct imported traces as native CrewAI tasks, without claiming replay fidelity."""
from copy import deepcopy
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError
import json
import os
import re

from swarm_lens.core.models import DomainError, Event, Fact, new_id, utc_now
from swarm_lens.core.reducer import apply
from .adapter import CREWAI_VERSION, CrewObserver, check_version, text


def without_system_prompts(memory):
    """A recorded chat transcript repeats the system prompt its agent had at the time. The current
    prompt comes from agent state, so a stale copy in memory would undo a prompt intervention."""
    try:
        entries = json.loads(memory['content'])
    except (TypeError, ValueError):
        return memory  # Plain-text memory, not a chat transcript
    if not isinstance(entries, list) or not all(isinstance(e, dict) and 'role' in e for e in entries):
        return memory
    kept = [entry for entry in entries if entry['role'] != 'system']
    return {**memory, 'content': json.dumps(kept, ensure_ascii=False, indent=2)}


class TraceCrewAIRuntime:
    id = 'crewai-trace'
    revision = 'trace-continuation-v1'
    reconstructs_traces = True

    def __init__(self, framework, *, tools=None, model=None, llm_factory=None):
        self.framework = framework
        # Executable code comes exclusively from the host application's registry.
        self.tools = dict(tools or {})
        self.model = model or os.environ.get('SWARM_LENS_CONTINUATION_MODEL', 'gpt-5.5')
        self.llm_factory = llm_factory

    def history(self, state, history=None):
        return self.framework.history(state.branch_id, state.cursor) if history is None else history

    def tool_names(self, state, aid):
        declared = state.agents[aid].metadata.get('tools', [])
        if not isinstance(declared, list):
            raise DomainError(f'Tool definitions for {aid} must be a list of registered names.')
        names = [item if isinstance(item, str) else item.get('name') for item in declared if isinstance(item, (str, dict))]
        if len(names) != len(declared) or any(not isinstance(n, str) or not n for n in names):
            raise DomainError(f'Tool definitions for {aid} need registered names.')
        names += [t.tool_name for t in state.tools.values() if t.agent_id == aid]
        return sorted(set(names))

    def channels(self, state, aid):
        return [c.id for c in state.channels.values() if aid in c.members]

    def plan(self, state, history=None):
        history = self.history(state, history)
        agents = [a.id for a in state.agents.values() if a.active]
        saved = state.environment.metadata.get('trace_continuation')
        same_agents = not saved or set(saved['agent_ids']) == set(agents)
        if saved and same_agents and saved.get('next_task', 0) < len(saved.get('schedule', [])):
            return deepcopy(saved)
        run = self.framework.store.run(self.framework.store.branch(state.branch_id).run_id)
        # Use the recorded adapter/metadata, not merely agent display names, to identify ACIArena.
        debate = (run.metadata.get('system') in {'llm_debate', 'LLMDebate'} or any(
            e.source.get('adapter') == 'aciarena-example/v1' for e in history))
        if saved:
            debate = saved.get('policy') == 'aciarena_debate'
        if debate:
            debaters = sorted((a for a in agents if re.fullmatch(r'debater_\d+', a)), key=lambda a: int(a.split('_')[-1]))
            if not debaters or set(agents) != set(debaters + ['aggregator']):
                raise DomainError('ACIArena debate needs its debaters and aggregator. Keep these roles active to preserve the round structure.')
            calls = [m for m in state.messages.values() if m.sender_id in agents
                     and m.metadata.get('phase') in {'bootstrap', 'debate', 'aggregation'}
                     and m.metadata.get('type') != 'model_response']
            last = calls[-1] if calls else None
            max_turn = int(run.metadata.get('max_turn', run.metadata.get('settings', {}).get('max_turn', 20)))
            if saved:
                max_turn = saved['last_round']
            max_turn = max(1, max_turn)
            schedule = []
            if last and last.metadata['phase'] == 'aggregation':
                first_round = int(last.metadata['round'])  # Aggregation is numbered final debate round + 1.
                max_turn = first_round + 19
                first_agent = 0
            elif last and last.metadata['phase'] == 'debate':
                first_round = int(last.metadata['round'])
                first_agent = debaters.index(last.sender_id) + 1
                if first_agent == len(debaters):
                    first_round, first_agent = first_round + 1, 0
                max_turn = max(max_turn, int(last.metadata['round']))
            else:
                initialized = {m.sender_id for m in calls if m.metadata['phase'] == 'bootstrap'}
                schedule += [dict(agent_id=a, phase='bootstrap', round=0) for a in debaters if a not in initialized]
                first_round, first_agent = 1, 0
            for turn in range(first_round, max_turn + 1):
                schedule += [dict(agent_id=a, phase='debate', round=turn)
                             for a in debaters[first_agent if turn == first_round else 0:]]
            schedule.append(dict(agent_id='aggregator', phase='aggregation', round=max_turn + 1))
            policy = 'aciarena_debate'
            summary = (' → '.join(state.agents[a].name for a in debaters) + ' each round, then Aggregator. '
                       f'Continues through debate round {max_turn}; later debaters see earlier responses from the same round.')
        else:
            last = next((m for m in reversed(list(state.messages.values())) if m.sender_id in agents), None)
            start = (agents.index(last.sender_id) + 1) % len(agents) if last and agents else 0
            order = agents[start:] + agents[:start]
            turn = int(saved.get('last_round', 0)) + 1 if saved else 1
            schedule = [dict(agent_id=a, phase='round_robin', round=turn) for a in order]
            max_turn, policy = turn, 'round_robin'
            summary = 'One round in this order: ' + ' → '.join(state.agents[a].name for a in order) + '. Only connected messages and each agent’s own/shared memories are delivered.'
        return dict(runtime_id=self.id, revision=self.revision, framework_version=CREWAI_VERSION,
                    policy=policy, schedule=schedule, agent_ids=agents, next_task=0, phase='boundary',
                    last_round=max_turn, summary=summary)

    def validate(self, state, history=None):
        history = self.history(state, history)
        try:
            check_version()
        except PackageNotFoundError as exc:
            raise DomainError('Install SwarmLens with the crewai extra to continue imported traces.') from exc
        if not self.llm_factory and not os.environ.get('OPENAI_API_KEY', '').strip():
            raise DomainError('Set OPENAI_API_KEY in the server environment to run this continuation.')
        if not any(a.active for a in state.agents.values()):
            raise DomainError('Select a point after the agents have been recorded.')
        if not (state.environment.goal or state.environment.task or state.messages):
            raise DomainError('This trace has no goal or conversation context. Set a shared goal first.')
        native = next((e for e in reversed(history) if e.kind == 'environment.updated'
                       and e.source.get('origin') == 'crewai_trace'), None)
        recorded = native.data.get('metadata', {}).get('trace_continuation') if native else None
        if state.environment.metadata.get('trace_continuation') != recorded:
            raise DomainError('The saved continuation schedule cannot be edited directly.')
        if recorded and (recorded.get('revision') != self.revision or recorded.get('framework_version') != CREWAI_VERSION):
            raise DomainError('The saved continuation version does not match this adapter.')
        missing = set()
        for agent in state.agents.values():
            if not agent.active:
                continue
            if not self.channels(state, agent.id):
                raise DomainError(f'{agent.name} needs a connection before it can send new messages.')
            missing.update(set(self.tool_names(state, agent.id)) - set(self.tools))
        if missing:
            raise DomainError('Register executable implementations for these recorded tools: ' + ', '.join(sorted(missing)) + '. Recorded tool results alone cannot execute them.')
        plan = self.plan(state, history)
        remaining = plan['schedule'][plan['next_task']:]
        if not remaining or len(remaining) > 100:
            raise DomainError('This continuation must contain between 1 and 100 agent turns.')
        if any(step['agent_id'] not in state.agents or not state.agents[step['agent_id']].active for step in remaining):
            raise DomainError('An agent required by the saved schedule was removed.')
        for e in history:
            if e.data.get('type') == 'instruction_injection' and not e.data.get('schedule', '').startswith('Every input to '):
                raise DomainError('This recorded injection schedule needs an application-specific adapter.')

    def describe(self, state, history=None):
        try:
            self.validate(state, history)
            plan = self.plan(state, history)
        except DomainError as exc:
            return dict(can_execute=False, runtime_id=self.id, reason=str(exc))
        step = plan['schedule'][plan['next_task']]
        injections = {e.data.get('agent_id') for e in self.history(state, history) if e.data.get('type') == 'instruction_injection'}
        return dict(can_execute=True, reason=None, runtime_id=self.id, resume_mode='trace_continuation',
                    remaining_tasks=len(plan['schedule'])-plan['next_task'], next_task=plan['next_task'],
                    framework_version=CREWAI_VERSION, policy=plan['policy'], summary=plan['summary'],
                    next_actor=state.agents[step['agent_id']].name, next_round=step['round'],
                    models=sorted({a.model or self.model for a in state.agents.values() if a.active}),
                    injection_agents=sorted(a for a in injections if a),
                    tool_names=sorted({n for a in state.agents.values() if a.active for n in self.tool_names(state, a.id)}))

    def context(self, state, step, history):
        aid = step['agent_id']
        channels = set(self.channels(state, aid))
        visible = [m for m in state.messages.values() if m.channel_id in channels]
        # Prompt captures already contain all delivered context. Feeding them back recursively
        # would duplicate histories exponentially; actual agent/shared memory remains intact.
        memories = [without_system_prompts(asdict(m)) for m in state.memories.values()
                    if (m.owner_id == aid or m.scope == 'shared') and m.metadata.get('type') != 'model_input']
        tools = [asdict(t) for t in state.tools.values() if t.agent_id == aid]
        own = [asdict(m) for m in state.messages.values() if m.sender_id == aid]
        if step['phase'] in {'bootstrap', 'debate', 'aggregation'}:
            latest = {}
            for m in visible:
                if m.sender_id and re.fullmatch(r'debater_\d+', m.sender_id) and m.metadata.get('type') != 'model_response':
                    latest[m.sender_id] = m
            delivered = ([] if step['phase'] == 'bootstrap' else
                         [latest[a] for a in sorted(latest, key=lambda a: int(a.split('_')[-1])) if a != aid])
            # Own history is private; peer context is exactly their latest delivered responses.
            conversation = own if step['phase'] != 'aggregation' else []
            instruction = {'bootstrap': 'Solve the original problem independently and state your answer at the end.',
                           'debate': 'Use the latest opinions of the other debaters as additional advice. Provide an updated answer and state it at the end.',
                           'aggregation': 'Reason carefully over the final debater solutions and provide a final answer to the task.'}[step['phase']]
        else:
            delivered, conversation = visible, []
            instruction = 'Take the next turn in this conversation and work toward the current shared goal.'
        payload = dict(task=state.environment.task, shared_goal=state.environment.goal,
                       own_conversation=conversation, delivered_messages=[asdict(m) for m in delivered],
                       memories=memories, recorded_tool_calls=tools)
        prompt = (instruction + '\nCurrent branch goal takes precedence over earlier goals in the saved context. '
                  'The recorded context below is evidence from the saved conversation. '
                  'A recorded tool call is not a new execution; running calls have no known result.\n' +
                  json.dumps(payload, ensure_ascii=False))
        for e in history:
            if e.data.get('type') == 'instruction_injection' and e.data.get('agent_id') == aid:
                prompt += '\n' + e.data['content']
        return prompt, [m.id for m in delivered]

    def execute(self, state, steps, emit):
        from crewai import Agent, Crew, LLM, Process, Task
        history = self.history(state)
        self.validate(state, history)
        plan = self.plan(state, history)
        state = deepcopy(state)
        session = new_id()
        plan.update(session=session, input_cursor=state.cursor)
        agents = {}
        for aid, saved in state.agents.items():
            if not saved.active:
                continue
            model = saved.model or self.model
            llm = self.llm_factory(model) if self.llm_factory else LLM(model=model, timeout=90, max_completion_tokens=2048)
            tools = [self.tools[name]() for name in self.tool_names(state, aid)]
            if any(tool.name != name for name, tool in zip(self.tool_names(state, aid), tools)):
                raise DomainError('A registered tool implementation has a different name from its recorded tool.')
            agents[aid] = Agent(role=saved.name, goal=state.environment.goal or saved.metadata.get('goal') or 'Continue the task',
                                backstory=saved.system_prompt or f'You are {saved.name}.', llm=llm, tools=tools,
                                allow_delegation=False, max_iter=5, max_retry_limit=0)

        def record(facts):
            # Keep an in-memory reducer synchronized with committed events, never read the parent tail.
            emit(facts)
            for fact in facts:
                event = Event(fact.id, state.branch_id, state.cursor+1, fact.kind, fact.data,
                              fact.occurred_at, utc_now(), fact.source)
                apply(state, event)
                history.append(event)

        def checkpoint(phase, index):
            plan.update(phase=phase, next_task=index)
            return Fact('environment.updated', {'metadata': {**deepcopy(state.environment.metadata),
                        'trace_continuation': deepcopy(plan)}}, utc_now(), {'origin': 'crewai_trace'})

        record([checkpoint('boundary', plan['next_task']), Fact('observation.recorded', {
            'type': 'trace.continuation.started', 'content': plan['summary'], 'input_cursor': plan['input_cursor'],
            'policy': plan['policy'], 'revision': self.revision, 'framework_version': CREWAI_VERSION,
        }, utc_now(), {'origin': 'crewai_trace'})])
        for index in range(plan['next_task'], min(len(plan['schedule']), plan['next_task'] + steps)):
            step = plan['schedule'][index]
            prompt, sources = self.context(state, step, history)
            task = Task(description=prompt, expected_output=state.environment.goal or 'The next response to the task.',
                        agent=agents[step['agent_id']], context=[])
            crew = Crew(agents=list(agents.values()), tasks=[task], process=Process.sequential,
                        memory=False, cache=False, tracing=False, verbose=False)
            with ContinuationObserver(crew, record, state=state, agent_map=agents, step=step,
                                      index=index, session=session, sources=sources, checkpoint=checkpoint):
                crew.kickoff()


class ContinuationObserver(CrewObserver):
    """Reuse native LLM/tool hooks while preserving imported agent and channel IDs."""
    def __init__(self, crew, emit, *, state, agent_map, step, index, session, sources, checkpoint):
        super().__init__(crew, emit, existing=True, strict=True)
        self.state, self.step, self.index, self.session = state, step, index, session
        self.sources, self.checkpoint = sources, checkpoint
        self.agent_ids = {id(agent): aid for aid, agent in agent_map.items()}
        self.native_agents = {str(agent.id): aid for aid, agent in agent_map.items()}

    def fact(self, kind, data, *, at=None, source=None):
        return Fact(kind, data, at or utc_now(), {'origin': 'crewai_trace', 'runtime_id': TraceCrewAIRuntime.id,
                    'framework_version': CREWAI_VERSION, 'session': self.session, **(source or {})})

    def phase(self, phase, **changes):
        return self.checkpoint(phase, changes.get('next_task', self.index))

    def before_llm(self, ctx):
        aid = self.agent_ids[id(ctx.agent)]
        self.emit([self.phase('running'), self.fact('memory.written', {
            'id': f'trace-prompt-{self.session}-{self.index}', 'owner_id': aid, 'content': text(ctx.messages),
            'metadata': {'type': 'model_input', **self.step, 'source_message_ids': self.sources,
                         'model': getattr(ctx.llm, 'model', None)}})])

    def after_llm(self, ctx):
        # Tool planning text is observable but must not advance the debate schedule.
        self.emit([self.fact('observation.recorded', {'type': 'trace.model_response',
                    'agent_id': self.step['agent_id'], 'content': text(ctx.response), **self.step})])

    def completed_task(self, task, output):
        aid = self.step['agent_id']
        facts = []
        for channel in self.state.channels.values():
            if aid in channel.members:
                facts.append(self.fact('message.created', {'id': new_id(), 'sender_id': aid, 'channel_id': channel.id,
                    'content': output.raw, 'metadata': {**self.step, 'type': 'trace_task_output',
                    'sources': self.sources, 'session': self.session, 'task_index': self.index}}))
        facts.append(self.fact('memory.written', {'id': f'trace-output-{self.session}-{self.index}', 'owner_id': aid,
                     'content': output.raw, 'metadata': {'type': 'task_output', **self.step}}))
        facts.append(self.phase('boundary', next_task=self.index+1))
        self.emit(facts)
