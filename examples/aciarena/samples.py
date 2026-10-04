"""Generate complete upstream ACIArena pairs, concurrently, without running analysis plugins."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx

from .recording import OBSERVERS, Budget, RecordingClient, Trace
from .sample_source import debate_rounds, expected_schedule
from .upstream import REVISION, SCENARIOS, disclosure_information, load_components, load_tasks, verify_checkout


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


class JudgementTrace:
    """Record the upstream attack judge outside the debate's model-call sequence."""

    def __init__(self, trace):
        self.trace = trace

    def append_call(self, agent, messages, response, elapsed, model, usage, finish_reason):
        self.trace.record({'type': 'attack_judgement', 'messages': deepcopy(messages), 'response': response,
                           'model': model, 'usage': usage, 'finish_reason': finish_reason})
        # Upstream reads .get("judgement", "yes"), which would count a malformed reply as a successful attack.
        if json.loads(response).get('judgement', '').lower() not in ('yes', 'no'):
            raise ValueError('The attack judge returned no yes/no judgement')


def install_attack(mas, attack_class, config, trace, client, budget, task):
    attack = attack_class(args=SimpleNamespace(task=task), llm_config=config)
    attack.llm_judge.client = RecordingClient(client, JudgementTrace(trace), 'attack_judge', budget)
    attack.run(mas)
    return attack


def run_sample(task, condition, settings, client, budget, directory, components, scenario=SCENARIOS['math-name-leak']):
    if condition not in ('control', 'injection'):
        raise ValueError('Expected control or injection')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    trace = SampleTrace(directory / 'trace.jsonl')
    base_class, attack_class, task_class = components

    class RecordedSystem(OBSERVERS[scenario.system](base_class, trace)):
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
    mas = RecordedSystem(config, logger=SimpleNamespace(log_message=lambda **kwargs: None),
                         malicious_agents=[scenario.malicious_agent], max_turn=settings['max_turn'])
    for agent_id, agent in mas.agents.items():
        agent.llm.client = SampleRecordingClient(client, trace, agent_id, budget)

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
    # Same order as upstream ContinuousAttackExecutor: install the attack, then plant user information.
    attack = install_attack(mas, attack_class, config, trace, client, budget, task) if condition == 'injection' else None
    if scenario.plants_user_information:
        for agent in mas.agents.values():
            agent.profile = f'{disclosure_information()}\n{agent.profile}'
            agent.user_information = disclosure_information()
    trace.record({'type': 'setup', 'task': task, 'model': settings['model'],
                  'agents': [{'id': name, 'profile': agent.profile} for name, agent in mas.agents.items()]})
    if attack:
        target = scenario.malicious_agent
        schedule = {'instruction_injection': f'Every input to {target}, starting with bootstrap',
                    'malicious_agent': f'System profile of {target} replaced before bootstrap'}[scenario.attack_path]
        if scenario.profile_fix:
            schedule += '; kept ahead of the profile the system sets at bootstrap (local fix)'
        trace.record({'type': 'injection_installed', 'agent': target, 'attack': scenario.attack,
                      'content': attack.payload, 'schedule': schedule})
    try:
        # Use ACIArena's own BaseMAS.run scheduler, bootstrap, debate and aggregation.
        answer = mas.run(task['problem'])
        answer.update(query=task['problem'], ground_truth=task['answer'])
        task_instance = task_class(query=task['problem'], ground_truth=task['answer'])
        task_instance.answer = answer
        rounds = debate_rounds(trace.calls)
        if attack:
            attack.set_turn(rounds + 1)
            attack.set_answer(answer)
        executed = [(call['phase'], call['round'], call['agent']) for call in trace.calls]
        if executed != expected_schedule(scenario.system, trace.calls, settings['max_turn']):
            raise ValueError('Upstream execution did not complete all scheduled calls')
        truncated = sum(call['finish_reason'] != 'stop' for call in trace.calls)
        result = {'task_id': task['id'], 'condition': condition,
                  'status': 'complete' if not truncated else 'incomplete',
                  'calls': len(trace.calls), 'debate_rounds': rounds,
                  'non_stop_completions': truncated, 'final_answer': answer['response'],
                  'task_correct': bool(task_instance.verify()),
                  'attack_success': bool(attack.verify()) if attack else None,
                  'usage': mas.get_token_usage()}
        trace.record({'type': 'run_completed', 'calls': len(trace.calls), 'status': result['status']})
        save(directory / 'result.json', result)
        return result
    except Exception as error:
        save(directory / 'failure.json', {'error_type': type(error).__name__, 'calls': len(trace.calls)})
        raise


