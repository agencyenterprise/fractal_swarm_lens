"""Import complete ACIArena traces as individually inspectable Swarm Lens events."""
import hashlib
import json
from pathlib import Path

from swarm_lens import Fact, Framework
from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.adapters.sqlite import SQLiteHistory

from .upstream import manifest_scenario

ATTACK_LABELS = {'instruction_injection': ('With injection', 'Instruction injection installed'),
                 'malicious_agent': ('With malicious agent', 'Malicious agent installed')}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


MAD_TURN = ('affirmative', 'negative', 'moderator')
STATELESS_AGENTS = {'aggregator'}  # Rebuilds its messages each call, so it has no memory to snapshot


def debate_rounds(calls):
    return max((call['round'] for call in calls if call['phase'] == 'debate'), default=0)


def expected_schedule(system, calls, max_turn):
    """The (phase, round, agent) order the upstream scheduler must have produced."""
    if system == 'LLMDebate':
        schedule = [('bootstrap', 0, f'debater_{i}') for i in range(3)]
        schedule += [('debate', turn, f'debater_{i}') for turn in range(1, max_turn + 1) for i in range(3)]
        return schedule + [('aggregation', max_turn + 1, 'aggregator')]
    if system == 'MAD':
        # MAD stops at the first moderator decision; only an undecided final round reaches the judge.
        rounds = debate_rounds(calls)
        if rounds > max_turn:
            raise ValueError('MAD ran more debate rounds than configured')
        schedule = [('bootstrap', 0, agent) for agent in MAD_TURN]
        schedule += [('debate', turn, agent) for turn in range(1, rounds + 1) for agent in MAD_TURN]
        if rounds == max_turn and calls and calls[-1]['agent'] == 'judge':
            schedule.append(('aggregation', rounds + 1, 'judge'))
        return schedule
    raise ValueError(f'Unknown upstream system: {system}')


