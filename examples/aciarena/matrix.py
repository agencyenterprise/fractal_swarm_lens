"""Resumable ACIArena math matrix: benign, name disclosure, location disclosure."""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

from swarm_lens.adapters.openai_embeddings import OpenAITextEncoder
from swarm_lens.observability.caspian import CaspianConfig
from .benchmark import run_case, save, summarize
from .recording import Budget, EmbeddingClient
from .upstream import ROOT, REVISION, load_components, load_tasks

CONDITIONS = ('benign', 'name_disclosure', 'location_disclosure')


@contextmanager
def output_lock(output):
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another process owns this matrix output') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class PersistentBudget(Budget):
    """Serial API budget persisted before every call, including uncertain interrupted usage."""
    def __init__(self, path, max_tokens, max_requests):
        super().__init__(max_tokens, max_requests)
        self.path, self.pending, self.uncertain_tokens = Path(path), 0, 0
        if self.path.exists():
            old = json.loads(self.path.read_text())
            self.tokens, self.requests = old['tokens'], old['requests']
            self.usage = {key: old[key] for key in self.usage}
            # An interrupted request might have been billed. Retain its full reservation.
            self.uncertain_tokens = old.get('uncertain_tokens', 0) + old.get('pending', 0)
            self.tokens += old.get('pending', 0)
        self.persist()

    def persist(self):
        save(self.path, self.describe())

    def reserve(self, input_tokens_upper_bound, output_tokens=0):
        if self.pending:
            raise ValueError('Resolve the previous API request before making another')
        super().reserve(input_tokens_upper_bound, output_tokens)
        self.pending = input_tokens_upper_bound + output_tokens
        self.persist()

    def complete(self, **usage):
        super().complete(**usage)
        self.pending = 0
        self.persist()

    def describe(self):
        return {**super().describe(), 'pending': self.pending, 'uncertain_tokens': self.uncertain_tokens}


def prepare_manifest(output, manifest):
    path = output / 'manifest.json'
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise ValueError('Matrix protocol or source changed; use a new output directory')
    else:
        save(path, manifest)


def completed_case(output, case):
    for path in sorted((output / case['id']).glob('attempt-*/result.json')):
        result = json.loads(path.read_text())
        if (result.get('status') == 'complete' and result['task'] == case['task']
                and result['condition'] == case['condition'] and result['history']['replay_matches']):
            return result
    return None


def next_attempt(output, case):
    parent = output / case['id']
    parent.mkdir(parents=True, exist_ok=True)
    index = 1
    while (parent / f'attempt-{index:04d}').exists():
        index += 1
    return parent / f'attempt-{index:04d}'


def matrix_cases(tasks):
    return [{'id': f"{task['id']}-{condition}", 'task': task['id'], 'condition': condition}
            for task in tasks for condition in CONDITIONS]


def report(output, cases, budget, session):
    results = [result for case in cases if (result := completed_case(output, case)) is not None]
    value = {**summarize(results), 'planned_runs': len(cases), 'remaining_runs': len(cases)-len(results),
             'failed_attempts': len(list(output.glob('*/attempt-*/failure.json'))),
             'conditions': {condition: summarize([r for r in results if r['condition'] == condition])
                            for condition in CONDITIONS},
             'api_usage': budget.describe(), 'latest_session': session}
    save(output / 'summary.json', value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', type=int, default=39)
    parser.add_argument('--max-new-cases', type=int, default=3)
    parser.add_argument('--max-turn', type=int, default=20)
    parser.add_argument('--max-output-tokens', type=int, default=1024)
    parser.add_argument('--model', default='gpt-4o-mini-2024-07-18')
    parser.add_argument('--dimensions', type=int, default=16)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-total-tokens', type=int, default=3_000_000)
    parser.add_argument('--max-requests', type=int, default=500)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--env', type=Path, default=Path('.env'))
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.max_turn <= 100 or not 1 <= args.max_output_tokens <= 4096:
        parser.error('Use 1..100 debate rounds and 1..4096 output tokens')
    if min(args.max_new_cases, args.max_total_tokens, args.max_requests) < 1:
        parser.error('Case and API budgets must be positive')
    tasks, digest = load_tasks(args.tasks)
    components = load_components()
    from aciarena.attacks.disclosure_attack import MathLocationLeakInstruction
    encoder_description = OpenAITextEncoder(None, dimensions=args.dimensions).describe()
    settings = {'model': args.model, 'max_turn': args.max_turn, 'max_output_tokens': args.max_output_tokens,
                'seed': args.seed, 'temperature': 0.0, 'encoder': encoder_description}
    cases = matrix_cases(tasks)
    manifest = {'protocol': 'aciarena-math-matrix-v1', 'upstream_revision': REVISION,
                'dataset_sha256': digest, 'cases': cases, 'settings': settings,
                'attack_classes': {'name_disclosure': 'MathNameLeakInstruction',
                                   'location_disclosure': 'MathLocationLeakInstruction'},
                'injection': 'debater_0 continuously from bootstrap', 'observed_channels': ['comm'],
                'caspian_config': asdict(CaspianConfig()),
                'source_sha256': {str(p.relative_to(ROOT.parents[1])): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for base in ('examples/aciarena', 'src/swarm_lens')
                                  for p in sorted((ROOT.parents[1]/base).rglob('*.py'))},
                'packages': {name: importlib.metadata.version(name) for name in
                             ('openai', 'numpy', 'math-verify', 'human_eval', 'pydantic', 'transformers',
                              'datasets', 'PyYAML', 'colorlog', 'tenacity', 'python-dotenv')}}
    with output_lock(args.output):
        prepare_manifest(args.output, manifest)
        if args.plan_only:
            print(json.dumps({'planned_runs': len(cases), 'output': str(args.output), 'api_calls': 0}))
            return
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv(args.env, override=False)
        if os.environ.get('CASPIAN_EMBEDDING_MODEL', OpenAITextEncoder.model) != OpenAITextEncoder.model:
            parser.error('This matrix requires text-embedding-3-small')
        budget = PersistentBudget(args.output/'budget.json', args.max_total_tokens, args.max_requests)
        client = OpenAI(base_url='https://api.openai.com/v1', max_retries=0, timeout=90)
        encoder = OpenAITextEncoder(EmbeddingClient(client, budget), dimensions=args.dimensions)
        session = {'started_at': datetime.now(timezone.utc).isoformat(), 'new_completed': 0, 'error_type': None}
        try:
            for case in cases:
                if completed_case(args.output, case):
                    continue
                if session['new_completed'] >= args.max_new_cases:
                    break
                task = next(t for t in tasks if t['id'] == case['task'])
                selected = (components[0], MathLocationLeakInstruction, components[2]) if case['condition'] == 'location_disclosure' else components
                session['current_case'] = case['id']
                run_case(task, case['condition'], settings, client, budget, encoder,
                         next_attempt(args.output, case), selected)
                session['new_completed'] += 1
                report(args.output, cases, budget, session)
        except Exception as error:
            session['error_type'] = type(error).__name__
        finally:
            session['ended_at'] = datetime.now(timezone.utc).isoformat()
            summary = report(args.output, cases, budget, session)
            client.close()
            print(json.dumps({key: summary[key] for key in ('completed_runs','remaining_runs','api_usage','latest_session')}), flush=True)
        if session['error_type']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
