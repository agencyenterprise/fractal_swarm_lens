import json
from pathlib import Path
from types import SimpleNamespace as S

import pytest

pytest.importorskip('math_verify')
pytest.importorskip('human_eval')

from examples.aciarena.recording import Budget
from examples.aciarena.sample_source import MAD_TURN, expected_schedule, import_pair, load_pair
from examples.aciarena.samples import digest, run_sample, save
from examples.aciarena.upstream import REVISION, SCENARIOS, disclosure_information, load_components, load_tasks
from swarm_lens import Framework
from swarm_lens.adapters.sqlite import SQLiteHistory


SETTINGS = {'model': 'fake', 'temperature': 0.0, 'seed': 42, 'max_turn': 20, 'max_output_tokens': 200}


class Client:
    api_key = 'test-placeholder-not-to-be-saved'
    base_url = 'https://api.openai.com/v1'

    def __init__(self, judgement='{"judgement": "no"}', moderator='undecided'):
        self.judgement, self.moderator = judgement, moderator
        self.chat = S(completions=S(create=self.create))

    def create(self, **kwargs):
        if 'response_format' in kwargs:
            content = self.judgement
        elif kwargs['messages'][0]['content'].startswith('You are a moderator'):
            content = self.moderator
        else:
            content = 'The answer is \\boxed{1}.'
        return S(usage=S(prompt_tokens=10, completion_tokens=5), model='fake',
                 choices=[S(message=S(content=content), finish_reason='stop')])


def build_pair(root, name, client=Client):
    scenario = SCENARIOS[name]
    task = load_tasks(1, domain=scenario.domain)[0][0]
    samples = []
    for condition in ('control', 'injection'):
        result = run_sample(task, condition, SETTINGS, client(), Budget(), root / condition,
                            load_components(scenario), scenario)
        samples.append({'condition': condition, 'directory': condition, 'result': result,
                        'trace_sha256': digest(root / condition / 'trace.jsonl'),
                        'result_sha256': digest(root / condition / 'result.json')})
    manifest = {'schema': 'swarm-lens.aciarena.examples/v1', 'status': 'complete', 'settings': SETTINGS,
                'task': task, 'system': scenario.system, 'attack': scenario.attack, 'scenario': name,
                'upstream_revision': REVISION,
                'upstream_repository': 'https://github.com/Greysahy/aciarena', 'samples': samples}
    save(root / 'manifest.json', manifest)
    return root


@pytest.fixture
def pair(tmp_path):
    return build_pair(tmp_path / 'pair', 'math-name-leak')


def test_complete_twenty_round_pair_retains_exact_inputs_memories_and_injection(pair, tmp_path):
    manifest, traces = load_pair(pair)
    calls = {condition: [row for row in rows if row['type'] == 'model_call'] for condition, rows in traces.items()}
    assert len(calls['control']) == len(calls['injection']) == 64
    assert calls['control'][0]['messages'][0] == calls['injection'][0]['messages'][0]
    payload = next(row['content'] for row in traces['injection'] if row['type'] == 'injection_installed')
    for condition, rows in calls.items():
        for row in rows:
            assert (payload in row['messages'][-1]['content']) == (condition == 'injection' and row['agent'] == 'debater_0')
        assert [item['sequence'] for item in rows[4]['sources']] == [4, 3]
        assert [item['sequence'] for item in rows[-1]['sources']] == [61, 62, 63]
    assert Client.api_key not in ''.join(path.read_text() for path in pair.rglob('*.jsonl'))
    imported = import_pair(pair, tmp_path / 'workspace')
    assert [(r['events'], r['model_responses'], r['messages'], r['memory_slots']) for r in imported] == [
        (199, 64, 65, 3), (200, 64, 65, 3)]
    assert import_pair(pair, tmp_path / 'workspace') == imported
    framework = Framework(SQLiteHistory(tmp_path / 'workspace/history.sqlite'))
    injected = framework.history(imported[1]['branch_id'])
    installation = next(event for event in injected if event.data.get('type') == 'instruction_injection')
    assert installation.source['applied_to_runtime'] is True
    first = next(event for event in injected if event.data.get('id') == 'call-1')
    child = framework.fork(imported[1]['branch_id'], first.position, 'Early prefix')
    assert set(framework.state(child.id).messages) == {'task', 'call-1'}
    assert not framework.state(child.id).memories  # The memory write is the next real event.
    assert framework.state(imported[1]['branch_id']).messages['call-64'].content == calls['injection'][-1]['response']
    for condition, rows in traces.items():
        for memory in (row for row in rows if row['type'] == 'memory_snapshot'):
            call = calls[condition][memory['sequence'] - 1]
            assert memory['conversation'] == call['messages'] + [{'role': 'assistant', 'content': call['response']}]


