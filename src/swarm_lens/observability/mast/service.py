"""Durable on-demand jobs; the input is frozen before scheduling the judge."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3

from swarm_lens.core.models import Conflict, DomainError, new_id, utc_now


class MastJobs:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS mast_jobs (id TEXT PRIMARY KEY, branch_id TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)")

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
            pending = db.execute("SELECT COUNT(*) FROM mast_jobs WHERE status IN ('queued','running')").fetchone()[0]
            if pending >= 4:
                raise Conflict("Four MAST jobs are already pending; wait for one to finish")
            db.execute("INSERT INTO mast_jobs VALUES (?,?,?,?,?)", (
                record["id"], record["branch_id"], record["created_at"], record["status"], json.dumps(record)))

    def get(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT record FROM mast_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise DomainError("Unknown MAST job")
        return json.loads(row[0])

    def list(self, branch_id):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT record FROM mast_jobs WHERE branch_id=? ORDER BY created_at DESC LIMIT 50", (branch_id,))]

    def update(self, record):
        with self.connection() as db:
            db.execute("UPDATE mast_jobs SET status=?, record=? WHERE id=?", (
                record["status"], json.dumps(record), record["id"]))

    def claim(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record FROM mast_jobs WHERE id=? AND status='queued'", (job_id,)).fetchone()
            if row is None:
                return None
            record = json.loads(row[0])
            record.update(status="running", started_at=utc_now())
            db.execute("UPDATE mast_jobs SET status='running', record=? WHERE id=?", (json.dumps(record), job_id))
            return record

    def recover_interrupted(self):
        # Single-server startup only. Never silently repeat a model call after a crash.
        with self.connection() as db:
            rows = db.execute("SELECT record FROM mast_jobs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                record = json.loads(row[0])
                record.update(status="interrupted", error="Server restarted before the job finished. Start a new analysis to retry.")
                db.execute("UPDATE mast_jobs SET status='interrupted', record=? WHERE id=?", (json.dumps(record), record["id"]))


class MastService:
    def __init__(self, framework, plugin, jobs, artifacts):
        self.framework, self.plugin, self.jobs, self.artifacts = framework, plugin, jobs, artifacts

    def preview(self, branch_id, cursor, completeness):
        history = self.framework.history(branch_id, cursor)
        _, _, summary = self.plugin.prepare_input(history, {"completeness": completeness})
        description = self.plugin.judge.describe()
        if not description["ready"]:
            summary.update(can_analyze=False, reason=description["reason"])
        return {**summary, "branch_id": branch_id, "cursor": cursor, "judge": description}

    def submit(self, branch_id, cursor, completeness):
        description = self.plugin.judge.describe()
        if not description["ready"]:
            raise DomainError(description["reason"])
        history = self.framework.history(branch_id, cursor)
        config = {"completeness": completeness}
        trace, prompt, summary = self.plugin.prepare_input(history, config)
        if not summary["can_analyze"]:
            raise DomainError(summary["reason"])
        input_digest = hashlib.sha256(json.dumps([asdict(e) for e in history], sort_keys=True).encode()).hexdigest()
        record = {"id": new_id(), "plugin_id": self.plugin.id, "plugin_version": self.plugin.version,
                  "branch_id": branch_id, "cursor": cursor, "created_at": utc_now(), "status": "queued",
                  "config": config, "input_digest": input_digest, "judge": description, "input_summary": summary,
                  "trace_artifact": self.artifacts.put(json.dumps({"text": trace}, ensure_ascii=False).encode()),
                  "prompt_artifact": self.artifacts.put(json.dumps({"text": prompt}, ensure_ascii=False).encode())}
        self.jobs.create(record)
        return record

    def execute(self, job_id):
        record = self.jobs.claim(job_id)
        if record is None:
            return
        try:
            trace = json.loads(self.artifacts.get(record["trace_artifact"]))["text"]
            prompt = json.loads(self.artifacts.get(record["prompt_artifact"]))["text"]
            output = self.plugin.evaluate(trace, prompt)
            output["input_summary"] = record.get("input_summary")
            analysis = {key: record[key] for key in (
                "id", "plugin_id", "plugin_version", "branch_id", "cursor", "created_at", "config", "input_digest",
            )}
            analysis["output"] = output
            self.framework.store.save_analysis(analysis)
            record.update(status="completed" if output["parse_status"] == "complete" else "needs_review",
                          analysis=analysis)
        except Exception as exc:
            # Provider errors can contain request details; never echo them into the UI.
            kind = type(exc).__name__
            messages = {
                "AuthenticationError": "The provider rejected the server credentials.",
                "RateLimitError": "The provider rate or quota limit was reached.",
                "BadRequestError": "The provider rejected the model or input. Check the configured model and trace size.",
                "APITimeoutError": "The model request timed out.",
            }
            record.update(status="failed", error=messages.get(kind, "MAST analysis failed; no negative classifications were inferred."),
                          error_type=kind)
        record["finished_at"] = utc_now()
        self.jobs.update(record)
