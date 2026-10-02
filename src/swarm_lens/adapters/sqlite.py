import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from swarm_lens.core.models import Branch, Conflict, DomainError, Event, Run, State


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class SQLiteHistory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise DomainError(f"Unsupported database schema version: {version}")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(Path(__file__).with_name("schema.sql").read_text())

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_run(self, run: Run, branch: Branch) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run.id, run.name, run.created_at, encode(run.metadata)))
            self._insert_branch(db, branch)

    @staticmethod
    def _insert_branch(db, branch):
        db.execute("INSERT INTO branches VALUES (?,?,?,?,?,?,?)", tuple(asdict(branch).values()))

    def runs(self) -> list[Run]:
        with self.connection() as db:
            return [Run(row["id"], row["name"], row["created_at"], json.loads(row["metadata"]))
                    for row in db.execute("SELECT * FROM runs ORDER BY created_at")]

    def run(self, run_id: str) -> Run:
        return next((run for run in self.runs() if run.id == run_id), None) or self._missing("run")

    @staticmethod
    def _missing(entity):
        raise DomainError(f"Unknown {entity}")

    def branch(self, branch_id: str) -> Branch:
        with self.connection() as db:
            row = db.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
            return Branch(**dict(row)) if row else self._missing("branch")

    def branches(self, run_id: str) -> list[Branch]:
        with self.connection() as db:
            return [Branch(**dict(row)) for row in db.execute(
                "SELECT * FROM branches WHERE run_id=? ORDER BY created_at, id", (run_id,))]

    def fork(self, branch: Branch) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute("SELECT * FROM branches WHERE id=?", (branch.parent_id,)).fetchone()
            if not parent or parent["run_id"] != branch.run_id or not 0 <= branch.fork_position <= parent["head"]:
                raise DomainError("Invalid fork position or parent")
            self._insert_branch(db, branch)

    def events(self, branch_id: str, cursor: int | None = None, after: int = 0) -> list[Event]:
        branch = self.branch(branch_id)
        cursor = branch.head if cursor is None else cursor
        if not 0 <= cursor <= branch.head:
            raise DomainError("Cursor is outside branch history")
        segments = []
        with self.connection() as db:
            while True:
                rows = db.execute("SELECT * FROM events WHERE branch_id=? AND position<=? AND position>? ORDER BY position",
                                  (branch.id, cursor, after)).fetchall()
                segments.append([self._event(row) for row in rows])
                if not branch.parent_id or branch.fork_position <= after:
                    break
                cursor = min(cursor, branch.fork_position)
                branch = Branch(**dict(db.execute("SELECT * FROM branches WHERE id=?", (branch.parent_id,)).fetchone()))
        return [event for segment in reversed(segments) for event in segment]

    @staticmethod
    def _event(row):
        data = dict(row)
        data["data"], data["source"] = json.loads(data["data"]), json.loads(data["source"])
        return Event(**data)

    def append(self, branch_id: str, events: list[Event], expected_head: int) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            branch = db.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
            if not branch:
                raise DomainError("Unknown branch")
            if branch["head"] != expected_head:
                raise Conflict("Branch changed; refresh the cursor before applying changes")
            for offset, event in enumerate(events, 1):
                if event.branch_id != branch_id or event.position != expected_head + offset:
                    raise DomainError("Invalid event sequence")
                try:
                    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", (
                        event.id, event.branch_id, event.position, event.kind, encode(event.data),
                        event.occurred_at, event.recorded_at, encode(event.source), event.schema_version))
                    self._index(db, branch["run_id"], event)
                except sqlite3.IntegrityError as exc:
                    raise Conflict("Duplicate event or invalid entity reference") from exc
            db.execute("UPDATE branches SET head=? WHERE id=?", (expected_head + len(events), branch_id))

    @staticmethod
    def _index(db, run_id, event):
        d, kind = event.data, event.kind
        if kind.startswith("agent."):
            db.execute("INSERT OR IGNORE INTO agents VALUES (?,?)", (run_id, d["id"]))
            db.execute("INSERT INTO agent_revisions VALUES (?,?,?,?,?,?,?)", (
                event.id, run_id, d["id"], d["name"], d["model"], d["system_prompt"], d["active"]))
        elif kind.startswith("channel."):
            db.execute("INSERT OR IGNORE INTO channels VALUES (?,?)", (run_id, d["id"]))
            db.execute("INSERT INTO channel_revisions VALUES (?,?,?,?)", (event.id, run_id, d["id"], d["name"]))
            db.executemany("INSERT INTO channel_members VALUES (?,?,?)", [(event.id, run_id, aid) for aid in d["members"]])
        elif kind == "message.created":
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)", (
                event.id, d["id"], run_id, d["channel_id"], d["sender_id"], d["sender_name"], d["role"], d["content"], d["reply_to_id"]))
        elif kind == "memory.written":
            db.execute("INSERT INTO memory_revisions VALUES (?,?,?,?,?,?)", (
                event.id, d["id"], run_id, d["owner_id"], d["scope"], d["content"]))
        elif kind.startswith("tool."):
            db.execute("INSERT INTO tool_revisions VALUES (?,?,?,?,?,?,?,?,?)", (
                event.id, d["id"], run_id, d["agent_id"], d["tool_name"], encode(d["arguments"]),
                encode(d["result"]), d["error"], d["status"]))
        elif kind == "environment.updated":
            db.execute("INSERT INTO environment_revisions VALUES (?,?,?)", (event.id, d["task"], d["goal"]))

    def save_snapshot(self, state: State) -> None:
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (state.branch_id, state.cursor, encode(state.to_dict())))

    def snapshot(self, branch_id: str, cursor: int) -> State | None:
        with self.connection() as db:
            while True:
                row = db.execute("SELECT state FROM snapshots WHERE branch_id=? AND cursor<=? ORDER BY cursor DESC LIMIT 1",
                                 (branch_id, cursor)).fetchone()
                if row:
                    return State.from_dict(json.loads(row[0]))
                branch = db.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
                if not branch or not branch["parent_id"]:
                    return None
                branch_id, cursor = branch["parent_id"], min(cursor, branch["fork_position"])

    def save_analysis(self, record: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO analyses VALUES (?,?,?,?)", (record["id"], record["branch_id"], record["cursor"], encode(record)))

    def analyses(self, branch_id: str, cursor: int) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT record FROM analyses WHERE branch_id=? AND cursor=?", (branch_id, cursor))]
