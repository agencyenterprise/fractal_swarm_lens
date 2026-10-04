from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import warnings

import pytest

from swarm_lens import DomainError, Fact, Framework
from swarm_lens.adapters import texts
from swarm_lens.adapters.sqlite import SQLiteHistory, encode
from swarm_lens.core.models import Conflict, State
from swarm_lens.core.reducer import apply
from conftest import AT, Facts

LEGACY_WORKSPACE = Path(__file__).parent / "fixtures" / "legacy_v1_history.sql"


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


def conversation(rng, prefix, count, senders):
    """Messages of mixed size and script, memories that repeat the transcript, and finished tool calls."""
    transcript, facts = [], []
    for index in range(count):
        sender = senders[index % len(senders)]
        content = f"{prefix}{index} " + "Ünïcödé ✓ 長い文章 🚀 " * rng.randint(0, 40)
        transcript.append(content)
        facts.append(Fact("message.created", {"id": f"{prefix}m{index}", "channel_id": "c", "sender_id": sender,
                                              "content": content}, AT))
        if index % 3 == 0:
            facts.append(Fact("memory.written", {"id": f"memory-{sender}", "owner_id": sender,
                                                 "content": "\n".join(transcript)}, AT))
        if index % 7 == 0:
            facts += [Fact("tool.started", {"id": f"{prefix}t{index}", "agent_id": sender, "tool_name": "search",
                                            "status": "running"}, AT),
                      Fact("tool.completed", {"id": f"{prefix}t{index}", "result": content, "status": "completed"}, AT)]
    return facts


def assert_every_cursor_replays(framework, run_id):
    """Snapshot-backed reads equal a replay of the effective history from nothing, byte for byte."""
    for branch in framework.store.branches(run_id):
        for cursor in range(branch.head + 1):
            replayed = State(branch.id)
            for event in framework.history(branch.id, cursor):
                apply(replayed, event)
            assert encode(framework.state(branch.id, cursor).to_dict()) == encode(replayed.to_dict())


def test_snapshot_reads_equal_full_replay_across_nested_forks(framework, branch):
    rng = random.Random(3)
    framework.ingest(branch.id, Facts(Fact("agent.added", {"id": "b", "name": "Agent B"}, AT),
                                      Fact("channel.updated", {"id": "c", "members": ["a", "b"]}, AT),
                                      *conversation(rng, "r", 60, ["a", "b"])), batch_size=4)
    child = framework.fork(branch.id, 40, "child")
    framework.intervene(child.id, "agent.removed", {"id": "b"}, 40)
    framework.intervene(child.id, "environment.updated", {"goal": "Fork goal"}, 41)
    framework.ingest(child.id, Facts(*conversation(rng, "c", 20, ["a"])), batch_size=3)
    inherited = framework.fork(child.id, 20, "before the child's fork")
    framework.ingest(inherited.id, Facts(*conversation(rng, "g", 15, ["a", "b"])), batch_size=5)
    framework.ingest(branch.id, Facts(*conversation(rng, "late", 10, ["b"])), batch_size=2)

    assert_every_cursor_replays(framework, branch.run_id)
    assert framework.state(child.id).channels["c"].members == ["a"]
    with sqlite3.connect(framework.store.path) as db:
        rows = dict(db.execute("SELECT CASE WHEN base_cursor IS NULL THEN 'full' WHEN base_branch = branch_id "
                               "THEN 'delta' ELSE 'delta on an ancestor' END, count(*) FROM snapshots GROUP BY 1"))
    assert rows["full"] > 1 and rows["delta"] > rows["full"] and rows["delta on an ancestor"] >= 2


