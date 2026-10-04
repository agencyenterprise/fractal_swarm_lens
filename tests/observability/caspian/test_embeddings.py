from types import SimpleNamespace
import math

import pytest

from swarm_lens.adapters.openai_embeddings import OpenAITextEncoder


class FakeClient:
    def __init__(self):
        self.embeddings = self
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(i+1)] * kwargs['dimensions'])
                                     for i in reversed(range(len(kwargs['input'])))])


def test_requested_model_batching_order_and_provenance():
    client = FakeClient()
    encoder = OpenAITextEncoder(client, dimensions=4, batch_size=2)
    assert encoder.encode(['a', 'b', 'c']) == [(1.,)*4, (2.,)*4, (1.,)*4]
    assert [call['input'] for call in client.calls] == [['a', 'b'], ['c']]
    assert all(call['model'] == 'text-embedding-3-small' for call in client.calls)
    assert encoder.feature_schema == 'openai/text-embedding-3-small/dimensions=4/unaltered-text/v1'
    assert 'client' not in encoder.describe()


def test_empty_inputs_make_no_requests_and_invalid_text_rejected_before_requests():
    client = FakeClient()
    encoder = OpenAITextEncoder(client)
    assert encoder.encode([]) == []
    with pytest.raises(ValueError):
        encoder.encode(['valid', ' '])
    assert not client.calls


@pytest.mark.parametrize('rows', [[(1, [1., 2.])], [(0, [1.])], [(0, [math.nan, 2.])]])
def test_invalid_provider_response_rejected(rows):
    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
        data=[SimpleNamespace(index=i, embedding=v) for i, v in rows])))
    with pytest.raises(ValueError):
        OpenAITextEncoder(client, dimensions=2).encode(['a'])


@pytest.mark.parametrize('dimensions', [0, 1537, True, 1.5])
def test_invalid_dimensions(dimensions):
    with pytest.raises(ValueError):
        OpenAITextEncoder(FakeClient(), dimensions=dimensions)
