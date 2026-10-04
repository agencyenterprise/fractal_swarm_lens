import json

import httpx
import openai
import pytest

from swarm_lens.observability.mast.errors import provider_failure


def rejection(body):
    return openai.BadRequestError("PRIVATE PROMPT MUST NOT ESCAPE", body=body,
        response=httpx.Response(400, headers={"x-request-id": "req_test123"},
                                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")))


def test_size_failure_keeps_only_structured_diagnostics():
    error = rejection({"code": "string_above_max_length", "param": "messages[0].content",
                       "message": "Invalid 'messages[0].content': string too long. Expected a string with maximum length 1048576, but got a string with length 3053704 instead. PRIVATE"})
    message, diagnostic = provider_failure(error)
    assert diagnostic == {"exception_type": "BadRequestError", "http_status": 400,
                          "request_id": "req_test123", "code": "string_above_max_length",
                          "parameter": "messages[0].content", "max_characters": 1048576,
                          "actual_characters": 3053704}
    assert "1,048,576" in message and "3,053,704" in message
    assert "PRIVATE" not in json.dumps([message, diagnostic])


@pytest.mark.parametrize("body", [None, "PRIVATE", {"error": "PRIVATE"},
    {"code": "PRIVATE", "param": "PRIVATE", "message": "PRIVATE"},
    {"code": [], "message": {"PRIVATE": True}},
    {"code": "string_above_max_length", "message": {"PRIVATE": True}}])
def test_unstructured_provider_details_are_never_exposed(body):
    assert "PRIVATE" not in json.dumps(provider_failure(rejection(body)))


def test_context_limit_failure_is_distinct_from_text_field_limit():
    message, diagnostic = provider_failure(rejection({"error": {"code": "context_length_exceeded"}}))
    assert "token count" in message
    assert diagnostic["code"] == "context_length_exceeded"
