"""Change points in agent behavior: where an agent's messages shift abruptly, as places to jump to and fork from.

This is a navigation layer, not an alarm. On ACIArena a verbosity shift tells attack runs from controls, but no
signal marks the moment an honest agent gives in (docs/plugins/change-points.md has the evaluation).

Each agent's messages form one series. An online Bayesian change-point detector (Adams & MacKay 2007) runs on it:
diagonal Gaussian segments, a constant hazard, the noise variance from successive differences, and the prior from
the agent's history so far. A boundary is reported once the new segment holds `min_support` messages, so every
marker carries its real detection delay.
"""
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import math
import statistics
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from swarm_lens.core.models import DomainError, Event
from swarm_lens.plugins import Annotation, Metric, Report

CHI2_1_MEDIAN = 0.454936
VARIANCE_FLOOR = {"verbosity": 1e-2, "latency": 1e-2, "content": 1e-4}
PRUNE_LOG_MASS = math.log(1e-8)
METRIC_NAMES = {"verbosity": "characters", "latency": "latency_seconds", "content": "content_step"}
Vector = tuple[float, ...]


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: Literal["verbosity", "latency", "content"] = Field(
        "verbosity", description="Message length; reply latency (recorded, else time since the agent's previous "
                                 "event); or message embedding (text-embedding-3-small, needs OPENAI_API_KEY).")
    hazard: float = Field(1 / 30, gt=0, lt=1, description="Prior chance of a change at each message (1/30: about "
                                                          "one change per 30 messages)")
    min_support: int = Field(2, ge=1, le=10, title="Minimum support",
                             description="Messages the new behavior must hold before a change is reported")
    min_shift: float = Field(0.0, ge=0, title="Minimum shift",
                             description="Hide changes smaller than this, in noise standard deviations")
    band_window: int = Field(10, ge=1, title="Band window",
                             description="Without round metadata, changes of different agents this many events "
                                         "apart or closer form a band")


@dataclass
class Step:
    event: Event
    value: Vector
    shown: float | None
    round: object


@dataclass
class Boundary:
    start: int
    detected: int
    shift: float
    run_length_mass: float
    before: Vector
    after: Vector


def difference_variance(xs: Sequence[Vector], floor: float) -> list[float]:
    """Per-dimension noise variance from successive differences, which level shifts barely move."""
    if len(xs) < 2:
        return [floor] * len(xs[0])
    return [max(statistics.median((b[d] - a[d]) ** 2 for a, b in zip(xs, xs[1:])) / (2 * CHI2_1_MEDIAN), floor)
            for d in range(len(xs[0]))]


def log_normal(x: Vector, mean: Sequence[float], var: Sequence[float]) -> float:
    return -0.5 * sum(math.log(2 * math.pi * v) + (xi - m) ** 2 / v for xi, m, v in zip(x, mean, var))


def log_sum_exp(values: Iterable[float]) -> float:
    values = list(values)
    top = max(values)
    return top + math.log(sum(math.exp(v - top) for v in values))


def mean_of(xs: Sequence[Vector]) -> Vector:
    return tuple(sum(column) / len(xs) for column in zip(*xs))


class OnlineDetector:
    """Run-length posterior for one agent's series; `update` returns a boundary when one is confirmed."""

    def __init__(self, hazard: float, floor: float, min_support: int):
        self.hazard, self.floor, self.min_support = hazard, floor, min_support
        self.xs: list[Vector] = []
        self.runs: list[tuple[float, int, list[float]]] = []
        self.reported = 0
        self.run_length = 0
        self.run_length_mass = 1.0

    def update(self, x: Vector) -> Boundary | None:
        self.xs.append(x)
        t = len(self.xs) - 1
        if t == 0:
            self.runs = [(0.0, 1, list(x))]
            self.run_length = 1
            return None
        history = self.xs[:t]
        noise = difference_variance(self.xs, self.floor)
        prior_mean = mean_of(history)
        prior_var = [max(statistics.pvariance(column), n) for column, n in zip(zip(*history), noise)]
        fresh = log_normal(x, prior_mean, [n + p for n, p in zip(noise, prior_var)])
        grown = []
        for log_mass, count, sums in self.runs:
            precision = [1 / p + count / n for p, n in zip(prior_var, noise)]
            mean = [(m / p + s / n) / q for m, p, s, n, q in zip(prior_mean, prior_var, sums, noise, precision)]
            var = [n + 1 / q for n, q in zip(noise, precision)]
            grown.append((log_mass + log_normal(x, mean, var) + math.log(1 - self.hazard), count + 1,
                          [s + xi for s, xi in zip(sums, x)]))
        change = log_sum_exp(m for m, _, _ in self.runs) + math.log(self.hazard) + fresh
        runs = [(change, 1, list(x)), *grown]
        total = log_sum_exp(m for m, _, _ in runs)
        top = max(m - total for m, _, _ in runs)
        self.runs = [(m - total, c, s) for m, c, s in runs if m - total - top > PRUNE_LOG_MASS]
        log_mass, self.run_length, _ = max(self.runs, key=lambda run: run[0])
        self.run_length_mass = math.exp(log_mass)
        start = t - self.run_length + 1
        if self.run_length < self.min_support or start - self.reported < self.min_support:
            return None
        before, after = self.xs[self.reported:start], self.xs[start:]
        boundary = Boundary(start, t, standardized_shift(mean_of(before), mean_of(after), noise),
                            self.run_length_mass, mean_of(before), mean_of(after))
        self.reported = start
        return boundary