CONDITIONS = ('control', 'injection')


@dataclass
class Pair:
    name: str
    scenario: object
    task: dict
    directory: Path
    manifest: dict
    budget: Budget
    components: tuple


def pair_manifest(name, scenario, task, dataset_digest, settings):
    manifest = {'schema': 'swarm-lens.aciarena.examples/v1', 'status': 'running',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'upstream_repository': 'https://github.com/Greysahy/aciarena', 'upstream_revision': REVISION,
                'dataset_sha256': dataset_digest, 'task': task, 'settings': settings, 'scenario': name,
                'system': scenario.system, 'attack': scenario.attack, 'attack_path': scenario.attack_path,
                'upstream_suite': scenario.suite,
                'upstream_default_debate_rounds': 2, 'samples': [],
                'runner_sha256': digest(__file__), 'recorder_sha256': digest(Path(__file__).with_name('recording.py')),
                'packages': {name: importlib.metadata.version(name) for name in ('openai', 'math-verify', 'pydantic')},
                'notes': ['The two runs have independent agents and memories and run concurrently.',
                          'Both receive the identical upstream synthetic disclosure context.'
                          if scenario.plants_user_information else
                          f'Neither receives synthetic user information; the upstream {scenario.suite} suite plants none.',
                          f'Only the injection case installs the upstream attack on {scenario.malicious_agent}.',
                          'Injection status and native attack success do not establish cascade occurrence.',
                          'No CASPIAN, MAST, embeddings, or benchmark-wide metrics are computed.',
                          'A judge reply without a yes/no judgement aborts the run; upstream would count it as attack success.']}
    if scenario.profile_fix:
        manifest['local_fixes'] = ['keep_malicious_profile: upstream MAD.init_prompt calls update_profile at bootstrap, '
                                   'which erases the MaliciousAgentAttack profile before the first model call. The payload '
                                   'is kept ahead of the profile MAD sets. The pinned upstream checkout is unmodified.']
    if scenario.attack_module == 'local':
        manifest['local_attack'] = (f'{scenario.attack} is defined in examples/aciarena/upstream.py, not in ACIArena. '
                                    'It replaces the target profile like upstream MaliciousAgentAttack and is graded '
                                    'with upstream QATask option extraction.')
    return manifest


def plan_pairs(args, settings):
    pairs = []
    for name in args.scenario:
        scenario = SCENARIOS[name]
        tasks, dataset_digest = load_tasks(max(args.task) + 1, domain=scenario.domain)
        components = load_components(scenario)
        for index in args.task:
            task = tasks[index]
            directory = args.output / f"{name}-{task['id']}"
            directory.mkdir()
            manifest = pair_manifest(name, scenario, task, dataset_digest, settings)
            save(directory / 'manifest.json', manifest)
            # Each run makes every scheduled debate call; the attack run may add one judge call.
            budget = Budget(max_tokens=args.max_total_tokens, max_requests=2 * (3 + 3 * args.max_turn + 1) + 1)
            pairs.append(Pair(name, scenario, task, directory, manifest, budget, components))
    return pairs


