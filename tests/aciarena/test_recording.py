from types import SimpleNamespace
import pytest

from examples.aciarena.recording import Budget, BudgetExceeded, RecordingClient
from examples.aciarena.source import FeatureBuilder


def test_budget_prevents_network_request():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: pytest.fail('network must not be called'))))
    recorder = RecordingClient(client, None, 'debater_0', Budget(max_tokens=1))
    with pytest.raises(BudgetExceeded):
        recorder.create(messages=[{'role': 'user', 'content': 'hello'}])


def test_budget_tracks_provider_usage_and_request_limit():
    budget = Budget(max_tokens=100, max_requests=1)
    budget.reserve(30, 20)
    budget.complete(chat_input=15, chat_output=10, embedding_input=5)
    assert budget.tokens == 30
    with pytest.raises(BudgetExceeded):
        budget.reserve(1)


class Encoder:
    def __init__(self): self.inputs = []
    def encode(self, texts):
        self.inputs.extend(texts)
        return [(float(len(text)), 1.0) for text in texts]


def test_features_use_only_delivered_text_and_cache_repeated_messages():
    encoder = Encoder()
    builder = FeatureBuilder(encoder)
    calls = [{'sequence': 2, 'agent': 'debater_1', 'response': 'answer',
              'messages': ['injection must not be encoded directly'], 'attack_label': True,
              'sources': [{'sequence': 1, 'agent': 'debater_0', 'response': 'advice'}]}]
    first = builder.build(1, calls)
    builder.build(2, calls)
    assert encoder.inputs == ['answer', 'advice']
    assert len(first.events) == 1
    assert first.events[0].source == 'debater_0'
    calls[0]['sources'][0]['sequence'] = 3
    with pytest.raises(ValueError, match='precede'):
        builder.build(3, calls)
