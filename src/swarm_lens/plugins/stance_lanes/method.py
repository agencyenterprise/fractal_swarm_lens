"""Stance lanes: each agent's position per message, its flips, whose read message it flipped toward, and
the run's overall pattern. An explorer, not a detector: it describes what happened and names no culprit.

Stance extraction is a parameter. `regex` takes the last match of a user pattern (first capture group) in
the message text. `llm` asks a labeller a user-set stance question about each message. Both labels pass
through `normalize_label`, so "(B)", "b." and "(b) Melanoma" become the same stance "b".

History before `start` is read for context (earlier stances, messages that were read), but every finding
lies in `start..end`.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from swarm_lens.core.models import DomainError
from swarm_lens.plugins import Annotation, Metric, Report

NO_STANCE = {"", "none", "n/a", "na", "unclear", "unknown", "no position", "no stance"}
ENUMERATED_OPTION = re.compile(r"^\(?([a-z0-9])[).:]\s+\S")
EDGE = "()[]{}\"'`*.,;:!? "


def normalize_label(label: str | None) -> str | None:
    """Lowercase, single spaces, no wrapping brackets, quotes or end punctuation; "(b) text" becomes "b"."""
    if label is None:
        return None
    text = " ".join(str(label).split()).lower()
    option = ENUMERATED_OPTION.match(text)
    if option:
        return option.group(1)
    text = text.strip(EDGE)
    return None if text in NO_STANCE else text


@dataclass(frozen=True)
class Utterance:
    """One agent message, with the tool calls the agent made since its previous message."""
    event_id: str
    position: int
    agent_id: str
    message_id: str | None
    content: str
    actions: tuple[str, ...]
    sources: tuple[str, ...] | None  # delivered_sources: message ids read before writing; None = not recorded

    @property
    def text_with_actions(self) -> str:
        """Chronological: the tool calls came before the message."""
        return "\n".join([*self.actions, self.content])


class Labeller(Protocol):
    def label(self, task: str, question: str, items: list[tuple[str, str]]) -> list[str | None]:
        """One raw stance label (or None) per (agent_id, text) item, in order."""


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stance_source: Literal["regex", "llm"] = Field(
        "regex", title="Stance source",
        description="regex: the last match of your pattern in each message. llm: a model answers your stance "
                    "question for each message; the messages and the arguments of the agents' tool calls are sent "
                    "to OpenAI (gpt-4o-mini) with the server's OPENAI_API_KEY.")
    pattern: str | None = Field(
        None, title="Stance pattern",
        description="Regex with a capture group; the first group of the last match is the stance. "
                    r"Option letters such as (B): \(([A-Z])\)")
    stance_question: str | None = Field(
        None, title="Stance question", json_schema_extra={"format": "textarea"},
        description="For the llm source: what position to read from each message, e.g. "
                    "'Does the agent use the undocumented endpoint, refuse it, or not yet decide?'")
    answer_key: str | None = Field(
        None, title="Answer key", description="Optional correct stance; cells are then marked correct or wrong")
    min_messages: int = Field(2, ge=1, le=10_000, title="Minimum messages",
                              description="Agents with fewer messages in the range get no lane (their messages "
                                          "still count as read evidence)")

    @model_validator(mode="after")
    def check_source(self):
        if self.stance_source == "regex":
            if not self.pattern:
                raise ValueError("The regex stance source needs a pattern with a capture group")
            try:
                groups = re.compile(self.pattern).groups
            except re.error as error:
                raise ValueError(f"Invalid stance pattern: {error}") from error
            if groups < 1:
                raise ValueError("The stance pattern needs a capture group, e.g. \\(([A-Z])\\)")
        elif not (self.stance_question or "").strip():
            raise ValueError("The llm stance source needs a stance question")
        return self


@dataclass
class History:
    """Every agent message up to `end`, with each agent's stance carried forward over messages stating none."""
    utterances: list[Utterance]
    stance: dict[str, str | None]   # event id -> the author's stance at that message
    stated: dict[str, bool]         # event id -> the message itself stated a stance
    by_message: dict[str, Utterance] = field(init=False)

    def __post_init__(self):
        self.by_message = {u.message_id: u for u in self.utterances if u.message_id}

    def of(self, agent: str) -> list[Utterance]:
        return [u for u in self.utterances if u.agent_id == agent]

    def read_set(self, u: Utterance) -> tuple[dict[str, Utterance], str]:
        """The latest message of each peer that `u`'s author read before writing `u`, and how we know.

        With delivered_sources the read-set is exact (an empty list means the agent read nobody). Without it,
        we assume each peer's latest earlier message was visible, and label the basis "timing".
        """
        if u.sources is not None:
            candidates, basis = [self.by_message[s] for s in u.sources if s in self.by_message], "delivered_sources"
        else:
            candidates, basis = [v for v in self.utterances if v.position < u.position], "timing"
        latest: dict[str, Utterance] = {}
        for v in candidates:
            if v.agent_id != u.agent_id and v.position < u.position:
                if v.agent_id not in latest or v.position > latest[v.agent_id].position:
                    latest[v.agent_id] = v
        return latest, basis


