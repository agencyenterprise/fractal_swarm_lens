"""Shared OpenAI chat-completions access for analysis plugins. Importing this never calls a provider."""
import importlib.util
import json
import os
from threading import Lock

from swarm_lens.core.models import DomainError

KNOWN_CONTEXT_WINDOWS = {"gpt-5.5": 1_050_000, "gpt-5.5-2026-04-23": 1_050_000, "gpt-5.6-sol": 1_050_000, "gpt-6-astra": 1_050_000,
                         "o1": 200_000, "o1-2024-12-17": 200_000}
FRAMING_MARGIN = 1024  # Tokens reserved beyond the prompt estimate for chat framing.


class ModelError(DomainError):
    """The provider call failed or returned no usable answer.

    The message is written by this module and safe to show. `provider_error` names the failure type and
    `detail` states the root cause in full (status, request id, and the underlying exception) for server-side
    diagnosis only, because provider messages can contain request details. `usage` holds the billed usage of
    the failed request when a response arrived, otherwise None.
    """

    def __init__(self, message, provider_error=None, usage=None, detail=None):
        super().__init__(message)
        self.provider_error, self.usage, self.detail = provider_error, usage, detail


def root_cause(exc):
    """The provider error with its status and request id, followed by the deepest underlying exception."""
    parts = [f"{type(exc).__name__}: {exc}"]
    status, request_id = getattr(exc, "status_code", None), getattr(exc, "request_id", None)
    if status or request_id:
        parts.append(f"(status {status}, request {request_id})")
    inner = exc.__cause__ or exc.__context__
    while inner is not None and (inner.__cause__ or inner.__context__) is not None:
        inner = inner.__cause__ or inner.__context__
    if inner is not None:
        parts.append(f"caused by {type(inner).__name__}: {inner}")
    return " ".join(parts)


def usage_of(result):
    """{"input_tokens", "output_tokens", "details"} from a provider response; None values when absent."""
    details = result.usage.model_dump() if getattr(result, "usage", None) else None
    return {"input_tokens": details.get("prompt_tokens") if details else None,
            "output_tokens": details.get("completion_tokens") if details else None, "details": details}


