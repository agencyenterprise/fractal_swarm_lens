"""Seeded spread tracer: follow one phrase or one message through the recorded "who read whom" graph.

The user picks what to trace. The tracer reads the branch once, in event order, and records for each agent
when it first saw the content (in which agent's message, or from the environment) and when it first
repeated it. It reports no automatic patient zero and no reproduction number: on short multi-agent traces
those estimates did not find the injected agent (see docs/plugins/spread-tracer.md).
"""
import json
import math
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from swarm_lens.core.models import DomainError
from swarm_lens.core.reducer import apply
from swarm_lens.plugins import Annotation, Metric, Report

ENVIRONMENT = "environment"
NGRAM = 4
MIN_AGENTS_FOR_FIT = 10
MIN_CARRIERS_FOR_FIT = 3
FIT_GRID = 100
FIT_RATES = [10 ** (-3 + i * 0.05) for i in range(81)]
TOKEN = re.compile(r"\(\w\)|\w+")
CAVEATS = ("Carrying means the text matches. It does not tell use from refusal or quotation.",
           "Only events in the analyzed range are read; sightings before it are not counted.",
           "With read model 'auto', one message that records delivered_sources switches the whole range to it.")


def as_text(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def ngrams(text: str) -> set[str]:
    tokens = TOKEN.findall(text.lower())
    return {" ".join(tokens[i:i + NGRAM]) for i in range(len(tokens) - NGRAM + 1)}


def normalized(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class Sighting:
    """An agent saw the content at `position`. `source` wrote it in event `source_event_id` at `source_position`."""
    position: int
    source: str
    source_position: int
    source_event_id: str

    def as_dict(self) -> dict:
        return {"source": self.source, "position": self.position, "source_position": self.source_position,
                "source_event_id": self.source_event_id}


@dataclass
class AgentTrace:
    agent_id: str
    sightings: list[Sighting] = field(default_factory=list)
    first_carry: dict | None = None
    repeats: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "repeated" if self.first_carry else "saw_not_repeated" if self.sightings else "never_saw"

    def ordered_sightings(self) -> list[Sighting]:
        """Earliest seen first; on the same event, the source written earlier first."""
        return sorted(self.sightings, key=lambda s: (s.position, s.source_position, s.source))

    def row(self) -> dict:
        sightings = self.ordered_sightings()
        return {"agent_id": self.agent_id, "status": self.status,
                "first_seen": sightings[0].as_dict() if sightings else None,
                "sightings": [s.as_dict() for s in sightings],
                "first_carry": self.first_carry, "repeats": self.repeats,
                "received_from": sorted({s.source for s in self.sightings})}


class Spread:
    """Per-agent sightings and carries, filled in event order so a source always precedes its carrier."""

    def __init__(self, agent_ids, start: int):
        self.agents = {agent_id: AgentTrace(agent_id) for agent_id in agent_ids}
        self.edges: list[dict] = []
        self.curve: list[tuple[int, float]] = [(start, 0.0)]

    def saw(self, agent_id: str, sighting: Sighting) -> None:
        agent = self.agents[agent_id]
        if sighting not in agent.sightings:
            agent.sightings.append(sighting)

    def carry(self, agent_id: str, event, via: str) -> list:
        agent = self.agents[agent_id]
        if agent.first_carry is not None:
            agent.repeats.append({"position": event.position, "event_id": event.id, "via": via})
            return [Annotation(event.position, event.position, "Repeated", agent_id,
                               data={"role": "repeat", "via": via}, cited_event_ids=(event.id,))]
        sources = agent.ordered_sightings()
        parent = sources[0] if sources else None
        source = parent.source if parent else None
        agent.first_carry = {"position": event.position, "event_id": event.id, "via": via, "source": source,
                             "sources": [s.as_dict() for s in sources]}
        self.edges.append({"from": source, "from_position": parent and parent.source_position,
                           "from_event_id": parent and parent.source_event_id, "to": agent_id,
                           "to_position": event.position, "to_event_id": event.id, "via": via})
        fraction = sum(a.first_carry is not None for a in self.agents.values()) / len(self.agents)
        self.curve.append((event.position, fraction))
        label = f"Earliest recorded source: {source}" if parent else "Carried it first, no recorded source"
        return [Annotation(event.position, event.position, label, agent_id,
                           data={"role": "first", "via": via, "source": source,
                                 "sources": [s.as_dict() for s in sources]},
                           cited_event_ids=(event.id, *dict.fromkeys(s.source_event_id for s in sources))),
                Metric(event.position, "repeated_fraction", fraction)]

    def unrepeated(self) -> list[Annotation]:
        findings = []
        for agent in self.agents.values():
            if agent.status != "saw_not_repeated":
                continue
            sightings = agent.ordered_sightings()
            findings.append(Annotation(sightings[0].position, sightings[0].position, "Saw it, did not repeat",
                                       agent.agent_id, data={"role": "saw", "source": sightings[0].source,
                                                             "sightings": [s.as_dict() for s in sightings]},
                                       cited_event_ids=tuple(dict.fromkeys(s.source_event_id for s in sightings))))
        return findings

    def counts(self) -> dict:
        statuses = [a.status for a in self.agents.values()]
        return {"agents": len(statuses), **{s: statuses.count(s) for s in ("repeated", "saw_not_repeated", "never_saw")}}


class SpreadTracer:
    id, version = "spread-tracer", "1.0.0"
    title = "Spread tracer"
    description = "Trace one phrase or message: who saw it, from whom, when, and who repeated it."

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        phrase: str | None = Field(None, title="Phrase",
                                   description="Text to trace, matched without case. Give this or a seed message.")
        seed_event_id: str | None = Field(None, title="Seed message event ID",
                                          description="A message.created event whose content you want to trace.")
        min_shared_ngrams: int = Field(5, ge=1, le=100, title="Shared 4-word phrases",
                                       description="With a seed message: how many of its 4-word phrases another "
                                                   "message or tool call must reuse to count as carrying it.")
        read_model: Literal["auto", "delivered_sources", "channel_membership"] = Field(
            "auto", title="Who saw a message",
            description="delivered_sources: what each message records it read. channel_membership: every member "
                        "of the channel when it was posted. auto: delivered_sources when any message records it.")

        @field_validator("phrase", "seed_event_id")
        @classmethod
        def blank_is_missing(cls, value):
            return (value or "").strip() or None

        @model_validator(mode="after")
        def one_seed(self):
            if (self.phrase is None) == (self.seed_event_id is None):
                raise ValueError("give exactly one of phrase or seed_event_id")
            return self

    def analyze(self, view, start, end, params):
        events = view.events(start, end)
        agent_ids = list(view.state_at(end).agents)
        if not agent_ids:
            raise DomainError("This branch has no agents to trace")
        matches, seed = seed_matcher(events, params)
        read_model = params.read_model
        if read_model == "auto":
            read_model = ("delivered_sources" if any("delivered_sources" in e.source for e in events
                                                     if e.kind == "message.created") else "channel_membership")
        spread = Spread(agent_ids, start)
        state = view.state_at(start - 1)
        carrying: dict[str, tuple[int, str, str]] = {}   # message id -> (position, author, event id)

        for event in events:
            apply(state, event)
            found = list(self._observe(event, state, spread, matches, read_model, carrying))
            if event.position == start and not any(isinstance(f, Metric) for f in found):
                yield Metric(start, "repeated_fraction", 0.0)   # one point per event: a carry at `start` has its own
            yield from found

        yield from spread.unrepeated()
        fit = fit_logistic(spread.curve, len(agent_ids), end)
        yield Annotation(end, end, f"Logistic fit: midpoint at event {fit['midpoint_position']}" if fit["shown"]
                         else f"Fit not shown: {fit['reason']}", data=fit)
        yield Report({"seed": seed, "read_model": read_model, "start": start, "end": end,
                      "agents": [a.row() for a in spread.agents.values()], "edges": spread.edges,
                      "curve": [list(point) for point in spread.curve], "fit": fit,
                      "counts": spread.counts(), "caveats": list(CAVEATS)})

    @staticmethod
    def _observe(event, state, spread, matches, read_model, carrying):
        """Sightings and carries of one event. `state` is the state just after it, so channel members
        and tool callers are those of that moment."""
        data = event.data
        if event.kind == "message.created":
            author = data.get("sender_id")
            if author is not None and author not in spread.agents:
                return
            if author and read_model == "delivered_sources":
                for source_id in event.source.get("delivered_sources") or ():
                    if source_id in carrying and carrying[source_id][1] != author:
                        position, source, source_event = carrying[source_id]
                        spread.saw(author, Sighting(event.position, source, position, source_event))
            if not matches(as_text(data.get("content"))):
                return
            carrying[data.get("id", event.id)] = (event.position, author or ENVIRONMENT, event.id)
            if author:
                yield from spread.carry(author, event, "message")
            if author is None or read_model == "channel_membership":
                channel = state.channels.get(data.get("channel_id"))
                for member in channel.members if channel else ():
                    if member != author and member in spread.agents:
                        spread.saw(member, Sighting(event.position, author or ENVIRONMENT, event.position, event.id))
        elif event.kind.startswith("tool."):
            call = state.tools.get(data.get("id"))
            if call is None or call.agent_id not in spread.agents:
                return
            if event.kind in ("tool.started", "tool.recorded") and matches(as_text(data.get("arguments"))):
                yield from spread.carry(call.agent_id, event, "tool_call")
            if (event.kind in ("tool.completed", "tool.recorded")
                    and matches(as_text(data.get("result")) + "\n" + as_text(data.get("error")))):
                spread.saw(call.agent_id, Sighting(event.position, ENVIRONMENT, event.position, event.id))


def event_text(event) -> str:
    data = event.data
    return "\n".join(as_text(data.get(key)) for key in ("content", "arguments", "result", "error"))


def seed_matcher(events, params):
    """A text predicate for the traced content, and a description of the seed for the report.

    A seed message is traced by the 4-word phrases it introduced: those that no earlier text in the range
    contains. Phrases it shares with earlier text (the task, common wording) would mark messages that came
    before the seed as carriers.
    """
    if params.phrase:
        phrase = normalized(params.phrase)
        return (lambda text: phrase in normalized(text)), {"mode": "phrase", "phrase": params.phrase}
    seed_event = next((e for e in events if e.id == params.seed_event_id), None)
    if seed_event is None or seed_event.kind != "message.created":
        raise DomainError(f"Seed {params.seed_event_id!r} is not a message.created event in the analyzed range")
    earlier = set().union(*(ngrams(event_text(e)) for e in events if e.position < seed_event.position))
    traits = ngrams(as_text(seed_event.data.get("content"))) - earlier
    needed = params.min_shared_ngrams
    if len(traits) < needed:
        raise DomainError(f"The seed message introduced {len(traits)} new 4-word phrases. Set 'Shared 4-word "
                          f"phrases' to at most {len(traits)}, or pick a longer message.")
    seed = {"mode": "message", "event_id": seed_event.id, "position": seed_event.position,
            "author": seed_event.data.get("sender_id") or ENVIRONMENT,
            "new_ngrams": len(traits), "min_shared_ngrams": needed}
    return (lambda text: len(ngrams(text) & traits) >= needed), seed


def fit_logistic(curve: list[tuple[int, float]], population: int, end: int) -> dict:
    """A descriptive logistic curve K / (1 + exp(-rate (t - midpoint))), shown only with enough agents.

    It is fitted to the step function itself, sampled at evenly spaced positions from start to end, so long
    flat stretches weigh as much as their length.
    """
    carriers = len(curve) - 1
    if population < MIN_AGENTS_FOR_FIT:
        return {"shown": False, "reason": f"n too small: {population} agents (fewer than {MIN_AGENTS_FOR_FIT})"}
    if carriers < MIN_CARRIERS_FOR_FIT:
        return {"shown": False, "reason": f"too few carriers: {carriers} (fewer than {MIN_CARRIERS_FOR_FIT})"}
    first = curve[0][0]
    final = curve[-1][1]
    positions = [first + (end - first) * i / FIT_GRID for i in range(FIT_GRID + 1)]
    samples = [(t, step_value(curve, t)) for t in positions]

    def error(midpoint, rate):
        return sum((final / (1 + math.exp(max(-700.0, min(700.0, -rate * (t - midpoint))))) - y) ** 2
                   for t, y in samples)

    sse, midpoint, rate = min((error(m, r), m, r) for m in positions for r in FIT_RATES)
    return {"shown": True, "model": "logistic", "final_fraction": final, "midpoint_position": round(midpoint, 1),
            "rate_per_event": round(rate, 4), "sse": round(sse, 6),
            "note": "Descriptive only. Prompt Infection (arXiv 2410.07283) reports logistic growth in agent "
                    "societies. This is not a reproduction number."}


def step_value(curve: list[tuple[int, float]], position: float) -> float:
    return next(value for at, value in reversed(curve) if at <= position)


def create(services):
    return SpreadTracer()