def load_pair(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('schema') != 'swarm-lens.aciarena.examples/v1' or manifest.get('status') != 'complete':
        raise ValueError('Only complete ACIArena example pairs can be imported')
    if sorted(item['condition'] for item in manifest['samples']) != ['control', 'injection']:
        raise ValueError('A pair must contain exactly one control and one injection example')
    rows_by_condition = {}
    max_turn = manifest['settings']['max_turn']
    for sample in manifest['samples']:
        condition = sample['condition']
        if sample['directory'] != condition:
            raise ValueError('Unexpected sample directory')
        for filename, key in [('trace.jsonl', 'trace_sha256'), ('result.json', 'result_sha256')]:
            if sha256(directory / condition / filename) != sample[key]:
                raise ValueError(f'Example checksum mismatch: {condition}/{filename}')
        rows = [json.loads(line) for line in (directory / condition / 'trace.jsonl').read_text().splitlines()]
        calls = [row for row in rows if row['type'] == 'model_call']
        schedule = expected_schedule(manifest['system'], calls, max_turn)
        if sample['result']['status'] != 'complete' or sample['result']['calls'] != len(calls):
            raise ValueError('The example is missing scheduled model responses')
        if [call['sequence'] for call in calls] != list(range(1, len(calls) + 1)):
            raise ValueError('Model-call sequence is incomplete')
        if [(call['phase'], call['round'], call['agent']) for call in calls] != schedule:
            raise ValueError('The example does not follow the upstream debate schedule')
        if json.loads((directory / condition / 'result.json').read_text()) != sample['result']:
            raise ValueError('The manifest differs from the native result record')
        if any(call['finish_reason'] != 'stop' for call in calls):
            raise ValueError('A model response is truncated or incomplete')
        for call in calls:
            if any(source['sequence'] >= call['sequence'] for source in call['sources']):
                raise ValueError('A receiving agent references a future response')
            for source in call['sources']:
                original = calls[source['sequence'] - 1]
                if original['response'] != source['response'] or original['agent'] != source['agent']:
                    raise ValueError('A delivered response differs from its source')
        stateful_calls = [call for call in calls if call['agent'] not in STATELESS_AGENTS]
        if len([row for row in rows if row['type'] == 'memory_snapshot']) != len(stateful_calls):
            raise ValueError('Upstream conversation-memory snapshots are missing')
        if len([row for row in rows if row['type'] == 'injection_installed']) != int(condition == 'injection'):
            raise ValueError('Injection provenance does not match the example condition')
        if rows[-1]['type'] != 'run_completed' or rows[-1]['status'] != 'complete':
            raise ValueError('The example did not finish')
        rows_by_condition[condition] = rows
    return manifest, rows_by_condition


def stage(call):
    return {'bootstrap': 'Initialization', 'aggregation': 'Final aggregation'}.get(call['phase'], f"Debate round {call['round']}")


class ExampleSource:
    def __init__(self, manifest, rows, artifacts):
        self.manifest, self.rows, self.artifacts = manifest, rows, artifacts

    def facts(self):
        setup = next(row for row in self.rows if row['type'] == 'setup')
        at = setup['occurred_at']
        scenario = manifest_scenario(self.manifest)
        provenance = {'adapter': 'aciarena-example/v1', 'upstream_revision': self.manifest['upstream_revision']}
        yield Fact('environment.updated', {'task': setup['task']['problem'], 'goal': scenario.goal}, at, provenance)
        for agent in setup['agents']:
            yield Fact('agent.added', {'id': agent['id'], 'name': agent['id'].replace('_', ' ').title(),
                                      'model': setup['model'], 'system_prompt': agent['profile']}, at, provenance)
        yield Fact('channel.created', {'id': 'debate', 'name': 'Debate outputs',
                                      'members': [agent['id'] for agent in setup['agents']],
                                      'metadata': {'meaning': 'Recorded outputs; actual deliveries are recorded on each receiving model input.'}}, at, provenance)
        yield Fact('message.created', {'id': 'task', 'channel_id': 'debate', 'role': 'human', 'sender_name': 'Task',
                                      'content': setup['task']['problem'], 'metadata': {'stage_label': 'Original task'}}, at, provenance)
        for row in self.rows:
            kind = row['type']
            at = row['occurred_at']
            source = {**provenance, 'record_type': kind}
            if 'sequence' in row:
                source.update(sequence=row['sequence'], round=row['round'], phase=row['phase'])
            if kind == 'injection_installed':
                yield Fact('observation.recorded', {'type': scenario.attack_path, 'agent_id': row['agent'],
                    'content': row['content'], 'schedule': row['schedule'], 'attack': row['attack'],
                    'metadata': {'stage_label': ATTACK_LABELS[scenario.attack_path][1]}}, at,
                    # Part of the recorded run, not a change to it: an observation, not an intervention.
                    {**source, 'actor': 'ACIArena', 'applied_to_runtime': True})
            elif kind == 'model_call':
                artifact = self.artifacts.put(json.dumps(row, ensure_ascii=False).encode())
                delivered = [f"call-{item['sequence']}" for item in row['sources']]
                yield Fact('observation.recorded', {'type': 'model_input', 'agent_id': row['agent'],
                    'content': row['messages'][-1]['content'], 'source_message_ids': delivered,
                    'metadata': {'stage_label': f'{stage(row)} · model input', 'model_output_artifact': artifact}},
                    row['started_at'], {**source, 'delivered_sources': delivered})
                yield Fact('message.created', {'id': f"call-{row['sequence']}", 'channel_id': 'debate',
                    'sender_id': row['agent'], 'content': row['response'],
                    'metadata': {'round': row['round'], 'phase': row['phase'], 'stage_label': stage(row),
                                 'sources': delivered, 'model_output_artifact': artifact,
                                 'usage': row['usage'], 'latency_seconds': row['latency_seconds']}},
                    at, {**source, 'delivered_sources': delivered})
            elif kind == 'memory_snapshot':
                yield Fact('memory.written', {'id': f"conversation-{row['agent']}", 'owner_id': row['agent'],
                    'scope': 'agent', 'content': json.dumps(row['conversation'], ensure_ascii=False, indent=2),
                    'metadata': {'stage_label': f'{stage(row)} · conversation memory',
                                 'sequence': row['sequence'], 'source': 'agent.memory.conversation'}}, at, source)
            elif kind == 'run_completed':
                yield Fact('observation.recorded', {'type': 'run_completed', 'content': 'Full upstream execution completed.',
                    'model_calls': row['calls'], 'metadata': {'stage_label': 'Execution completed'}}, at, source)


def import_pair(directory, data):
    manifest, traces = load_pair(directory)
    pair_id = sha256(Path(directory) / 'manifest.json')
    framework = Framework(SQLiteHistory(Path(data) / 'history.sqlite'))
    artifacts = FileArtifacts(Path(data) / 'artifacts')
    imported = []
    scenario = manifest_scenario(manifest)
    attack_label = ATTACK_LABELS[scenario.attack_path][0]
    for condition in ('control', 'injection'):
        label = 'Without attack' if condition == 'control' else attack_label
        sample = next(item for item in manifest['samples'] if item['condition'] == condition)
        key = f'{pair_id}:{condition}'
        existing = next((run for run in framework.store.runs() if run.metadata.get('example_key') == key), None)
        if existing:
            branch = next(b for b in framework.store.branches(existing.id) if not b.parent_id)
        else:
            rounds = sample['result']['debate_rounds']
            title = f"ACIArena · {manifest['system']} · {manifest['task']['id']} · {label} · {rounds} rounds"
            branch = framework.create_run(title, metadata={
                'source_type': 'aciarena_example', 'example_key': key, 'example_pair': pair_id,
                'condition': condition, 'condition_label': label, 'max_turn': rounds,
                'team': ', '.join(agent['id'] for agent in next(r for r in traces[condition] if r['type'] == 'setup')['agents']),
                'task_title': f"ACIArena · {manifest['task']['id']}",
                'task_id': manifest['task']['id'], 'system': manifest['system'], 'settings': manifest['settings'],
                'dataset_url': manifest['upstream_repository'], 'attribution': f"ACIArena · upstream {manifest['system']}",
                'upstream_revision': manifest['upstream_revision'], 'trace_sha256': sample['trace_sha256'],
                'start': traces[condition][0]['occurred_at'], 'native_result': sample['result']})
        facts = list(ExampleSource(manifest, traces[condition], artifacts).facts())
        # Finish a previously interrupted local import without duplicating its prefix.
        history = framework.history(branch.id)
        if len(history) > len(facts) or any(
            event.kind != fact.kind or event.occurred_at != fact.occurred_at
            or any(event.data.get(key) != value for key, value in fact.data.items())
            for event, fact in zip(history, facts)
        ):
            raise ValueError('The existing example history differs from the saved trace')
        class Remaining:
            def facts(self):
                return iter(facts[len(history):])
        framework.ingest(branch.id, Remaining(), expected_head=len(history))
        state = framework.state(branch.id)
        imported.append({'condition': condition, 'branch_id': branch.id, 'events': state.cursor,
                         'model_responses': sample['result']['calls'], 'messages': len(state.messages),
                         'memory_slots': len(state.memories)})
    return imported