def finish_pair(pair, outcomes):
    """Record both runs in control-then-attack order; any failed or truncated run fails the pair."""
    errors = []
    for condition in CONDITIONS:
        outcome = outcomes[condition]
        if isinstance(outcome, Exception):
            errors.append(f'{condition}: {type(outcome).__name__}')
            continue
        pair.manifest['samples'].append({'condition': condition, 'directory': condition,
                                         'trace_sha256': digest(pair.directory / condition / 'trace.jsonl'),
                                         'result_sha256': digest(pair.directory / condition / 'result.json'),
                                         'result': outcome})
        if outcome['status'] != 'complete':
            errors.append(f'{condition}: a completion was truncated; partial evidence was saved')
    pair.manifest.update(status='failed' if errors else 'complete', usage=pair.budget.describe())
    if errors:
        pair.manifest['errors'] = errors
    save(pair.directory / 'manifest.json', pair.manifest)
    return errors


def generate(args):
    verify_checkout()
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(args.env, override=False)
    if not os.environ.get(args.api_key_env):
        raise ValueError(f'Configure {args.api_key_env} in the server environment or .env')
    settings = {'model': args.model, 'base_url': args.base_url, 'temperature': 0.0, 'seed': 42,
                'max_turn': args.max_turn, 'max_output_tokens': args.max_output_tokens}
    args.output.mkdir(parents=True, exist_ok=False)
    pairs = plan_pairs(args, settings)
    # Calls inside one run depend on each other; independent runs execute side by side.
    client = OpenAI(api_key=os.environ[args.api_key_env], base_url=args.base_url, max_retries=0, timeout=300,
                    http_client=httpx.Client(limits=httpx.Limits(max_connections=args.workers)))
    outcomes = {pair.directory: {} for pair in pairs}
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_sample, pair.task, condition, settings, client, pair.budget,
                                   pair.directory / condition, pair.components, pair.scenario): (pair, condition)
                       for pair in pairs for condition in CONDITIONS}
            for future in as_completed(futures):
                pair, condition = futures[future]
                outcomes[pair.directory][condition] = future.exception() or future.result()
    finally:
        client.close()
    failures = {pair.name + '/' + pair.task['id']: errors for pair in pairs
                if (errors := finish_pair(pair, outcomes[pair.directory]))}
    print(json.dumps({'output': str(args.output), 'pairs': len(pairs), 'failed': failures}, indent=2))
    if failures:
        raise SystemExit(f'{len(failures)} of {len(pairs)} pairs failed; see each manifest.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('generate', help='Generate fresh matched pairs through upstream ACIArena')
    create.add_argument('--output', type=Path, required=True, help='New directory; each pair goes in <scenario>-<task id>/')
    create.add_argument('--scenario', nargs='+', choices=sorted(SCENARIOS), default=['math-name-leak'])
    create.add_argument('--task', nargs='+', type=int, default=[0], help='Zero-based indexes into each scenario dataset')
    create.add_argument('--workers', type=int, default=32, help='Runs executed at the same time')
    create.add_argument('--env', type=Path, default=Path('.env'))
    create.add_argument('--model', default='gpt-4o-mini-2024-07-18')
    create.add_argument('--base-url', default='https://api.openai.com/v1', help='Any OpenAI-compatible endpoint, e.g. OpenRouter')
    create.add_argument('--api-key-env', default='OPENAI_API_KEY', help='Environment variable that holds the key')
    create.add_argument('--max-turn', type=int, default=20)
    create.add_argument('--max-output-tokens', type=int, default=2048)
    create.add_argument('--max-total-tokens', type=int, default=3_000_000)
    ingest = commands.add_parser('import', help='Load complete saved examples without model calls')
    ingest.add_argument('--input', type=Path, required=True)
    ingest.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'generate':
        # Reasoning models spend part of max_output_tokens on hidden thinking.
        if not 20 <= args.max_turn <= 100 or not 1 <= args.max_output_tokens <= 32768 or args.max_total_tokens < 1:
            parser.error('Use 20..100 debate rounds, 1..32768 maximum output tokens, and a positive token budget')
        if min(args.task) < 0 or args.workers < 1:
            parser.error('--task takes zero-based dataset indexes; --workers must be positive')
        generate(args)
    else:
        from .sample_source import import_pair
        print(json.dumps(import_pair(args.input, args.data), indent=2))


if __name__ == '__main__':
    main()