@dataclass
class Flip:
    utterance: Utterance
    old: str
    new: str
    read: dict[str, Utterance]   # peer -> latest message of that peer the agent read
    basis: str                   # "delivered_sources" | "timing"
    credit: dict[str, float]     # peers whose read message held `new`


@dataclass
class Lane:
    agent_id: str
    cells: list[Utterance]       # the agent's messages in start..end
    challenged: list[Utterance]  # cells written after reading a peer message with a different stance


class StanceLanes:
    id, version = "stance-lanes", "1.0.0"
    title = "Stance lanes"
    description = "Each agent's stance per message, its flips toward peers it read, holdouts and the run's pattern."
    Params = Params

    def __init__(self, labeller: Labeller | None = None):
        self.labeller = labeller

    def analyze(self, view, start, end, params):
        events = view.events(1, end)
        history = self._history(params, task_text(events), utterances(events))
        in_range = Counter(u.agent_id for u in history.utterances if u.position >= start)
        agents = sorted(agent for agent, n in in_range.items() if n >= params.min_messages)
        if not agents:
            raise DomainError(f"No agent has {params.min_messages} or more messages in events {start}..{end}")
        lanes = [build_lane(history, agent, start) for agent in agents]
        flips = [flip for agent in agents for flip in find_flips(history, agent, start)]
        key = normalize_label(params.answer_key)
        yield from findings(history, lanes, flips, key)
        yield Report(report(history, lanes, flips, key, params))

    def _history(self, params: Params, task: str, items: list[Utterance]) -> History:
        if params.stance_source == "regex":
            pattern = re.compile(params.pattern)
            raw = [next(reversed([m.group(1) for m in pattern.finditer(u.content)]), None) for u in items]
        elif self.labeller is None:
            raise DomainError("The llm stance source is not configured on this server")
        else:
            raw = self.labeller.label(task, params.stance_question.strip(),
                                      [(u.agent_id, u.text_with_actions) for u in items])
            if len(raw) != len(items):
                raise DomainError(f"The stance labeller returned {len(raw)} labels for {len(items)} messages")
        current: dict[str, str | None] = {}
        stance, stated = {}, {}
        for u, label in zip(items, raw):
            normalized = normalize_label(label)
            current[u.agent_id] = normalized or current.get(u.agent_id)
            stance[u.event_id], stated[u.event_id] = current[u.agent_id], normalized is not None
        return History(items, stance, stated)


# ---------------------------------------------------------------- reading the branch

def utterances(events) -> list[Utterance]:
    pending: dict[str, list[str]] = {}
    out = []
    for event in events:
        data = event.data
        if event.kind in ("tool.started", "tool.recorded") and data.get("agent_id"):
            pending.setdefault(data["agent_id"], []).append(f"[tool {data.get('tool_name')}] {data.get('arguments')}")
        elif event.kind == "message.created" and data.get("sender_id"):
            sender = data["sender_id"]
            sources = event.source.get("delivered_sources")
            out.append(Utterance(event.id, event.position, sender, data.get("id"), data.get("content") or "",
                                 tuple(pending.pop(sender, [])), tuple(sources) if isinstance(sources, list) else None))
    return out


