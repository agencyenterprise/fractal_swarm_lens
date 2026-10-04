"""One local worker, durable status, and atomic/idempotent event ingestion."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock

from swarm_lens.core.models import Conflict, DomainError, Fact, new_id, utc_now


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class Facts:
    def __init__(self, items):
        self.items = items

    def facts(self):
        yield from self.items


class LiveService:
    def __init__(self, framework, path, runtimes=()):
        self.framework, self.path = framework, Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runtimes = {runtime.id: runtime for runtime in runtimes}
        if len(self.runtimes) != len(runtimes):
            raise ValueError("Duplicate runtime ID")
        self.lock = RLock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="swarm-execution")
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS live_sessions (id TEXT PRIMARY KEY, branch_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS execution_jobs (id TEXT PRIMARY KEY, branch_id TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        # Called once at server startup. Never repeat a provider/tool call after a crash.
        with self.db() as db:
            for row in db.execute("SELECT * FROM execution_jobs WHERE status IN ('queued','running')").fetchall():
                job = json.loads(row['record'])
                job.update(status="interrupted", finished_at=utc_now(), error="Server restarted. Fork an earlier saved event to retry.")
                db.execute("UPDATE execution_jobs SET status=?, record=? WHERE id=?", (job['status'], json.dumps(job), job['id']))
                if job.get('kind') == 'start':
                    capture = db.execute("SELECT record FROM live_sessions WHERE branch_id=?", (job['branch_id'],)).fetchone()
                    if capture:
                        record = json.loads(capture[0])
                        if record['status'] == 'capturing':
                            record.update(status='incomplete', updated_at=utc_now())
                            db.execute("UPDATE live_sessions SET record=? WHERE branch_id=?", (json.dumps(record), job['branch_id']))

    def close(self):
        self.worker.shutdown(wait=True, cancel_futures=False)

    def create_capture(self, client_id, name, metadata):
        with self.lock, self.db() as db:
            signature = digest({"name": name, "metadata": metadata})
            row = db.execute("SELECT record FROM live_sessions WHERE id=?", (client_id,)).fetchone()
            if row:
                record = json.loads(row[0])
                if record['signature'] != signature:
                    raise Conflict("Capture ID was already used with different metadata")
                return record
            branch = self.framework.create_run(name, {**metadata, "source_type": "live", "capture_id": client_id})
            record = {"id": client_id, "branch_id": branch.id, "signature": signature,
                      "status": "capturing", "created_at": utc_now(), "updated_at": utc_now()}
            db.execute("INSERT INTO live_sessions VALUES (?,?,?)", (client_id, branch.id, json.dumps(record)))
            return record

    def capture(self, branch_id):
        with self.db() as db:
            row = db.execute("SELECT record FROM live_sessions WHERE branch_id=?", (branch_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def finish_capture(self, branch_id, status, *, execution=False):
        if status not in {"completed", "failed", "incomplete"}:
            raise DomainError("Invalid capture status")
        with self.lock, self.db() as db:
            if not execution and any(job.get('kind') == 'start' for job in self.jobs(branch_id)):
                raise Conflict('The server manages this capture; it closes when execution finishes')
            record = self.capture(branch_id)
            if not record:
                raise DomainError("Unknown capture")
            if record['status'] != 'capturing' and record['status'] != status:
                raise Conflict("Capture already closed")
            record.update(status=status, updated_at=utc_now())
            db.execute("UPDATE live_sessions SET record=? WHERE branch_id=?", (json.dumps(record), branch_id))
            return record

    def append(self, branch_id, facts, expected_head, *, execution=False):
        if not facts or len(facts) > 500:
            raise DomainError("Submit between 1 and 500 facts")
        if len({fact.id for fact in facts}) != len(facts):
            raise DomainError("Duplicate source IDs in batch")
        with self.lock:
            if not execution:
                capture = self.capture(branch_id)
                if not capture:
                    raise DomainError("This branch is not an external capture")
                if any(job.get('kind') == 'start' for job in self.jobs(branch_id)):
                    raise Conflict('Only the registered application can append to this capture')
            branch = self.framework.store.branch(branch_id)
            signed = []
            for fact in facts:
                raw = asdict(fact)
                if fact.source.get('origin') == 'intervention':
                    raise DomainError("Capture cannot impersonate an intervention")
                signed.append(Fact(fact.kind, fact.data, fact.occurred_at,
                                   {**fact.source, "live_fact_digest": digest(raw)}, fact.id))
            if branch.head != expected_head:
                existing = self.framework.store.events(branch_id, min(branch.head, expected_head + len(signed)), after=expected_head)
                if len(existing) == len(signed) and all(a.id == b.id and a.source.get('live_fact_digest') == b.source['live_fact_digest'] for a,b in zip(existing,signed)):
                    return {"head": expected_head + len(signed), "duplicate": True}
                raise Conflict("Capture cursor is stale or a source ID was reused with different content")
            if not execution and capture['status'] != 'capturing':
                raise Conflict("Capture is closed")
            # The entire batch commits atomically. IDs + digest make lost acknowledgements retryable.
            self.framework.ingest(branch_id, Facts(signed), batch_size=len(signed), expected_head=expected_head)
            return {"head": expected_head + len(signed), "duplicate": False}

    def jobs(self, branch_id):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT record FROM execution_jobs WHERE branch_id=? ORDER BY rowid DESC LIMIT 20", (branch_id,))]

    def job(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT record FROM execution_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise DomainError("Unknown execution")
        return json.loads(row[0])

    def _save_job(self, job):
        with self.db() as db:
            db.execute("UPDATE execution_jobs SET status=?, record=? WHERE id=?", (job['status'], json.dumps(job), job['id']))

    def apps(self):
        return [info for runtime in self.runtimes.values()
                if callable(getattr(runtime, 'launch_info', None)) and (info := runtime.launch_info())]

    def submit_start(self, runtime_id, request_id):
        with self.lock:
            with self.db() as db:
                existing = db.execute('SELECT record FROM execution_jobs WHERE id=?', (request_id,)).fetchone()
                if existing:
                    job = json.loads(existing[0])
                    if job.get('kind') != 'start' or job['runtime_id'] != runtime_id:
                        raise Conflict('Launch request ID was already used for another execution')
                    return job
                if db.execute("SELECT COUNT(*) FROM execution_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 4:
                    raise Conflict('Four executions are already pending')
            info = next((app for app in self.apps() if app['id'] == runtime_id), None)
            if not info:
                raise DomainError('This application is not registered for launching')
            if not info['ready']:
                raise DomainError(info['reason'])
            capture = self.create_capture('launch-'+request_id, info['title'],
                                          {key: info[key] for key in ('framework', 'framework_version', 'revision')} | {'runtime_id': runtime_id})
            job = {'id': request_id, 'kind': 'start', 'branch_id': capture['branch_id'], 'input_cursor': 0,
                   'runtime_id': runtime_id, 'status': 'queued', 'created_at': utc_now()}
            with self.db() as db:
                db.execute('INSERT INTO execution_jobs VALUES (?,?,?,?)', (job['id'], job['branch_id'], job['status'], json.dumps(job)))
            self.worker.submit(self.execute, job['id'])
            return job

    def assert_idle(self, branch_id):
        if any(job['status'] in {'queued','running'} for job in self.jobs(branch_id)):
            raise Conflict("This branch is executing. Fork a saved checkpoint to make changes.")
        capture = self.capture(branch_id)
        if capture and capture['status'] == 'capturing':
            raise Conflict("This branch is collecting live events. Fork to make changes.")

    def runtime_for(self, state):
        metadata = state.environment.metadata
        config = metadata.get('trace_continuation') or metadata.get('crewai', {})
        return (self.runtimes.get(config.get('runtime_id')) or
                next((r for r in self.runtimes.values() if getattr(r, 'reconstructs_traces', False)), None))

    def validate_runtime(self, runtime, state, cursor, extra_event=None):
        if getattr(runtime, 'reconstructs_traces', False):
            # Preview state may include an unsaved edit; never load a later parent event for it.
            history = self.framework.history(state.branch_id, cursor)
            runtime.validate(state, history=history)
        else:
            runtime.validate(state)
            self.validate_interventions(state.branch_id, cursor, state=state, extra_event=extra_event)

    def describe(self, branch_id, cursor, intervention=None):
        state = self.framework.state(branch_id, cursor)
        runtime = self.runtime_for(state)
        result = {"can_execute": False, "reason": "This trace has no registered CrewAI task checkpoint.",
                  "checkpoints": [], "jobs": self.jobs(branch_id), "capture": self.capture(branch_id)}
        if runtime:
            extra_event = None
            if intervention:
                try:
                    extra_event = self.framework.prepare_intervention(state, **intervention)
                except DomainError as exc:
                    return {**result, 'reason': str(exc)}
            if getattr(runtime, 'reconstructs_traces', False):
                result.update(runtime.describe(state, history=self.framework.history(branch_id, cursor)))
            else:
                result.update(runtime.describe(state))
            if result['can_execute']:
                try:
                    self.validate_runtime(runtime, state, cursor, extra_event)
                except DomainError as exc:
                    result.update(can_execute=False, reason=str(exc))
        for event in self.framework.history(branch_id, cursor):
            cp = event.data.get('metadata', {}).get('crewai', {}) if event.kind == 'environment.updated' else {}
            if runtime and event.source.get('origin') == 'crewai' and cp.get('phase') == 'boundary' and not cp.get('replay_reason') and cp.get('revision') == runtime.revision:
                result['checkpoints'].append({"cursor": event.position, "next_task": cp['next_task'], "total_tasks": cp['total_tasks']})
        return result

    def validate_interventions(self, branch_id, cursor, *, state=None, extra_event=None):
        history = self.framework.history(branch_id, cursor)
        if extra_event:
            history.append(extra_event)
        checkpoints = [e for e in history if e.kind == 'environment.updated' and e.source.get('origin') == 'crewai'
                       and e.data.get('metadata', {}).get('crewai', {}).get('phase') == 'boundary']
        if not checkpoints:
            raise DomainError('No recorded CrewAI checkpoint is present.')
        checkpoint = checkpoints[-1]
        # Native running-phase updates are valid saved state too. Compare against
        # the latest native metadata, so an intervention cannot forge a boundary.
        native = [e for e in history if e.kind == 'environment.updated' and e.source.get('origin') == 'crewai'
                  and e.data.get('metadata', {}).get('crewai')]
        base = self.framework.state(branch_id, native[-1].position)
        state = state or self.framework.state(branch_id, cursor)
        if state.environment.metadata.get('crewai') != base.environment.metadata.get('crewai'):
            raise DomainError('The runtime checkpoint cannot be edited. Fork an original task checkpoint.')
        if state.environment.task != base.environment.task:
            raise DomainError('Change the shared goal to guide this branch; changing the task definition requires a crew factory.')
        for e in history:
            if e.position <= checkpoint.position or e.source.get('origin') != 'intervention':
                continue
            before = self.framework.state(branch_id, e.position - 1)
            if e.kind == 'memory.written':
                memory = before.memories.get(e.data['id'])
                if not memory or memory.metadata.get('type') != 'task_output':
                    raise DomainError('Only completed task-output memory can be edited for this runtime.')
                if {k:v for k,v in e.data.items() if k != 'content'} != {k:v for k,v in asdict(memory).items() if k != 'content'}:
                    raise DomainError('Task-output memory metadata and ownership cannot change.')
            if e.kind == 'agent.updated' and e.data.get('metadata') != asdict(before.agents[e.data['id']])['metadata']:
                raise DomainError('Edit the agent name, prompt, or model; other agent configuration belongs to the crew factory.')

    def submit(self, branch_id, expected_head, steps):
        if not 1 <= steps <= 100:
            raise DomainError("Execute between 1 and 100 tasks")
        with self.lock:
            branch = self.framework.store.branch(branch_id)
            if not branch.parent_id:
                raise DomainError("Fork the recorded conversation before generating a continuation")
            if branch.head != expected_head:
                raise Conflict("Branch changed; refresh before executing")
            self.assert_idle(branch_id)
            state = self.framework.state(branch_id)
            runtime = self.runtime_for(state)
            if not runtime:
                raise DomainError("No compatible CrewAI runtime is registered for this trace")
            self.validate_runtime(runtime, state, state.cursor)
            with self.db() as db:
                if db.execute("SELECT COUNT(*) FROM execution_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 4:
                    raise Conflict("Four executions are already pending")
                job = {"id": new_id(), "branch_id": branch_id, "input_cursor": expected_head, "steps": steps,
                       "runtime_id": runtime.id, "status": "queued", "created_at": utc_now()}
                db.execute("INSERT INTO execution_jobs VALUES (?,?,?,?)", (job['id'], branch_id, job['status'], json.dumps(job)))
            self.worker.submit(self.execute, job['id'])
            return job

    def submit_fork(self, parent_id, cursor, name, steps, request_id, intervention=None):
        """Validate the saved state, then create a child in the same conversation."""
        signature = digest(dict(parent_id=parent_id, cursor=cursor, name=name, steps=steps, intervention=intervention))
        with self.lock:
            with self.db() as db:
                existing = db.execute('SELECT record FROM execution_jobs WHERE id=?', (request_id,)).fetchone()
                if existing:
                    job = json.loads(existing[0])
                    if job.get('request_signature') != signature:
                        raise Conflict('This request ID was already used for a different execution')
                    return job
                if db.execute("SELECT COUNT(*) FROM execution_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 4:
                    raise Conflict('Four executions are already pending')
            if not 1 <= steps <= 100:
                raise DomainError('Execute between 1 and 100 tasks')
            if not name.strip() or len(name) > 160:
                raise DomainError('Provide a branch name of 1–160 characters')
            state = self.framework.state(parent_id, cursor)
            runtime = self.runtime_for(state)
            if not runtime:
                raise DomainError('No compatible CrewAI runtime is registered for this trace')
            extra_event = self.framework.prepare_intervention(state, **intervention) if intervention else None
            self.validate_runtime(runtime, state, cursor, extra_event)
            branch = self.framework.fork_with_intervention(parent_id, cursor, name, intervention)
            job = dict(id=request_id, kind='fork', branch_id=branch.id, parent_id=parent_id,
                       branch_name=branch.name, input_cursor=branch.head, fork_cursor=cursor, steps=steps, runtime_id=runtime.id,
                       request_signature=signature, status='queued', created_at=utc_now())
            with self.db() as db:
                db.execute('INSERT INTO execution_jobs VALUES (?,?,?,?)',
                           (job['id'], branch.id, job['status'], json.dumps(job)))
            self.worker.submit(self.execute, job['id'])
            return job

    def execute(self, job_id):
        with self.lock:
            job = self.job(job_id)
            if job['status'] != 'queued':
                return
            job.update(status='running', started_at=utc_now())
            self._save_job(job)
        try:
            state = self.framework.state(job['branch_id'])
            if state.cursor != job['input_cursor']:
                raise Conflict("Branch changed before execution")
            head = state.cursor
            def emit(facts):
                nonlocal head
                head = self.append(job['branch_id'], facts, head, execution=True)['head']
            runtime = self.runtimes[job['runtime_id']]
            if job.get('kind') == 'start':
                runtime.start(emit)
            else:
                runtime.execute(state, job['steps'], emit)
            job.update(status='completed', output_cursor=head)
        except Exception as exc:
            # Provider exception strings can contain credentials or full prompts.
            job.update(status='failed', error_type=type(exc).__name__,
                       error="Execution failed. Recorded events are preserved; fork an earlier saved event to retry.")
        job['finished_at'] = utc_now()
        if job.get('kind') == 'start':
            self.finish_capture(job['branch_id'], job['status'], execution=True)
        self._save_job(job)
