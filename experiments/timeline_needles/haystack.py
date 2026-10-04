"""Benign haystack: a recorded three-debater math debate, tiled to reach a target length."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path

from swarm_lens import Fact

from .haystack_mast import RenderMeter

SAMPLE_DIR = Path("examples/aciarena/samples/llm-debate-pair-20261003/control")
CHANNEL_ID = "debate"
EPISODE_GAP = timedelta(minutes=1)


@dataclass(frozen=True)
class Turn:
    agent: str
    round: int
    phase: str
    response: str
    occurred_at: datetime


@dataclass(frozen=True)
class Debate:
    problem: str
    profiles: dict[str, str]
    turns: tuple[Turn, ...]

    @property
    def agents(self):
        return list(self.profiles)

    @property
    def duration(self):
        return self.turns[-1].occurred_at - self.turns[0].occurred_at


@lru_cache(maxsize=1)
def load_debate(sample_dir=SAMPLE_DIR):
    rows = [json.loads(line) for line in (Path(sample_dir) / "trace.jsonl").read_text().splitlines()]
    setup = next(row for row in rows if row["type"] == "setup")
    profiles = {agent["id"]: agent["profile"].strip() for agent in setup["agents"]}
    turns = tuple(Turn(row["agent"], int(row["round"]), row["phase"], row["response"],
                       datetime.fromisoformat(row["occurred_at"]))
                  for row in rows if row["type"] == "model_call")
    return Debate(setup["task"]["problem"], profiles, turns)


def setup_facts(debate, at):
    agents = [Fact("agent.added", {"id": agent, "name": agent, "system_prompt": profile}, at)
              for agent, profile in debate.profiles.items()]
    channel = Fact("channel.created", {"id": CHANNEL_ID, "name": "debate", "members": debate.agents}, at)
    return agents + [channel]


def episode_facts(debate, episode, episodes):
    """One full debate. Timestamps are shifted so episodes follow each other in time."""
    shift = episode * (debate.duration + EPISODE_GAP)
    start = (debate.turns[0].occurred_at + shift).isoformat()
    marker = Fact("environment.updated", {
        "task": debate.problem,
        "goal": f"Episode {episode + 1} of {episodes}: debate the problem and agree on a final answer."}, start)
    messages = [Fact("message.created", {
        "id": f"e{episode}-m{index}", "channel_id": CHANNEL_ID, "sender_id": turn.agent, "content": turn.response,
        "metadata": {"round": turn.round, "phase": turn.phase}}, (turn.occurred_at + shift).isoformat())
        for index, turn in enumerate(debate.turns)]
    return [marker] + messages


@lru_cache(maxsize=8)
def haystack_facts(episodes):
    """Setup facts, then the debate repeated `episodes` times back to back."""
    if episodes < 1:
        raise ValueError("A haystack needs at least one episode")
    debate = load_debate()
    facts = setup_facts(debate, debate.turns[0].occurred_at.isoformat())
    for episode in range(episodes):
        facts += episode_facts(debate, episode, episodes)
    return tuple(facts)


def debaters():
    return [agent for agent in load_debate().agents if agent.startswith("debater_")]


def rendered_tokens(facts):
    meter = RenderMeter()
    meter.add(facts)
    return meter.tokens


def build(target_tokens: int, seed: int) -> list[Fact]:
    """Fewest tiled episodes whose rendered size reaches target_tokens. Tiling is deterministic;
    `seed` is accepted for interface parity with haystack_mast.build."""
    episodes = max(1, -(-target_tokens // rendered_tokens(list(haystack_facts(1)))))
    while rendered_tokens(list(haystack_facts(episodes))) < target_tokens:
        episodes += 1
    return list(haystack_facts(episodes))
