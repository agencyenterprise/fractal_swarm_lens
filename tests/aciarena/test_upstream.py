import json
from types import SimpleNamespace as S
import pytest

pytest.importorskip('math_verify')
pytest.importorskip('human_eval')
from examples.aciarena.benchmark import run_case
from examples.aciarena.recording import Budget
from examples.aciarena.upstream import ROOT, load_components, load_tasks, disclosure_information

pytestmark = pytest.mark.skipif(not (ROOT / 'aciarena').exists(), reason='Initialize optional ACIArena submodule')


class Encoder:
    feature_schema = 'test/deterministic/4'
    def encode(self, texts):
        return [tuple(float((sum(map(ord, text)) + i) % 17) / 17 for i in range(4)) for text in texts]


class Client:
    api_key = 'test-placeholder'
    base_url = 'https://api.openai.com/v1'
    def __init__(self):
        self.chat = S(completions=S(create=self.create))
    def create(self, **kwargs):
        return S(usage=S(prompt_tokens=10, completion_tokens=5), model='fake',
                 choices=[S(message=S(content='The answer is \\boxed{1}.'), finish_reason='stop')])


@pytest.mark.parametrize('condition', ['benign', 'name_disclosure'])
def test_real_upstream_debate_wiring_and_history_replay(tmp_path, condition):
    settings = {'model': 'fake', 'max_output_tokens': 100, 'seed': 42, 'max_turn': 20}
    components = load_components()
    result = run_case(load_tasks(1)[0][0], condition, settings, Client(), Budget(), Encoder(),
                      tmp_path / 'case', components)
    calls = [row for row in map(json.loads, (tmp_path / 'case/trace.jsonl').read_text().splitlines())
             if row['type'] == 'model_call']
    assert result['calls'] == 64
    assert result['history']['replay_matches']
    assert all(not call['sources'] for call in calls[:3])
    # Sequential upstream debate: debater_1 receives this round's debater_0 answer.
    assert [s['sequence'] for s in calls[3]['sources']] == [2, 3]
    assert [s['sequence'] for s in calls[4]['sources']] == [4, 3]
    assert [s['sequence'] for s in calls[5]['sources']] == [4, 5]
    assert [s['sequence'] for s in calls[-1]['sources']] == [61, 62, 63]
    payload = components[1](args=S(), llm_config={'provider': 'openai', 'api_key': 'test-placeholder',
                                                 'model_name': 'fake'}).payload
    for call in calls:
        assert disclosure_information() in call['messages'][0]['content']
        injected = payload in call['messages'][-1]['content']
        assert injected == (condition == 'name_disclosure' and call['agent'] == 'debater_0')
