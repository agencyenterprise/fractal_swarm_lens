"""Lazy model access: browsing/importing traces never calls a provider."""
import importlib.util
import os
import math

from swarm_lens.core.models import DomainError


class OpenAIMastJudge:
    def __init__(self, *, model=None, max_completion_tokens=16_384, context_window=None, max_input_tokens=None):
        self.model = model or os.environ.get("MAST_MODEL", "gpt-5.5")
        self.max_completion_tokens = max_completion_tokens
        default_window = {"gpt-5.5": 1_050_000, "gpt-5.5-2026-04-23": 1_050_000,
                          "o1": 200_000, "o1-2024-12-17": 200_000}.get(self.model)
        configured = context_window or os.environ.get("MAST_CONTEXT_WINDOW")
        self.context_window = int(configured) if configured else default_window
        self.max_input_tokens = int(max_input_tokens if max_input_tokens is not None else
                                    os.environ.get("MAST_MAX_INPUT_TOKENS", "240000"))
        if self.max_input_tokens < 1:
            raise ValueError("MAST_MAX_INPUT_TOKENS must be positive")
        self.reasoning_effort = "medium"
        self.temperature = None if self.model.startswith("gpt-5") else 1.0

    def describe(self):
        installed = all(importlib.util.find_spec(name) is not None for name in ("openai", "tiktoken"))
        key = os.environ.get("OPENAI_API_KEY", "")
        ready = installed and bool(key) and key != "your_openai_api_key_here" and bool(self.context_window)
        return {"provider": "openai", "model": self.model, "ready": ready,
                "reason": None if ready else (
                    "Set MAST_CONTEXT_WINDOW for this model so the complete prompt can be checked."
                    if not self.context_window else "Install the mast extra and configure OPENAI_API_KEY on the server."),
                "context_window": self.context_window, "reasoning_effort": self.reasoning_effort,
                "max_completion_tokens": self.max_completion_tokens, "temperature": self.temperature,
                "max_input_tokens": self.max_input_tokens}

    def input_budget(self, prompt):
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("o200k_base")
            # One user message; reserve additional framing margin beyond this estimate.
            tokens = len(encoding.encode(prompt, disallowed_special=())) + 7
        except Exception as exc:
            raise DomainError("Unable to load the MAST tokenizer. Check the server's tiktoken installation and vocabulary cache. Nothing was sent.") from exc
        # Local token counts and published windows are estimates, not an exact
        # provider admission boundary. Leave proportional headroom for long traces.
        margin = max(4096, math.ceil((self.context_window or 0) * 0.05))
        context_available = max(0, (self.context_window or 0) - self.max_completion_tokens - margin)
        available = min(context_available, self.max_input_tokens)
        fits = bool(self.context_window) and tokens <= available
        return {"estimated_input_tokens": tokens, "tokenizer": encoding.name,
                "input_token_limit": available, "context_window": self.context_window,
                "reserved_output_tokens": self.max_completion_tokens, "token_margin": margin,
                "budget_policy": "bounded-input-with-five-percent-headroom/v2",
                "configured_input_token_limit": self.max_input_tokens,
                "can_analyze": fits,
                "reason": None if fits else (
                    f"The complete MAST prompt needs approximately {tokens:,} input tokens; "
                    f"the per-request limit is {available:,} after applying the configured input cap and output reserve. "
                    "Use smaller chunks or review the configured input limit. Nothing was truncated or sent.")}

    def complete(self, prompt):
        from openai import OpenAI

        budget = self.input_budget(prompt)
        if not budget["can_analyze"]:
            raise DomainError(budget["reason"])
        parameters = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                      "reasoning_effort": self.reasoning_effort,
                      "max_completion_tokens": self.max_completion_tokens, "store": False}
        if self.temperature is not None:
            parameters["temperature"] = self.temperature
        with OpenAI(base_url="https://api.openai.com/v1", timeout=300.0, max_retries=0) as client:
            result = client.chat.completions.create(**parameters)
        if not result.choices or not result.choices[0].message.content:
            raise ValueError("The judge returned no textual assessment")
        return {"text": result.choices[0].message.content, "provider": "openai", "model": result.model,
                "requested_model": self.model, "request_id": result.id,
                "finish_reason": result.choices[0].finish_reason,
                "usage": result.usage.model_dump() if result.usage else None,
                "temperature": self.temperature, "reasoning_effort": self.reasoning_effort,
                "context_window": self.context_window, "max_completion_tokens": self.max_completion_tokens}
