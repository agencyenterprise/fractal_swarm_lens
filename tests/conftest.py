import pytest

from swarm_lens import Fact, Framework
from swarm_lens.adapters.sqlite import SQLiteHistory

AT = "2026-01-01T12:00:00+00:00"


class Facts:
    def __init__(self, *facts):
        self.items = facts

    def facts(self):
        yield from self.items


@pytest.fixture
def framework(tmp_path):
    return Framework(SQLiteHistory(tmp_path / "history.sqlite"))


@pytest.fixture
def branch(framework):
    branch = framework.create_run("Experiment")
    framework.ingest(branch.id, Facts(
        Fact("environment.updated", {"goal": "Solve the task"}, AT),
        Fact("agent.added", {"id": "a", "name": "Agent A", "model": "model-a"}, AT),
        Fact("channel.created", {"id": "c", "name": "Shared", "members": ["a"]}, AT),
        Fact("message.created", {"id": "m1", "channel_id": "c", "sender_id": "a", "content": "first"}, AT),
        Fact("memory.written", {"id": "memory-a", "owner_id": "a", "content": "old"}, AT),
        Fact("memory.written", {"id": "memory-a", "owner_id": "a", "content": "new"}, AT),
        Fact("message.created", {"id": "m2", "channel_id": "c", "sender_id": "a", "content": "future"}, AT),
    ), batch_size=3)
    return framework.store.branch(branch.id)