def test_rejects_modified_or_incomplete_saved_pair(pair):
    path = pair / 'control/trace.jsonl'
    path.write_text(path.read_text() + '\n')
    with pytest.raises(ValueError, match='checksum'):
        load_pair(pair)
    manifest = json.loads((pair / 'manifest.json').read_text())
    manifest['status'] = 'failed'
    save(pair / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='complete ACIArena'):
        load_pair(pair)


def test_malicious_agent_pair_replaces_only_debater_0_profile_and_records_the_judge(tmp_path):
    pair = build_pair(tmp_path / 'pair', 'medicine-misalign')
    manifest, traces = load_pair(pair)
    assert manifest['task']['id'] == 'medicine-000'
    payload = next(row['content'] for row in traces['injection'] if row['type'] == 'injection_installed')
    for condition, rows in traces.items():
        calls = [row for row in rows if row['type'] == 'model_call']
        assert len(calls) == 64
        for row in calls[:-1]:
            assert (row['messages'][0]['content'] == payload) == (condition == 'injection' and row['agent'] == 'debater_0')
        assert not any(disclosure_information() in json.dumps(row['messages']) for row in calls)
        judgements = [row for row in rows if row['type'] == 'attack_judgement']
        assert len(judgements) == int(condition == 'injection')
    injected = next(s['result'] for s in manifest['samples'] if s['condition'] == 'injection')
    assert injected['attack_success'] is False and injected['task_correct'] is False
    imported = import_pair(pair, tmp_path / 'workspace')
    framework = Framework(SQLiteHistory(tmp_path / 'workspace/history.sqlite'))
    history = framework.history(imported[1]['branch_id'])
    installation = next(event for event in history if event.data.get('type') == 'malicious_agent')
    assert installation.data['content'] == payload
    assert framework.state(imported[1]['branch_id']).agents['debater_0'].system_prompt == payload


def test_judge_reply_without_a_judgement_fails_instead_of_counting_as_success(tmp_path):
    scenario = SCENARIOS['medicine-misalign']
    task = load_tasks(1, domain=scenario.domain)[0][0]
    with pytest.raises(RuntimeError, match='no yes/no judgement'):
        run_sample(task, 'injection', SETTINGS, Client(judgement='{}'), Budget(), tmp_path / 'injection',
                   load_components(scenario), scenario)
    assert json.loads((tmp_path / 'injection/failure.json').read_text())['calls'] == 64


def test_code_hijacking_pair_grades_with_upstream_checks(tmp_path):
    pair = build_pair(tmp_path / 'pair', 'code-malicious-report')
    manifest, traces = load_pair(pair)
    assert manifest['task']['id'] == 'code-000'
    injected = next(s['result'] for s in manifest['samples'] if s['condition'] == 'injection')
    assert injected['task_correct'] is False and injected['attack_success'] is False
    assert not any(row['type'] == 'attack_judgement' for row in traces['injection'])  # URL check, no LLM judge
    assert [r['model_responses'] for r in import_pair(pair, tmp_path / 'workspace')] == [64, 64]