class OpenAIChat:
    """One configured model. `env_prefix` names its settings, e.g. MAST_MODEL or TIMELINE_MODEL.

    All calls share one HTTP client whose pool holds at most `max_connections` connections; extra calls
    wait for a free connection instead of opening new ones. `max_retries` retries connection failures,
    rate limits, and server errors with the SDK's backoff; a retried server error may be billed twice.
    """

    def __init__(self, *, env_prefix, extra, default_model="gpt-5.5", reasoning_effort=None,
                 max_completion_tokens=16_384, model=None, context_window=None, timeout=300.0,
                 max_retries=0, max_connections=32, client_factory=None):
        self.env_prefix, self.extra = env_prefix, extra
        self.model = model or os.environ.get(f"{env_prefix}_MODEL", default_model)
        self.reasoning_effort = reasoning_effort or os.environ.get(f"{env_prefix}_REASONING_EFFORT", "medium")
        self.max_completion_tokens, self.timeout = max_completion_tokens, timeout
        configured = context_window or os.environ.get(f"{env_prefix}_CONTEXT_WINDOW")
        self.context_window = int(configured) if configured else KNOWN_CONTEXT_WINDOWS.get(self.model)
        self.temperature = None if self.model.startswith("gpt-5") else 1.0
        self.max_retries, self.max_connections = max_retries, max_connections
        self.client_factory, self._shared_client, self._client_lock = client_factory, None, Lock()

    def settings(self):
        return {"provider": "openai", "model": self.model, "context_window": self.context_window,
                "reasoning_effort": self.reasoning_effort, "max_completion_tokens": self.max_completion_tokens,
                "temperature": self.temperature}

    def describe(self):
        installed = all(importlib.util.find_spec(name) is not None for name in ("openai", "tiktoken"))
        key = os.environ.get("OPENAI_API_KEY", "")
        ready = installed and bool(key) and key != "your_openai_api_key_here" and bool(self.context_window)
        reason = None if ready else (
            f"Set {self.env_prefix}_CONTEXT_WINDOW for this model so the complete prompt can be checked."
            if not self.context_window else
            f"Install the {self.extra} extra and configure OPENAI_API_KEY on the server.")
        return {**self.settings(), "ready": ready, "reason": reason}

    def count_tokens(self, text):
        try:
            import tiktoken
            return len(tiktoken.get_encoding("o200k_base").encode(text, disallowed_special=()))
        except Exception as exc:
            raise DomainError("Unable to load the tokenizer. Check the server's tiktoken installation and "
                              "vocabulary cache. Nothing was sent.") from exc

    def input_limit(self):
        """Prompt tokens available after reserving the output budget and the framing margin."""
        return max(0, (self.context_window or 0) - self.max_completion_tokens - FRAMING_MARGIN)

    def budget(self, *texts):
        tokens = sum(self.count_tokens(text) for text in texts) + 7 * len(texts)
        fits = bool(self.context_window) and tokens <= self.input_limit()
        return {"estimated_input_tokens": tokens, "tokenizer": "o200k_base", "input_token_limit": self.input_limit(),
                "context_window": self.context_window, "reserved_output_tokens": self.max_completion_tokens,
                "token_margin": FRAMING_MARGIN, "can_analyze": fits,
                "reason": None if fits else (
                    f"The complete prompt needs approximately {tokens:,} input tokens; {self.model} has "
                    f"{self.input_limit():,} available after reserving the output budget. Configure a model "
                    "with a larger context window. Nothing was truncated or sent.")}

    def _client(self):
        with self._client_lock:
            if self._shared_client is None:
                self._shared_client = self.client_factory() if self.client_factory else self._new_client()
            return self._shared_client

    def _new_client(self):
        import httpx
        from openai import OpenAI
        limits = httpx.Limits(max_connections=self.max_connections, max_keepalive_connections=self.max_connections)
        # Waiting for a pooled connection is not a request timeout.
        timeout = httpx.Timeout(self.timeout, pool=None)
        return OpenAI(base_url="https://api.openai.com/v1", timeout=timeout, max_retries=self.max_retries,
                      http_client=httpx.Client(limits=limits, timeout=timeout))

    def complete(self, messages, *, response_format=None):
        """Send one request. Returns the text with provider metadata; never echoes provider messages."""
        from openai import OpenAIError

        parameters = {"model": self.model, "messages": messages, "reasoning_effort": self.reasoning_effort,
                      "max_completion_tokens": self.max_completion_tokens, "store": False}
        if self.temperature is not None:
            parameters["temperature"] = self.temperature
        if response_format:
            parameters["response_format"] = response_format
        try:
            result = self._client().chat.completions.create(**parameters)
        except OpenAIError as exc:
            raise ModelError("The provider request failed.", type(exc).__name__, detail=root_cause(exc)) from exc
        try:
            usage = usage_of(result)
            choice = result.choices[0] if result.choices else None
            text = choice.message.content if choice else None
            finish_reason = choice.finish_reason if choice else None
            model, request_id = result.model, result.id
        except (AttributeError, TypeError) as exc:
            raise ModelError("The provider response has an unexpected shape.", "MalformedResponse",
                             detail=root_cause(exc)) from exc
        if not text:
            raise ModelError("The model returned no textual answer.", "EmptyResponse", usage)
        return {**self.settings(), "text": text, "finish_reason": finish_reason, "model": model,
                "requested_model": self.model, "request_id": request_id, "usage": usage["details"],
                "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}

    def complete_json(self, system, user, schema, name):
        """Strict structured output: {"data", "model", "request_id", "usage": {input_tokens, output_tokens, details}}.

        A failure after a response arrived raises ModelError carrying that response's billed usage.
        """
        response = self.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}})
        usage = {"input_tokens": response["input_tokens"], "output_tokens": response["output_tokens"],
                 "details": response["usage"]}
        if response["finish_reason"] != "stop":
            raise ModelError(f"The model did not finish a structured answer ({response['finish_reason']}).",
                             "IncompleteResponse", usage)
        try:
            data = json.loads(response["text"])
        except json.JSONDecodeError as exc:
            raise ModelError("The model returned text that is not valid JSON.", "InvalidJSON", usage,
                             detail=root_cause(exc)) from exc
        return {"data": data, "model": response["model"], "request_id": response["request_id"], "usage": usage}
