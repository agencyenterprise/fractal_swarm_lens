import json
from pathlib import Path
from types import SimpleNamespace as S

import pytest

pytest.importorskip('math_verify')
pytest.importorskip('human_eval')

from examples.aciarena.recording import Budget
from examples.aciarena.sample_source import import_pair, load_pair
from examples.aciarena.samples import digest, run_sample, save
from examples.aciarena.upstream import REVISION, load_components, load_tasks
from swarm_lens import Framework
from swarm_lens.adapters.sqlite import SQLiteHistory


class Client:
    api_key = 'test-placeholder-not-to-be-saved'
    base_url = 'https://api.openai.com/v1'

    def __init__(self):
        self.chat = S(completions=S(create=self.create))

    def create(self, **kwargs):
        return S(usage=S(prompt_tokens=10, completion_tokens=5), model='fake',
                 choices=[S(message=S(content='The answer is \\boxed{1}.'), finish_reason='stop')])


@pytest.fixture
def pair(tmp_path):
    root = tmp_path / 'pair'
    settings = {'model': 'fake', 'temperature': 0.0, 'seed': 42, 'max_turn': 20, 'max_output_tokens': 200}
    task = load_tasks(1)[0][0]
    samples = []
    for condition in ('control', 'injection'):
        result = run_sample(task, condition, settings, Client(), Budget(), root / condition, load_components())
        samples.append({'condition': condition, 'directory': condition, 'result': result,
                        'trace_sha256': digest(root / condition / 'trace.jsonl'),
                        'result_sha256': digest(root / condition / 'result.json')})
    manifest = {'schema': 'swarm-lens.aciarena.examples/v1', 'status': 'complete', 'settings': settings,
                'task': task, 'system': 'LLMDebate', 'upstream_revision': REVISION,
                'upstream_repository': 'https://github.com/Greysahy/aciarena', 'samples': samples}
    save(root / 'manifest.json', manifest)
    return root


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
