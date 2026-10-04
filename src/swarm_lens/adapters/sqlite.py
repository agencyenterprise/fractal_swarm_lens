from collections.abc import Iterable
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from swarm_lens.application.ports import RunContents
from swarm_lens.core.models import Branch, Comment, Conflict, DomainError, Event, Run, State
from . import snapshots, texts

SCHEMA = Path(__file__).with_name("schema.sql").read_text()
SCHEMA_VERSION = 2
V1_TEXT_TABLES = {"messages": "event_id, message_id, run_id, channel_id, sender_id, sender_name, role, content, "
                              "reply_to_id",
                  "memory_revisions": "event_id, memory_id, run_id, owner_id, scope, content"}
V1_SNAPSHOT_COLUMNS = ("base_branch TEXT", "base_cursor INTEGER", "chain_entities INTEGER NOT NULL DEFAULT 0",
                       "full_entities INTEGER NOT NULL DEFAULT 0", "format INTEGER NOT NULL DEFAULT 1")


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _content_columns(data, refs):
    """The (content, content_digest) projection columns: exactly one holds a value."""
    return (None, data["content"]) if "content" in refs else (data["content"], None)


def _statements(script):
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            yield statement
            statement = ""


def _create_schema(db):
    """Create the current schema, migrating a version 1 workspace in the same transaction."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise DomainError(f"Unsupported database schema version: {version}")
    if version == 1 and db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='texts'").fetchone():
        raise DomainError("This workspace was upgraded to storage version 2, but a process running older Swarm Lens "
                          "code marked it version 1 again. Stop every process that uses it, then run "
                          "'PRAGMA user_version = 2' on it; the upgrade had already finished.")
    if version == 1:
        _set_aside_v1(db)
    for statement in _statements(SCHEMA):
        db.execute(statement)
    if version == 1:
        _restore_v1_text_tables(db)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _set_aside_v1(db):
    """Version 1 kept every text inline: add the new columns and move aside the tables whose text becomes optional."""
    db.execute("ALTER TABLE events ADD COLUMN text_refs TEXT")
    for column in V1_SNAPSHOT_COLUMNS:
        db.execute(f"ALTER TABLE snapshots ADD COLUMN {column}")
    for table in V1_TEXT_TABLES:
        db.execute(f"ALTER TABLE {table} RENAME TO {table}_v1")


def _restore_v1_text_tables(db):
    for table, columns in V1_TEXT_TABLES.items():
        db.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}_v1")
        db.execute(f"DROP TABLE {table}_v1")


class SQLiteHistory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            _create_schema(db)
            db.commit()
            db.execute("PRAGMA journal_mode=WAL")

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
            self._insert_run(db, run)
            self._insert_branch(db, branch)

    @staticmethod
    def _insert_run(db, run):
        db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run.id, run.name, run.created_at, encode(run.metadata)))

    @staticmethod
    def _insert_branch(db, branch):
        db.execute("INSERT INTO branches VALUES (?,?,?,?,?,?,?)", tuple(asdict(branch).values()))

    def runs(self) -> list[Run]:
        with self.connection() as db:
            return [self._run(row) for row in db.execute("SELECT * FROM runs ORDER BY created_at")]

    @staticmethod
    def _run(row):
        return Run(row["id"], row["name"], row["created_at"], json.loads(row["metadata"]))

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
                segments.append(db.execute("SELECT * FROM events WHERE branch_id=? AND position<=? AND position>? "
                                           "ORDER BY position", (branch.id, cursor, after)).fetchall())
                if not branch.parent_id or branch.fork_position <= after:
                    break
                cursor = min(cursor, branch.fork_position)
                branch = Branch(**dict(db.execute("SELECT * FROM branches WHERE id=?", (branch.parent_id,)).fetchone()))
            return self._events(db, [row for segment in reversed(segments) for row in segment])

    @staticmethod
    def _events(db, rows):
        parsed = [(row, json.loads(row["data"]), json.loads(row["text_refs"] or "[]")) for row in rows]
        found = texts.load(db, (data[name] for _, data, refs in parsed for name in refs))
        return [Event(row["id"], row["branch_id"], row["position"], row["kind"], texts.unpack(data, refs, found),
                      row["occurred_at"], row["recorded_at"], json.loads(row["source"]), row["schema_version"])
                for row, data, refs in parsed]

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
            self._insert_events(db, branch["run_id"], events)
            db.execute("UPDATE branches SET head=? WHERE id=?", (expected_head + len(events), branch_id))

    @classmethod
    def _insert_events(cls, db, run_id, events):
        for event in events:
            data, refs = texts.pack(db, event.data)
            try:
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    event.id, event.branch_id, event.position, event.kind, encode(data),
                    event.occurred_at, event.recorded_at, encode(event.source), event.schema_version,
                    encode(refs) if refs else None))
                cls._index(db, run_id, event, data, refs)
            except sqlite3.IntegrityError as exc:
                raise Conflict("Duplicate event or invalid entity reference") from exc

    @staticmethod
    def _index(db, run_id, event, d, refs):
        """Project one event; `d` is its stored data, whose fields named in `refs` hold text digests."""
        kind = event.kind
        if kind.startswith("agent."):
            db.execute("INSERT OR IGNORE INTO agents VALUES (?,?)", (run_id, d["id"]))
            db.execute("INSERT INTO agent_revisions VALUES (?,?,?,?,?,?,?)", (
                event.id, run_id, d["id"], d["name"], d["model"], d["system_prompt"], d["active"]))
        elif kind.startswith("channel."):
            db.execute("INSERT OR IGNORE INTO channels VALUES (?,?)", (run_id, d["id"]))
            db.execute("INSERT INTO channel_revisions VALUES (?,?,?,?)", (event.id, run_id, d["id"], d["name"]))
            db.executemany("INSERT INTO channel_members VALUES (?,?,?)", [(event.id, run_id, aid) for aid in d["members"]])
        elif kind == "message.created":
            db.execute("INSERT INTO messages (event_id, message_id, run_id, channel_id, sender_id, sender_name, role, "
                       "content, content_digest, reply_to_id) VALUES (?,?,?,?,?,?,?,?,?,?)", (
                event.id, d["id"], run_id, d["channel_id"], d["sender_id"], d["sender_name"], d["role"],
                *_content_columns(d, refs), d["reply_to_id"]))
        elif kind == "memory.written":
            db.execute("INSERT INTO memory_revisions (event_id, memory_id, run_id, owner_id, scope, content, "
                       "content_digest) VALUES (?,?,?,?,?,?,?)", (
                event.id, d["id"], run_id, d["owner_id"], d["scope"], *_content_columns(d, refs)))
        elif kind.startswith("tool."):
            db.execute("INSERT INTO tool_revisions VALUES (?,?,?,?,?,?,?,?,?)", (
                event.id, d["id"], run_id, d["agent_id"], d["tool_name"], encode(d["arguments"]),
                encode(d["result"]), d["error"], d["status"]))
        elif kind == "environment.updated":
            db.execute("INSERT INTO environment_revisions VALUES (?,?,?)", (event.id, d["task"], d["goal"]))

    def save_snapshot(self, state: State) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshots.save(db, state)

    def snapshot(self, branch_id: str, cursor: int) -> State | None:
        with self.connection() as db:
            return snapshots.load(db, branch_id, cursor)

    def save_analysis(self, record: dict) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO analyses VALUES (?,?,?,?)", (record["id"], record["branch_id"], record["cursor"], encode(record)))

    def analyses(self, branch_id: str, cursor: int) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT record FROM analyses WHERE branch_id=? AND cursor=?", (branch_id, cursor))]

    def add_comment(self, comment: Comment) -> None:
        with self.connection() as db:
            self._insert_comment(db, comment)

    @staticmethod
    def _insert_comment(db, comment):
        db.execute("INSERT INTO comments VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(asdict(comment).values()))

    def comment(self, comment_id: str) -> Comment:
        with self.connection() as db:
            row = db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
            return self._comment(row) if row else self._missing("comment")

    @staticmethod
    def _comment(row):
        return Comment(**{**dict(row), "resolved": bool(row["resolved"])})

    def comments(self, branch_id: str) -> list[Comment]:
        branch = self.branch(branch_id)
        limit, found = branch.head, []
        with self.connection() as db:
            while True:
                found += [self._comment(row) for row in db.execute(
                    "SELECT * FROM comments WHERE branch_id=? AND position<=?", (branch.id, limit))]
                if not branch.parent_id:
                    break
                limit = min(limit, branch.fork_position)
                branch = Branch(**dict(db.execute("SELECT * FROM branches WHERE id=?", (branch.parent_id,)).fetchone()))
        return sorted(found, key=lambda comment: (comment.position, comment.created_at, comment.id))

    def update_comment(self, comment: Comment) -> None:
        with self.connection() as db:
            changed = db.execute("UPDATE comments SET text=?, resolved=?, updated_at=? WHERE id=?",
                                 (comment.text, comment.resolved, comment.updated_at, comment.id)).rowcount
            if not changed:
                self._missing("comment")

    def delete_comment(self, comment_id: str) -> None:
        with self.connection() as db:
            if not db.execute("DELETE FROM comments WHERE id=?", (comment_id,)).rowcount:
                self._missing("comment")

    def run_contents(self, run_id: str) -> RunContents:
        with self.connection() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone() or self._missing("run")
            branches = [Branch(**dict(row)) for row in db.execute(
                "SELECT * FROM branches WHERE run_id=? ORDER BY created_at, id", (run_id,))]
            own_events = {branch.id: self._events(db, db.execute(
                "SELECT * FROM events WHERE branch_id=? ORDER BY position", (branch.id,)).fetchall())
                for branch in branches}
            comments = [self._comment(comment) for comment in db.execute(
                "SELECT comments.* FROM comments JOIN branches ON branches.id=comments.branch_id "
                "WHERE branches.run_id=?", (run_id,))]
            return RunContents(self._run(row), branches, own_events, comments)

    def import_run(self, run: Run, branches: list[Branch], batches: Iterable[tuple[list[Event], State]],
                   comments: list[Comment]) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert_run(db, run)
            for branch in branches:
                self._insert_branch(db, branch)
            for events, state in batches:
                self._insert_events(db, run.id, events)
                snapshots.save(db, state)
            for comment in comments:
                self._insert_comment(db, comment)
