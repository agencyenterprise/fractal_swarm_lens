import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from swarm_lens.core.models import DomainError, Event
from swarm_lens.observability.mast.judge import OpenAIMastJudge
from swarm_lens.observability.mast.method import MastPlugin, history_document, make_prompt
from swarm_lens.observability.mast.trace import decode_trace, encode_trace


def test_repeated_content_and_json_memory_reconstruct_exactly():
    repeated = "A repeated response, including Unicode: café 🐈. " * 10
    memory = json.dumps([{"role": "user", "content": repeated},
                         {"role": "assistant", "content": repeated}], ensure_ascii=False, indent=2)
    original = {"events": [{"position": 1, "content": repeated}, {"position": 2, "content": repeated},
                           {"position": 3, "memory": memory}, {"position": 4, "memory": memory}],
                "literal": {"$mast_text": "t1", "$mast_json_text": [repeated], "$mast_object": []}}
    encoded = encode_trace(original)
    assert len(encoded["events"]) == 4
    assert len(json.dumps(encoded)) < len(json.dumps(original))
    assert decode_trace(json.loads(json.dumps(encoded))) == original


@pytest.mark.parametrize("condition", ["control", "injection"])
def test_complete_aciarena_example_fits_and_keeps_every_event(condition, tmp_path):
    from examples.aciarena.sample_source import load_pair, ExampleSource
    from swarm_lens.adapters.artifacts import FileArtifacts

    root = Path(__file__).resolve().parents[3]
    manifest, rows = load_pair(root / "examples/aciarena/samples/llm-debate-pair-20261003")
    facts = ExampleSource(manifest, rows[condition], FileArtifacts(tmp_path / "artifacts")).facts()
    history = [Event(f.id, "test", i, f.kind, f.data, f.occurred_at, f.occurred_at, f.source)
               for i, f in enumerate(facts, 1)]
    plugin = MastPlugin(OpenAIMastJudge(model="gpt-5.5"))
    trace, prompt, summary = plugin.prepare_input(history, {"completeness": "complete"})
    assert decode_trace(json.loads(trace)) == history_document(history, "complete")
    assert summary["message_count"] == 65
    assert summary["memory_snapshot_count"] == 63
    assert summary["event_count"] == 199 + (condition == "injection")
    assert summary["prepared_characters"] < summary["original_characters"] / 10
    assert summary["estimated_input_tokens"] < summary["input_token_limit"]
    assert summary["can_analyze"]
    assert prompt == make_prompt(trace)
    # A prefix contains only text available at its boundary, even in the reference table.
    prefix, _, _ = plugin.prepare_input(history[:15], {"completeness": "partial"})
    assert decode_trace(json.loads(prefix)) == history_document(history[:15], "partial")


def test_gpt55_request_omits_unsupported_temperature_and_records_model(monkeypatch):
    import openai

    requests = []
    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer"), finish_reason="stop")],
                                   model="gpt-5.5-2026-04-23", id="fake-request", usage=None)

    monkeypatch.setattr(openai, "OpenAI", Client)
    judge = OpenAIMastJudge(model="gpt-5.5")
    result = judge.complete("Small test prompt")
    assert requests[0]["model"] == "gpt-5.5"
    assert requests[0]["reasoning_effort"] == "medium"
    assert "temperature" not in requests[0]
    assert requests[0]["store"] is False
    assert result["model"] == "gpt-5.5-2026-04-23"
    assert result["requested_model"] == "gpt-5.5"
    requests.clear()
    too_small = OpenAIMastJudge(model="gpt-5.5", context_window=10)
    with pytest.raises(DomainError, match="Nothing was truncated or sent"):
        too_small.complete("Does not fit")
    assert not requests


def test_long_input_reserves_proportional_headroom(monkeypatch):
    import tiktoken
    # This local count was accepted by the old budget but rejected by the provider.
    class Encoding:
        name = "test-encoding"
        def encode(self, text, **kwargs):
            return range(1_032_308 if text == "formerly accepted" else 950_000)
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: Encoding())
    judge = OpenAIMastJudge(model="gpt-5.5")
    judge.max_input_tokens = 1_050_000
    old = judge.input_budget("formerly accepted")
    assert old["estimated_input_tokens"] == 1_032_315
    assert old["token_margin"] == 52_500
    assert old["input_token_limit"] == 981_116
    assert not old["can_analyze"]
    assert judge.input_budget("smaller chunk")["can_analyze"]


def test_operational_input_cap_is_separate_from_advertised_context(monkeypatch):
    import tiktoken
    monkeypatch.delenv("MAST_MAX_INPUT_TOKENS", raising=False)
    class Encoding:
        name = "test-encoding"
        def encode(self, text, **kwargs):
            return range(int(text))
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: Encoding())
    judge = OpenAIMastJudge(model="gpt-5.5")
    assert judge.input_budget("239993")["can_analyze"]
    assert not judge.input_budget("239994")["can_analyze"]
    assert judge.describe()["max_input_tokens"] == 240_000
    assert OpenAIMastJudge(max_input_tokens=100).input_budget("94")["input_token_limit"] == 100
    monkeypatch.setenv("MAST_MAX_INPUT_TOKENS", "180000")
    assert OpenAIMastJudge().input_budget("180000")["input_token_limit"] == 180000
    with pytest.raises(ValueError, match="must be positive"):
        OpenAIMastJudge(max_input_tokens=0)