def test_large_texts_are_stored_once_and_read_back_exactly(framework, branch, tmp_path):
    huge = "".join(map(chr, range(0x20, 0xD800))) * 30 + "🚀"
    boundary = "b" * texts.INLINE_LIMIT
    looks_like_a_digest = hashlib.sha256(b"literal").hexdigest()
    contents = [huge, huge, boundary, boundary + "!", looks_like_a_digest, "", "Ünïcödé " * 50]
    framework.ingest(branch.id, Facts(
        *[Fact("message.created", {"id": f"text-{index}", "channel_id": "c", "sender_id": "a", "content": content}, AT)
          for index, content in enumerate(contents)],
        Fact("memory.written", {"id": "memory-a", "owner_id": "a", "content": huge}, AT),
        Fact("observation.recorded", {"type": "trace", "content": huge}, AT)), batch_size=4)

    events = framework.history(branch.id)[7:]
    assert [event.data["content"] for event in events] == [*contents, huge, huge]
    state = framework.state(branch.id)
    assert [state.messages[f"text-{index}"].content for index in range(len(contents))] == contents
    assert state.memories["memory-a"].content == huge
    with sqlite3.connect(framework.store.path) as db:
        stored = dict(db.execute("SELECT digest, length(body) FROM texts"))
        projected = db.execute("SELECT content, content_digest FROM messages WHERE message_id LIKE 'text-%' "
                               "ORDER BY rowid").fetchall()
        inline_copies = db.execute("SELECT count(*) FROM events WHERE instr(data, ?)", (huge[:1000],)).fetchone()[0]
    digests = {content: hashlib.sha256(content.encode()).hexdigest() for content in contents}
    assert set(stored) == {digests[content] for content in contents if len(content) > texts.INLINE_LIMIT}
    assert stored[digests[contents[-1]]] < len(contents[-1].encode()) / 4
    assert inline_copies == 0
    assert [content is None for content, _ in projected] == [len(text) > texts.INLINE_LIMIT for text in contents]

    other = Framework(SQLiteHistory(tmp_path / "other.sqlite"))
    imported = other.import_run(framework.export_run(branch.run_id))
    assert [event.data for event in other.history(imported.id)] == [event.data for event in framework.history(branch.id)]
    assert encode({**other.state(imported.id).to_dict(), "branch_id": None}) == encode({**state.to_dict(), "branch_id": None})


def test_version_1_workspace_migrates_on_open(tmp_path):
    path = tmp_path / "history.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(LEGACY_WORKSPACE.read_text())
        legacy_messages = db.execute("SELECT event_id, content FROM messages ORDER BY rowid").fetchall()
        legacy_snapshots = db.execute("SELECT branch_id, cursor, state FROM snapshots").fetchall()

    framework = Framework(SQLiteHistory(path))
    for branch_id, cursor, state in legacy_snapshots:
        assert framework.store.snapshot(branch_id, cursor).to_dict() == json.loads(state)
    run = framework.store.runs()[0]
    root, fork = framework.store.branches(run.id)
    assert not framework.state(fork.id).agents["b"].active
    assert framework.state(fork.id).messages["m3"].content.endswith("on the fork")
    assert_every_cursor_replays(framework, run.id)

    framework.ingest(fork.id, Facts(*conversation(random.Random(5), "new", 12, ["a"])), batch_size=2)
    framework.fork(root.id, 4, "after migration")
    assert_every_cursor_replays(Framework(SQLiteHistory(path)), run.id)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT event_id, content FROM messages WHERE content_digest IS NULL "
                          "ORDER BY rowid LIMIT 4").fetchall() == legacy_messages
        db.execute("PRAGMA user_version = 1")
    with pytest.raises(DomainError, match="older Swarm Lens code marked it version 1"):
        SQLiteHistory(path)


@pytest.fixture
def snapshotted(framework, branch):
    framework.ingest(branch.id, Facts(*conversation(random.Random(9), "s", 30, ["a"])), batch_size=4)
    child = framework.fork(branch.id, 30, "child")
    framework.ingest(child.id, Facts(*conversation(random.Random(10), "c", 10, ["a"])), batch_size=3)
    return framework


def test_a_missing_base_snapshot_falls_back_to_replay(snapshotted, branch):
    child = next(b for b in snapshotted.store.branches(branch.run_id) if b.parent_id)
    with sqlite3.connect(snapshotted.store.path) as db:
        base = db.execute("SELECT base_branch, base_cursor FROM snapshots WHERE branch_id=? AND base_cursor IS NOT NULL "
                          "ORDER BY cursor DESC", (child.id,)).fetchone()
        db.execute("DELETE FROM snapshots WHERE branch_id=? AND cursor=?", base)
    with pytest.warns(RuntimeWarning, match="replaying history"):
        assert_every_cursor_replays(snapshotted, branch.run_id)

    with pytest.warns(RuntimeWarning, match="replaying history"):
        snapshotted.ingest(child.id, Facts(*conversation(random.Random(11), "n", 4, ["a"])), batch_size=2)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        snapshotted.state(child.id)


def test_saving_at_a_damaged_snapshot_replaces_it(snapshotted, branch):
    with sqlite3.connect(snapshotted.store.path) as db:
        cursor = db.execute("SELECT max(cursor) FROM snapshots WHERE branch_id=?", (branch.id,)).fetchone()[0]
        db.execute("UPDATE snapshots SET state=x'00' WHERE branch_id=? AND cursor=?", (branch.id, cursor))
    with pytest.warns(RuntimeWarning, match="replaying history"):
        repaired = snapshotted.state(branch.id, cursor)

    snapshotted.store.save_snapshot(repaired)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert encode(snapshotted.store.snapshot(branch.id, cursor).to_dict()) == encode(repaired.to_dict())
