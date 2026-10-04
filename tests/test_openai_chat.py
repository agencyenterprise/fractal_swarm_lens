"""Shared OpenAI chat adapter: request shape, billed usage on failure, and safe error reporting."""
from types import SimpleNamespace

import pytest

from swarm_lens.adapters.openai_chat import ModelError, OpenAIChat


class FakeCompletions:
    def __init__(self, finish_reason="stop", content='{"milestones": []}'):
        self.finish_reason, self.content, self.requests = finish_reason, content, []

    def create(self, **request):
        self.requests.append(request)
        choice = SimpleNamespace(finish_reason=self.finish_reason, message=SimpleNamespace(content=self.content))
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 7, "completion_tokens": 3})
        return SimpleNamespace(choices=[choice], model="gpt-test-1", id="req-1", usage=usage)


def chat(completions, **settings):
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAIChat(env_prefix="TEST", extra="test", model="gpt-5.5", client_factory=lambda: client, **settings)


def test_structured_request_is_strict_and_reports_usage():
    completions = FakeCompletions()
    result = chat(completions).complete_json("system", "user", {"type": "object"}, "timeline")
    assert result["data"] == {"milestones": []} and result["model"] == "gpt-test-1"
    assert result["usage"]["input_tokens"] == 7 and result["usage"]["output_tokens"] == 3
    request = completions.requests[0]
    assert request["response_format"]["json_schema"]["strict"] is True and request["store"] is False
    assert "temperature" not in request and request["reasoning_effort"] == "medium"


def test_failures_after_a_response_keep_its_billed_usage():
    for completions in (FakeCompletions(finish_reason="length"), FakeCompletions(content="{not json")):
        with pytest.raises(ModelError) as error:
            chat(completions).complete_json("s", "u", {}, "timeline")
        assert error.value.usage["input_tokens"] == 7


def test_provider_failures_keep_the_root_cause_out_of_the_shown_message():
    import openai

    class Failing:
        def create(self, **request):
            try:
                raise ConnectionResetError("Connection reset by peer")
            except ConnectionResetError as reset:
                raise openai.APIConnectionError(request=None, message="Connection error.") from reset

    with pytest.raises(ModelError) as error:
        chat(Failing()).complete([{"role": "user", "content": "u"}])
    assert error.value.provider_error == "APIConnectionError"
    assert "ConnectionResetError: Connection reset by peer" in error.value.detail
    assert str(error.value) == "The provider request failed."


def test_settings_and_budget_come_from_the_prefixed_environment(monkeypatch):
    monkeypatch.setenv("TEST_MODEL", "unknown-model")
    described = OpenAIChat(env_prefix="TEST", extra="test").describe()
    assert not described["ready"] and "TEST_CONTEXT_WINDOW" in described["reason"]
    small = chat(FakeCompletions(), context_window=20_000, max_completion_tokens=10_000)
    assert small.budget("short prompt")["can_analyze"] and not small.budget("word " * 20_000)["can_analyze"]
