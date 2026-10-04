"""Durable on-demand analysis jobs, one SQLite table per plugin."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3

from swarm_lens.core.models import Conflict, DomainError, utc_now


class JobStore:
    """Jobs are JSON records keyed by id and branch; `name` selects the table (`{name}_jobs`)."""

    def __init__(self, path, name, label, max_pending=4):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError("A job store name must be a lowercase identifier")
        self.path, self.table, self.label, self.max_pending = Path(path), f"{name}_jobs", label, max_pending
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.table} (id TEXT PRIMARY KEY, branch_id TEXT NOT NULL, "
                       "created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, record):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute(f"SELECT COUNT(*) FROM {self.table} WHERE status IN ('queued','running')").fetchone()[0]
            if pending >= self.max_pending:
                raise Conflict(f"{self.max_pending} {self.label} jobs are already pending; wait for one to finish")
            db.execute(f"INSERT INTO {self.table} VALUES (?,?,?,?,?)", (
                record["id"], record["branch_id"], record["created_at"], record["status"], json.dumps(record)))

    def get(self, job_id):
        with self.connection() as db:
            row = db.execute(f"SELECT record FROM {self.table} WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise DomainError(f"Unknown {self.label} job")
        return json.loads(row[0])

    def list(self, branch_id):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                f"SELECT record FROM {self.table} WHERE branch_id=? ORDER BY created_at DESC LIMIT 50", (branch_id,))]

    def update(self, record):
        with self.connection() as db:
            db.execute(f"UPDATE {self.table} SET status=?, record=? WHERE id=?", (
                record["status"], json.dumps(record), record["id"]))

    def claim(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT record FROM {self.table} WHERE id=? AND status='queued'", (job_id,)).fetchone()
            if row is None:
                return None
            record = json.loads(row[0])
            record.update(status="running", started_at=utc_now())
            db.execute(f"UPDATE {self.table} SET status='running', record=? WHERE id=?", (json.dumps(record), job_id))
            return record

    def import_records(self, records):
        """Add records in one transaction, keeping any job that already exists; safe to repeat."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany(f"INSERT OR IGNORE INTO {self.table} VALUES (?,?,?,?,?)", [
                (record["id"], record["branch_id"], record["created_at"], record["status"], json.dumps(record))
                for record in records])

    def recover_interrupted(self):
        # Single-server startup only. Never silently repeat a model call after a crash.
        with self.connection() as db:
            rows = db.execute(f"SELECT record FROM {self.table} WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                record = json.loads(row[0])
                record.update(status="interrupted", error="Server restarted before the job finished. Start a new analysis to retry.")
                db.execute(f"UPDATE {self.table} SET status='interrupted', record=? WHERE id=?", (json.dumps(record), record["id"]))


def read_jobs(path, table):
    """Every record of a job table in another database, opened read-only; none when the file does not exist."""
    if not Path(path).is_file():
        return []
    db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        return [json.loads(row[0]) for row in db.execute(f"SELECT record FROM {table}")]
    finally:
        db.close()