def task_text(events) -> str:
    for event in events:
        if event.kind == "message.created" and not event.data.get("sender_id"):
            return event.data.get("content") or ""
    for event in events:
        if event.kind == "environment.updated" and event.data.get("goal"):
            return event.data["goal"]
    return ""


# ---------------------------------------------------------------- dynamics

def build_lane(history: History, agent: str, start: int) -> Lane:
    cells = [u for u in history.of(agent) if u.position >= start]
    challenged = []
    for u in cells:
        own = history.stance[u.event_id]
        read, _ = history.read_set(u)
        if own is not None and any(history.stance[v.event_id] not in (None, own) for v in read.values()):
            challenged.append(u)
    return Lane(agent, cells, challenged)


def find_flips(history: History, agent: str, start: int) -> list[Flip]:
    """Stance changes between the agent's consecutive messages; the earlier one may lie before `start`."""
    messages = history.of(agent)
    flips = []
    for before, u in zip(messages, messages[1:]):
        old, new = history.stance[before.event_id], history.stance[u.event_id]
        if u.position < start or old is None or new is None or old == new:
            continue
        read, basis = history.read_set(u)
        holders = sorted(peer for peer, v in read.items() if history.stance[v.event_id] == new)
        flips.append(Flip(u, old, new, read, basis, {peer: 1 / len(holders) for peer in holders}))
    return flips


def typology(history: History, lanes: list[Lane], flips: list[Flip]) -> dict:
    """The run's pattern from each lane's first and last stance in the range. Ground truth is never used."""
    first = {lane.agent_id: next((history.stance[u.event_id] for u in lane.cells if history.stance[u.event_id]), None)
             for lane in lanes}
    last = {lane.agent_id: history.stance[lane.cells[-1].event_id] for lane in lanes}
    agents = [agent for agent in first if first[agent] is not None]
    unlabelled = [agent for agent in first if first[agent] is None]
    note = f" {', '.join(unlabelled)} stated no stance." if unlabelled else ""
    result = _pattern(agents, first, last, bool(flips))
    return {**result, "text": result["text"] + note, "unlabelled": unlabelled}


def _pattern(agents: list[str], first: dict, last: dict, flipped: bool) -> dict:
    if not agents:
        return {"kind": "no stances", "agents": [], "text": "No message stated a stance."}
    if not flipped:
        if len({first[a] for a in agents}) == 1:
            return {"kind": "unanimous", "agents": agents, "text": "Every labelled agent held one stance throughout."}
        return {"kind": "stalemate", "agents": agents, "text": "Agents disagreed and nobody changed stance."}
    final = {last[a] for a in agents}
    if len(final) > 1:
        return {"kind": "mixed", "agents": agents, "text": "Stances changed but the agents did not converge."}
    consensus = final.pop()
    origin = [a for a in agents if first[a] == consensus]
    movers = [a for a in agents if first[a] != consensus]
    if not origin:
        return {"kind": "new consensus", "agents": [],
                "text": f"All agents converged on {consensus!r}, which nobody held at first."}
    if not movers:
        return {"kind": "wavered", "agents": agents,
                "text": f"Agents left the shared stance {consensus!r} and came back to it."}
    if len(origin) < len(movers):
        return {"kind": "cascade", "agents": origin,
                "text": f"{', '.join(origin)} started in the minority with {consensus!r}; everyone ended there."}
    if len(origin) > len(movers):
        return {"kind": "dissenter gives in", "agents": movers,
                "text": f"{', '.join(movers)} moved to the majority's first stance {consensus!r}."}
    return {"kind": "split resolved", "agents": origin,
            "text": f"Agents started evenly split; everyone ended on {', '.join(origin)}'s {consensus!r}."}


def transition(old: str, new: str, key: str | None) -> str | None:
    if key is None:
        return None
    if old == key:
        return "C->W"
    return "W->C" if new == key else "W->W'"


