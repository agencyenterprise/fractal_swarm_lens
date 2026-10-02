from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import sqlite3

import pytest

from swarm_lens import DomainError, Fact, Framework
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.core.models import Conflict
from conftest import AT, Facts


def test_nested_forks_preserve_exact_prefix(framework, branch):
    child = framework.fork(branch.id, 5, "First intervention")
    framework.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": "Check your work"}, 5)
    grandchild = framework.fork(child.id, 6, "Second intervention")
    framework.intervene(grandchild.id, "agent.removed", {"id": "a"}, 6)
    framework.intervene(branch.id, "environment.updated", {"goal": "Later parent goal"}, 7)
    assert list(framework.state(grandchild.id).messages) == ["m1"]
    assert framework.state(child.id).memories["memory-a"].content == "old"
    assert framework.state(grandchild.id).environment.goal == "Solve the task"
    assert framework.state(child.id).agents["a"].active
    assert not framework.state(grandchild.id).agents["a"].active
    assert framework.state(branch.id).agents["a"].system_prompt is None
    assert [event.position for event in framework.history(grandchild.id)] == list(range(1, 8))


def test_time_travel_and_restart(framework, branch):
    assert framework.state(branch.id, 4).memories == {}
    assert framework.state(branch.id, 5).memories["memory-a"].content == "old"
    assert framework.state(branch.id, 6).memories["memory-a"].content == "new"
    reopened = Framework(SQLiteHistory(framework.store.path))
    assert reopened.state(branch.id).to_dict() == framework.state(branch.id).to_dict()
    state = reopened.state(branch.id)
    state.agents["a"].name = "mutated view"
    assert reopened.state(branch.id).agents["a"].name == "Agent A"


def test_historical_fork_before_parent_creation_point(framework, branch):
    child = framework.fork(branch.id, 6, "child")
    grandchild = framework.fork(child.id, 2, "earlier")
    assert framework.state(grandchild.id).channels == {}
    assert len(framework.history(grandchild.id)) == 2


@pytest.mark.parametrize("cursor", [-1, 8])
def test_invalid_cursor_rejected(framework, branch, cursor):
    with pytest.raises(DomainError):
        framework.state(branch.id, cursor)
    with pytest.raises(DomainError):
        framework.fork(branch.id, cursor, "bad")


def test_invalid_batch_is_not_partially_committed(framework, branch):
    with pytest.raises(DomainError):
        framework.ingest(branch.id, Facts(
            Fact("agent.added", {"id": "b", "name": "B"}, AT),
            Fact("message.created", {"id": "bad", "channel_id": "missing", "content": "x"}, AT),
        ))
    assert framework.store.branch(branch.id).head == 7
    assert "b" not in framework.state(branch.id).agents


def test_duplicate_event_rolls_back_sql_transaction(framework, branch):
    original = framework.history(branch.id)[0]
    with pytest.raises(Conflict):
        framework.ingest(branch.id, Facts(
            Fact("agent.added", {"id": "b", "name": "B"}, AT),
            Fact("environment.updated", {"goal": "duplicate"}, AT, id=original.id),
        ))
    assert framework.store.branch(branch.id).head == 7
    assert "b" not in framework.state(branch.id).agents


def test_concurrent_edits_have_one_winner(framework, branch):
    def edit(goal):
        try:
            framework.intervene(branch.id, "environment.updated", {"goal": goal}, 7)
            return "accepted"
        except Conflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["first", "second"]))
    assert sorted(results) == ["accepted", "conflict"]
    assert framework.store.branch(branch.id).head == 8


def test_references_and_lifecycle(framework, branch):
    framework.intervene(branch.id, "agent.removed", {"id": "a"}, 7)
    with pytest.raises(DomainError, match="Removed"):
        framework.ingest(branch.id, Facts(Fact("message.created", {
            "id": "late", "channel_id": "c", "sender_id": "a", "content": "late",
        }, AT)))
    assert len(framework.state(branch.id).messages) == 2
    assert framework.state(branch.id).channels["c"].members == []


def test_tool_lifecycle(framework, branch):
    framework.ingest(branch.id, Facts(
        Fact("tool.started", {"id": "t", "agent_id": "a", "tool_name": "calculate", "status": "running"}, AT),
        Fact("tool.completed", {"id": "t", "result": 4, "status": "completed"}, AT),
    ))
    assert framework.state(branch.id, 8).tools["t"].status == "running"
    assert framework.state(branch.id, 9).tools["t"].result == 4
    with pytest.raises(DomainError, match="not running"):
        framework.ingest(branch.id, Facts(Fact("tool.completed", {"id": "t", "status": "completed"}, AT)))


def test_relational_projection_has_foreign_keys(framework, branch):
    with sqlite3.connect(framework.store.path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT content FROM messages ORDER BY rowid").fetchall() == [("first",), ("future",)]
        assert db.execute("SELECT count(*) FROM memory_revisions").fetchone()[0] == 2


def test_observation_without_channel_or_message_is_supported(framework, branch):
    framework.ingest(branch.id, Facts(Fact("observation.recorded", {"type": "METRIC", "agent_id": "a", "value": 4}, AT)))
    assert framework.store.branch(branch.id).head == 8


def test_runtime_is_an_application_extension(framework, branch):
    with pytest.raises(DomainError, match="no execution runtime"):
        framework.continue_run(branch.id, 1)

    class Runtime:
        id = "test-runtime"
        def capabilities(self):
            return {"continue": True}
        def continue_from(self, state, steps):
            yield Fact("message.created", {"id": "generated", "channel_id": "c", "sender_id": "a",
                                          "content": state.agents["a"].system_prompt}, AT)

    child = framework.fork(branch.id, 4, "runtime")
    framework.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": "new instruction"}, 4)
    framework.runtime = Runtime()
    assert framework.continue_run(child.id, 1) == 1
    assert framework.state(child.id).messages["generated"].content == "new instruction"
    assert "generated" not in framework.state(branch.id).messages


@pytest.mark.parametrize("lazy", [False, True])
def test_runtime_does_not_append_to_a_concurrently_changed_branch(framework, branch, lazy):
    class Runtime:
        id = "racing-runtime"

        def capabilities(self):
            return {"continue": True}

        def continue_from(self, state, steps):
            def produce():
                framework.intervene(branch.id, "environment.updated", {"goal": "Concurrent edit"}, 7)
                yield Fact("message.created", {
                    "id": "stale", "channel_id": "c", "sender_id": "a", "content": "outdated",
                }, AT)
            return produce() if lazy else list(produce())

    framework.runtime = Runtime()
    with pytest.raises(Conflict):
        framework.continue_run(branch.id, 1)
    assert framework.state(branch.id).environment.goal == "Concurrent edit"
    assert "stale" not in framework.state(branch.id).messages
