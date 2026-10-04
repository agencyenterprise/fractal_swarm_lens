"""Optional LLM stance labeller: answers a user-set stance question for each message, ten per call.

Each call sees the labels already used in this run, so one position keeps one label. Every request is cached
on disk by its full content, so re-running an analysis costs nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from swarm_lens.core.models import DomainError
from .method import normalize_label

SYSTEM = (
    "You read messages from a multi-agent run and answer one question about each message: the stance that "
    "message commits to. Answer with a SHORT canonical label (at most 6 words). If the stance is one of "
    "discrete options, answer with the option id only. Reuse a label from LABELS ALREADY USED whenever the "
    "stance is the same. Answer \"none\" if the message commits to no stance. Reply as JSON: "
    "{\"stances\": [{\"i\": <number>, \"stance\": <label>}]}"
)
BATCH, MAX_CHARS = 10, 4000

Complete = Callable[[list[dict]], dict]


def openai_complete(model: str) -> Complete:
    def complete(messages: list[dict]) -> dict:
        if not os.environ.get("OPENAI_API_KEY"):
            raise DomainError("The llm stance source needs OPENAI_API_KEY on the server")
        from openai import OpenAI

        with OpenAI(timeout=60.0, max_retries=2) as client:
            response = client.chat.completions.create(model=model, messages=messages, temperature=0, seed=0,
                                                      response_format={"type": "json_object"})
        return json.loads(response.choices[0].message.content)
    return complete


class LLMStanceLabeller:
    def __init__(self, cache_dir: Path, complete: Complete | None = None, model: str = "gpt-4o-mini"):
        self.cache_dir, self.model = Path(cache_dir), model
        self.complete = complete or openai_complete(model)

    def label(self, task: str, question: str, items: list[tuple[str, str]]) -> list[str | None]:
        labels: list[str | None] = []
        vocabulary: list[str] = []
        for start in range(0, len(items), BATCH):
            batch = items[start:start + BATCH]
            for raw in self._batch(task, question, batch, vocabulary):
                labels.append(raw)
                stance = normalize_label(raw)
                if stance and stance not in vocabulary:
                    vocabulary.append(stance)
        return labels

    def _batch(self, task, question, batch, vocabulary) -> list[str | None]:
        numbered = "\n\n".join(f"### Message {k} (agent {agent})\n{text[-MAX_CHARS:]}"
                               for k, (agent, text) in enumerate(batch))
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"TASK:\n{task[:3000]}\n\nQUESTION: {question}\n\n"
                                                f"LABELS ALREADY USED: {json.dumps(vocabulary)}\n\n{numbered}"}]
        by_index = indexed(self._cached(messages), len(batch))
        missing = [k for k in range(len(batch)) if k not in by_index]
        if missing:
            repair = [*messages, {"role": "assistant", "content": json.dumps({"stances": [
                {"i": k, "stance": v} for k, v in sorted(by_index.items())]})},
                      {"role": "user", "content": f"You skipped messages {missing}. Label exactly those."}]
            for k, stance in indexed(self._cached(repair), len(batch)).items():
                by_index.setdefault(k, stance)
        if any(k not in by_index for k in range(len(batch))):
            raise DomainError("The stance labeller skipped messages twice; try again or use a regex pattern")
        return [by_index[k] for k in range(len(batch))]

    def _cached(self, messages: list[dict]) -> dict:
        request = json.dumps({"model": self.model, "messages": messages}, sort_keys=True)
        path = self.cache_dir / f"{hashlib.sha256(request.encode()).hexdigest()}.json"
        if path.exists():
            return json.loads(path.read_text())
        reply = self.complete(messages)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        partial.write_text(json.dumps(reply))
        os.replace(partial, path)  # readers never see a half-written file
        return reply


def indexed(reply, size: int) -> dict[int, str | None]:
    """The labels of one reply by message index; any malformed reply fails loudly."""
    items = reply.get("stances") if isinstance(reply, dict) else None
    if not isinstance(items, list):
        raise DomainError("The stance labeller replied without a stances list")
    out: dict[int, str | None] = {}
    for item in items:
        index, stance = (item.get("i"), item.get("stance", ...)) if isinstance(item, dict) else (None, ...)
        if type(index) is not int or not 0 <= index < size or index in out:
            raise DomainError(f"The stance labeller replied with an invalid or repeated index: {item!r}")
        if stance is not None and not isinstance(stance, str):
            raise DomainError(f"The stance labeller replied with a stance that is not text: {item!r}")
        out[index] = stance
    return out
