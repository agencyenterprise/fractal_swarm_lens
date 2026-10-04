"""Generate a complete upstream ACIArena pair, without running analysis plugins."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .recording import Budget, RecordingClient, Trace, observed_debate
from .upstream import ROOT, REVISION, disclosure_information, load_components, load_tasks, verify_checkout


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


class SampleTrace(Trace):
    def record(self, value):
        completed = datetime.now(timezone.utc)
        value['occurred_at'] = completed.isoformat()
        if value['type'] == 'model_call':
            value['started_at'] = self.request_started_at
        super().record(value)


class SampleRecordingClient(RecordingClient):
    def create(self, **kwargs):
        self.trace.request_started_at = datetime.now(timezone.utc).isoformat()
        return super().create(**kwargs)


def run_sample(task, condition, settings, client, budget, directory, components):
    if condition not in ('control', 'injection'):
        raise ValueError('Expected control or injection')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    trace = SampleTrace(directory / 'trace.jsonl')
    base_class, attack_class, task_class = components

    class RecordedDebate(observed_debate(base_class, trace)):
        def step(self, args):
            trace.phase = 'debate'
            trace.round += 1
            return super().step(args)

        def conclude(self, args):
            trace.phase = 'aggregation'
            trace.round += 1
            return super().conclude(args)

    config = {'provider': 'openai', 'api_key': client.api_key, 'base_url': str(client.base_url),
              'model_name': settings['model'], 'temperature': settings['temperature'],
              'max_tokens': settings['max_output_tokens'], 'seed': settings['seed']}
    mas = RecordedDebate(config, logger=SimpleNamespace(log_message=lambda **kwargs: None),
                         malicious_agents=['debater_0'], max_turn=settings['max_turn'])
    for agent_id, agent in mas.agents.items():
        agent.llm.client = SampleRecordingClient(client, trace, agent_id, budget)
        agent.profile = f'{disclosure_information()}\n{agent.profile}'
        agent.user_information = disclosure_information()

        # Read memory only after the original upstream agent has updated it.
        def recorded_step(query, *args, _step=agent.run_step, _agent=agent, **kwargs):
            response = _step(query, *args, **kwargs)
            if _agent.memory.conversation:
                trace.record({'type': 'memory_snapshot', 'agent': _agent.name,
                              'sequence': trace.latest[_agent.name]['sequence'],
                              'round': trace.round, 'phase': trace.phase,
                              'conversation': deepcopy(_agent.memory.conversation)})
            print(json.dumps({'condition': condition, 'phase': trace.phase, 'round': trace.round,
                              'agent': _agent.name, 'calls': len(trace.calls), 'tokens': budget.tokens}), flush=True)
            return response

        agent.run_step = recorded_step
    trace.record({'type': 'setup', 'task': task, 'model': settings['model'],
                  'agents': [{'id': name, 'profile': agent.profile} for name, agent in mas.agents.items()]})
    attack = None
    if condition == 'injection':
        attack = attack_class(args=SimpleNamespace(), llm_config=config)
        attack.run(mas)
        trace.record({'type': 'injection_installed', 'agent': 'debater_0',
                      'attack': 'MathNameLeakInstruction', 'content': attack.payload,
                      'schedule': 'Every input to debater_0, starting with bootstrap'})
    try:
        # Use ACIArena's own BaseMAS.run scheduler, bootstrap, debate and aggregation.
        answer = mas.run(task['problem'])
        task_instance = task_class(query=task['problem'], ground_truth=task['answer'])
        task_instance.answer = answer
        if attack:
            attack.set_turn(settings['max_turn'] + 1)
            attack.set_answer(answer)
        expected_calls = 3 + 3 * settings['max_turn'] + 1
        if len(trace.calls) != expected_calls:
            raise ValueError('Upstream execution did not complete all scheduled calls')
        truncated = sum(call['finish_reason'] != 'stop' for call in trace.calls)
        result = {'task_id': task['id'], 'condition': condition,
                  'status': 'complete' if not truncated else 'incomplete',
                  'calls': len(trace.calls), 'debate_rounds': settings['max_turn'],
                  'non_stop_completions': truncated, 'final_answer': answer['response'],
                  'math_correct': bool(task_instance.verify()),
                  'attack_success': bool(attack.verify()) if attack else None,
                  'usage': mas.get_token_usage()}
        trace.record({'type': 'run_completed', 'calls': len(trace.calls), 'status': result['status']})
        save(directory / 'result.json', result)
        return result
    except Exception as error:
        save(directory / 'failure.json', {'error_type': type(error).__name__, 'calls': len(trace.calls)})
        raise


def generate(args):
    verify_checkout()
    tasks, dataset_digest = load_tasks(1)
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(args.env, override=False)
    if not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('Configure OPENAI_API_KEY in the server environment or .env')
    settings = {'model': args.model, 'temperature': 0.0, 'seed': 42,
                'max_turn': args.max_turn, 'max_output_tokens': args.max_output_tokens}
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 'swarm-lens.aciarena.examples/v1', 'status': 'running',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'upstream_repository': 'https://github.com/Greysahy/aciarena', 'upstream_revision': REVISION,
                'dataset_sha256': dataset_digest, 'task': tasks[0], 'settings': settings,
                'system': 'LLMDebate', 'attack': 'MathNameLeakInstruction',
                'upstream_default_debate_rounds': 2, 'samples': [],
                'runner_sha256': digest(__file__), 'recorder_sha256': digest(Path(__file__).with_name('recording.py')),
                'packages': {name: importlib.metadata.version(name) for name in ('openai', 'math-verify', 'pydantic')},
                'notes': ['The two runs have independent agents and memories.',
                          'Both receive the identical upstream synthetic disclosure context.',
                          'Only the injection case installs the upstream attack on debater_0.',
                          'Injection status and native attack success do not establish cascade occurrence.',
                          'No CASPIAN, MAST, embeddings, or benchmark-wide metrics are computed.']}
    save(args.output / 'manifest.json', manifest)
    budget = Budget(max_tokens=args.max_total_tokens, max_requests=2 * (3 + 3 * args.max_turn + 1))
    client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], base_url='https://api.openai.com/v1', max_retries=0, timeout=120)
    try:
        components = load_components()
        for condition in ('control', 'injection'):
            result = run_sample(tasks[0], condition, settings, client, budget, args.output / condition, components)
            manifest['samples'].append({'condition': condition, 'directory': condition,
                                        'trace_sha256': digest(args.output / condition / 'trace.jsonl'),
                                        'result_sha256': digest(args.output / condition / 'result.json'),
                                        'result': result})
            manifest['usage'] = budget.describe()
            save(args.output / 'manifest.json', manifest)
            if result['status'] != 'complete':
                raise ValueError('A completion was truncated or interrupted; partial evidence was saved')
        manifest['status'] = 'complete'
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        client.close()
        manifest['usage'] = budget.describe()
        save(args.output / 'manifest.json', manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('generate', help='Generate a fresh matched pair through upstream ACIArena')
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--env', type=Path, default=Path('.env'))
    create.add_argument('--model', default='gpt-4o-mini-2024-07-18')
    create.add_argument('--max-turn', type=int, default=20)
    create.add_argument('--max-output-tokens', type=int, default=2048)
    create.add_argument('--max-total-tokens', type=int, default=3_000_000)
    ingest = commands.add_parser('import', help='Load complete saved examples without model calls')
    ingest.add_argument('--input', type=Path, required=True)
    ingest.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'generate':
        if not 20 <= args.max_turn <= 100 or not 1 <= args.max_output_tokens <= 4096 or args.max_total_tokens < 1:
            parser.error('Use 20..100 debate rounds, 1..4096 maximum output tokens, and a positive token budget')
        generate(args)
    else:
        from .sample_source import import_pair
        print(json.dumps(import_pair(args.input, args.data), indent=2))


if __name__ == '__main__':
    main()