def standardized_shift(before: Vector, after: Vector, noise: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 / n for a, b, n in zip(after, before, noise)) / len(noise))


def parse_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


@dataclass
class Scan:
    """One pass over a branch prefix in event order: per-agent detectors and the bands formed so far."""
    params: Params
    encode: Callable[[list[str]], list[Vector]]
    detectors: dict[str, OnlineDetector] = field(default_factory=dict)
    steps: dict[str, list[Step]] = field(default_factory=dict)
    last_seen: dict[str, datetime] = field(default_factory=dict)
    groups: dict[object, dict[str, int]] = field(default_factory=dict)
    banded: set = field(default_factory=set)

    def run(self, events: Sequence[Event]) -> list[tuple[int, Metric | Annotation]]:
        """Findings in emission order, each with the position at which an online run would emit it."""
        messages = [e for e in events if e.kind == "message.created" and e.data.get("sender_id")]
        vectors = self._vectors(messages)
        found: list[tuple[int, Metric | Annotation]] = []
        for event in events:
            agent = event.data.get("sender_id") if event.kind == "message.created" else None
            if agent and event.id in vectors:
                found += self._message(agent, event, vectors[event.id])
            actor = agent or event.data.get("agent_id") or event.data.get("owner_id")
            if actor:
                self.last_seen[actor] = parse_time(event.occurred_at)
        return found

    def _vectors(self, messages: list[Event]) -> dict[str, Vector]:
        signal = self.params.signal
        if signal == "verbosity":
            return {e.id: (math.log1p(len(e.data.get("content") or "")),) for e in messages}
        if signal == "latency":
            return {e.id: (math.log1p(self._latency(e)),) for e in messages}
        texted = [e for e in messages if (e.data.get("content") or "").strip()]
        return dict(zip((e.id for e in texted), self.encode([e.data["content"] for e in texted])))

    def _latency(self, event: Event) -> float:
        recorded = (event.data.get("metadata") or {}).get("latency_seconds")
        if recorded is not None:
            return max(float(recorded), 0.0)
        previous = self.last_seen.get(event.data["sender_id"])
        return max((parse_time(event.occurred_at) - previous).total_seconds(), 0.0) if previous else 0.0

    def _message(self, agent: str, event: Event, value: Vector) -> list[tuple[int, Metric | Annotation]]:
        params = self.params
        detector = self.detectors.setdefault(
            agent, OnlineDetector(params.hazard, VARIANCE_FLOOR[params.signal], params.min_support))
        steps = self.steps.setdefault(agent, [])
        steps.append(Step(event, value, self._shown(event, value, steps), (event.data.get("metadata") or {}).get("round")))
        boundary = detector.update(value)
        found: list[tuple[int, Metric | Annotation]] = []
        if steps[-1].shown is not None:
            found.append((event.position, Metric(event.position, METRIC_NAMES[params.signal], steps[-1].shown, agent)))
        found.append((event.position, Metric(event.position, "messages_since_change", detector.run_length, agent)))
        if boundary and boundary.shift >= params.min_shift:
            found.append((event.position, self._annotation(agent, boundary, steps)))
            found += [(event.position, band) for band in self._band(agent, steps[boundary.start])]
        return found

    def _shown(self, event: Event, value: Vector, steps: list[Step]) -> float | None:
        if self.params.signal == "verbosity":
            return float(len(event.data.get("content") or ""))
        if self.params.signal == "latency":
            return math.expm1(value[0])
        if not steps:
            return None
        previous = steps[-1].value
        dot = sum(a * b for a, b in zip(previous, value))
        norm = math.sqrt(sum(a * a for a in previous) * sum(b * b for b in value)) or 1.0
        return 1 - dot / norm

    def _annotation(self, agent: str, boundary: Boundary, steps: list[Step]) -> Annotation:
        changed, detected = steps[boundary.start].event, steps[boundary.detected].event
        signal = self.params.signal
        if signal == "content":
            label, size = "Content shift", {}
        else:
            before, after = math.expm1(boundary.before[0]), math.expm1(boundary.after[0])
            label = f"{signal.capitalize()} shift ×{after / max(before, 1e-9):.2g}"
            size = {"before": round(before, 3), "after": round(after, 3)}
        return Annotation(
            changed.position, detected.position, label, agent, score=round(boundary.shift, 4),
            data={"signal": signal, "shift_sd": round(boundary.shift, 4), **size,
                  "delay": {"messages": boundary.detected - boundary.start,
                            "events": detected.position - changed.position},
                  "run_length_mass": round(boundary.run_length_mass, 4),
                  "note": "score is the shift size in noise standard deviations; run_length_mass is the model's "
                          "posterior mass on the current run, not a calibrated probability of a real change"},
            cited_event_ids=(changed.id, detected.id))

    def _band(self, agent: str, changed: Step) -> list[Annotation]:
        """One band per round (else per window of `band_window` events) once a second agent changes in it.

        The band is emitted online, so agents that change in the same round later are not added to it."""
        key = (("round", changed.round) if changed.round is not None
               else ("window", changed.event.position // self.params.band_window))
        members = self.groups.setdefault(key, {})
        members.setdefault(agent, changed.event.position)
        if len(members) < 2 or key in self.banded:
            return []
        self.banded.add(key)
        positions = sorted(members.values())
        label = f"{len(members)} agents shifted" + (f" in round {changed.round}" if key[0] == "round" else "")
        return [Annotation(positions[0], positions[-1], label, None, score=float(len(members)),
                           data={"signal": self.params.signal, "agents": sorted(members),
                                 "round": changed.round if key[0] == "round" else None})]


class ChangePoints:
    id, version = "change-points", "1.0.0"
    title = "Change points"
    description = "Marks where an agent's behavior shifted (verbosity, latency or content), to jump to and fork from."
    Params = Params

    def __init__(self, encoder_factory: Callable[[], object] | None = None):
        """No run state lives on the instance: every call rebuilds the detectors from the view. The only shared
        state is a memo of embeddings by text hash (the same text always gets the same vector), behind a lock."""
        self._encoder_factory, self._encoder = encoder_factory, None
        self._cache: dict[str, Vector] = {}
        self._lock = threading.Lock()

    def analyze(self, view, start, end, params):
        found = Scan(params, self._encode).run(view.events(1, end))
        kept = [item for at, item in found if start <= at <= end and getattr(item, "seq_from", start) >= start]
        yield from kept
        yield Report(summary(kept, params))

    def on_events(self, view, events, params):
        """Replays the branch up to the batch, then yields what an online run emits within the batch.

        Replaying keeps no state between calls, at a cost quadratic in the number of batches. Assumes the stream
        starts at event 1, as live ingestion will."""
        if not events:
            return
        first, last = events[0].position, events[-1].position
        for at, item in Scan(params, self._encode).run(view.events(1, last)):
            if first <= at <= last:
                yield item

    def _encode(self, texts: list[str]) -> list[Vector]:
        if self._encoder_factory is None:
            raise DomainError("The content signal needs an embedding encoder: set OPENAI_API_KEY and install "
                              "swarm-lens[embeddings]")
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
        with self._lock:
            missing = {k: t for k, t in zip(keys, texts) if k not in self._cache}
            if missing:
                self._encoder = self._encoder or self._encoder_factory()
                self._cache.update(zip(missing, self._encoder.encode(list(missing.values()))))
            return [self._cache[k] for k in keys]


def summary(findings: list[Metric | Annotation], params: Params) -> dict:
    changes = [f for f in findings if isinstance(f, Annotation) and f.agent_id]
    per_agent: dict[str, int] = {}
    for change in changes:
        per_agent[change.agent_id] = per_agent.get(change.agent_id, 0) + 1
    return {"signal": params.signal, "changes": len(changes), "per_agent": per_agent,
            "bands": sum(1 for f in findings if isinstance(f, Annotation) and not f.agent_id),
            "delay_messages": [c.data["delay"]["messages"] for c in changes],
            "reading": "Navigation aid. A change point says behavior shifted here; it does not say the agent was "
                       "manipulated. See docs/plugins/change-points.md."}


def openai_encoder():
    from swarm_lens.adapters.openai_embeddings import OpenAITextEncoder
    return OpenAITextEncoder.from_env()


def create(services):
    return ChangePoints(openai_encoder)