# ---------------------------------------------------------------- findings

def stance_order(history: History, lanes: list[Lane]) -> list[str]:
    order: list[str] = []
    for u in sorted((u for lane in lanes for u in lane.cells), key=lambda u: u.position):
        stance = history.stance[u.event_id]
        if stance is not None and stance not in order:
            order.append(stance)
    return order


def first_stances_differ(history: History, lanes: list[Lane]) -> bool:
    firsts = {next((history.stance[u.event_id] for u in lane.cells if history.stance[u.event_id]), None)
              for lane in lanes}
    return len(firsts - {None}) > 1


def findings(history: History, lanes: list[Lane], flips: list[Flip], key: str | None):
    order = stance_order(history, lanes)
    for lane in lanes:
        for u in lane.cells:
            stance = history.stance[u.event_id]
            if stance is None:
                continue
            yield Metric(u.position, "stance", order.index(stance) + 1, lane.agent_id)
            if key is not None:
                yield Metric(u.position, "correct", int(stance == key), lane.agent_id)
    for flip in flips:
        toward = sorted(flip.credit)
        label = f"Flip {flip.old} → {flip.new}" + (f" toward {', '.join(toward)}" if toward else "")
        yield Annotation(flip.utterance.position, flip.utterance.position, label, flip.utterance.agent_id,
                         data={"from": flip.old, "to": flip.new,
                               "toward_agent": toward[0] if len(toward) == 1 else None,
                               "toward_agents": flip.credit, "read_basis": flip.basis,
                               "read_event_ids": {peer: v.event_id for peer, v in flip.read.items()},
                               "transition": transition(flip.old, flip.new, key)},
                         cited_event_ids=(flip.utterance.event_id, *(flip.read[peer].event_id for peer in toward)))
    flipped = {flip.utterance.agent_id for flip in flips}
    for lane in lanes:
        if lane.agent_id not in flipped and lane.challenged:
            yield Annotation(lane.challenged[0].position, lane.cells[-1].position, "Holdout", lane.agent_id,
                             data={"challenged_messages": len(lane.challenged)},
                             cited_event_ids=tuple(u.event_id for u in lane.challenged))
    pattern = typology(history, lanes, flips)
    positions = [u.position for lane in lanes for u in lane.cells]
    yield Annotation(min(positions), max(positions), f"Run pattern: {pattern['kind']}",
                     data={**pattern, "first_stances_differ": first_stances_differ(history, lanes)})


def report(history: History, lanes: list[Lane], flips: list[Flip], key: str | None, params: Params) -> dict:
    flipped = Counter(flip.utterance.agent_id for flip in flips)
    return {
        "format": "stance-lanes/v1",
        "stance_source": params.stance_source,
        "pattern": params.pattern if params.stance_source == "regex" else None,
        "stance_question": params.stance_question if params.stance_source == "llm" else None,
        "answer_key": key,
        "stances": stance_order(history, lanes),
        "typology": typology(history, lanes, flips),
        "first_stances_differ": first_stances_differ(history, lanes),
        "read_basis": sorted({flip.basis for flip in flips}),
        "lanes": [{
            "agent_id": lane.agent_id,
            "flips": flipped[lane.agent_id],
            "challenged_messages": len(lane.challenged),
            "holdout": flipped[lane.agent_id] == 0 and bool(lane.challenged),
            "cells": [{"seq": u.position, "event_id": u.event_id, "stance": history.stance[u.event_id],
                       "stated": history.stated[u.event_id],
                       "correct": None if key is None or history.stance[u.event_id] is None
                       else history.stance[u.event_id] == key} for u in lane.cells],
        } for lane in lanes],
        "flips": [{"agent_id": f.utterance.agent_id, "seq": f.utterance.position, "event_id": f.utterance.event_id,
                   "from": f.old, "to": f.new, "toward": f.credit, "basis": f.basis,
                   "read": {peer: {"seq": v.position, "event_id": v.event_id} for peer, v in f.read.items()},
                   "transition": transition(f.old, f.new, key)} for f in flips],
    }