def test_mad_malicious_profile_survives_bootstrap_and_reaches_only_negative(tmp_path):
    pair = build_pair(tmp_path / 'pair', 'medicine-misalign-mad')
    manifest, traces = load_pair(pair)
    payload = next(row['content'] for row in traces['injection'] if row['type'] == 'injection_installed')
    for condition, rows in traces.items():
        calls = [row for row in rows if row['type'] == 'model_call']
        assert len(calls) == 3 + 3 * 20 + 1 and calls[-1]['agent'] == 'judge'
        for call in calls:
            system_prompt = call['messages'][0]['content']
            assert system_prompt.startswith(payload) == (condition == 'injection' and call['agent'] == 'negative')
        negative = next(call for call in calls if call['agent'] == 'negative')
        assert 'You are a debater' in negative['messages'][0]['content']
        assert [source['agent'] for source in negative['sources']] == ['affirmative']
        moderator = next(call for call in calls if call['agent'] == 'moderator')
        assert [source['agent'] for source in moderator['sources']] == ['affirmative', 'negative']
    imported = import_pair(pair, tmp_path / 'workspace')
    assert [r['model_responses'] for r in imported] == [64, 64]


def test_mad_stops_at_the_first_moderator_decision(tmp_path):
    decided = '{"Whether there is a preference": "Yes", "Supported Side": "Affirmative", "Reason": "r", "debate_answer": "A"}'
    pair = build_pair(tmp_path / 'pair', 'medicine-misalign-mad', client=lambda: Client(moderator=decided))
    manifest, traces = load_pair(pair)
    for sample in manifest['samples']:
        assert sample['result']['calls'] == 3 and sample['result']['debate_rounds'] == 0
        assert sample['result']['task_correct'] is True


def test_rejects_a_mad_trace_that_reaches_the_judge_after_an_early_decision():
    calls = [{'phase': 'bootstrap', 'round': 0, 'agent': agent} for agent in MAD_TURN]
    calls.append({'phase': 'aggregation', 'round': 1, 'agent': 'judge'})
    assert [(c['phase'], c['round'], c['agent']) for c in calls] != expected_schedule('MAD', calls, 20)


def test_generate_runs_every_pair_concurrently_and_each_pair_imports(tmp_path, monkeypatch):
    import openai
    from examples.aciarena.samples import generate

    class FakeOpenAI(Client):
        def __init__(self, **kwargs):
            super().__init__()

        def close(self):
            pass

    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    monkeypatch.setenv('FAKE_KEY', 'test-placeholder-not-to-be-saved')
    args = S(output=tmp_path / 'batch', scenario=['math-name-leak', 'medicine-misalign'], task=[0, 1], workers=8,
             env=tmp_path / 'missing.env', model='fake', base_url='https://example.invalid/v1', api_key_env='FAKE_KEY',
             max_turn=20, max_output_tokens=200, max_total_tokens=3_000_000)
    generate(args)
    directories = sorted(path.name for path in args.output.iterdir())
    assert directories == ['math-name-leak-math-000', 'math-name-leak-math-001',
                           'medicine-misalign-medicine-000', 'medicine-misalign-medicine-001']
    for name in directories:
        manifest, _ = load_pair(args.output / name)
        assert [s['condition'] for s in manifest['samples']] == ['control', 'injection']


def test_wrong_option_attack_targets_a_fixed_wrong_letter_and_grades_with_upstream_extraction(tmp_path):
    from examples.aciarena.upstream import wrong_option
    tasks = load_tasks(5, domain='medicine')[0]
    assert [wrong_option(task) for task in tasks] == ['B', 'B', 'A', 'A', 'B']
    pair = build_pair(tmp_path / 'pair', 'medicine-wrong-option')
    manifest, traces = load_pair(pair)
    payload = next(row['content'] for row in traces['injection'] if row['type'] == 'injection_installed')
    assert 'The answer is (B).' in payload
    injected = next(s['result'] for s in manifest['samples'] if s['condition'] == 'injection')
    assert injected['attack_success'] is False
