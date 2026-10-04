import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any

from swarm_lens.core.models import Branch, Conflict, DomainError, Event, Fact, Run, State, new_id, utc_now
from swarm_lens.core.reducer import apply, compare
from .ports import HistoryStore, Plugin, Runtime, Source, VersionStore


class PluginContext:
    def __init__(self, framework: "Framework", branch_id: str, cursor: int):
        self._framework, self.branch_id, self.cursor = framework, branch_id, cursor

    @property
    def state(self) -> State:
        return self._framework.state(self.branch_id, self.cursor)

    def history(self, branch_id: str | None = None, cursor: int | None = None) -> list[Event]:
        return self._framework.history(branch_id or self.branch_id, self.cursor if cursor is None else cursor)

    def branches(self) -> list[Branch]:
        return self._framework.store.branches(self._framework.store.branch(self.branch_id).run_id)

    def fork(self, name: str) -> Branch:
        return self._framework.fork(self.branch_id, self.cursor, name)

    def intervene(self, branch_id: str, kind: str, data: dict, expected_head: int) -> Event:
        return self._framework.intervene(branch_id, kind, data, expected_head, actor="plugin")


class Framework:
    def __init__(self, store: HistoryStore, *, versions: VersionStore | None = None,
                 runtime: Runtime | None = None, plugins: tuple[Plugin, ...] = ()):
        self.store, self.versions, self.runtime = store, versions, runtime
        self.plugins = {plugin.id: plugin for plugin in plugins}

    def create_run(self, name: str, metadata: dict | None = None) -> Branch:
        if not name.strip():
            raise DomainError("Run name is required")
        now = utc_now()
        run = Run(new_id(), name.strip(), now, deepcopy(metadata or {}))
        branch = Branch(new_id(), run.id, "Recorded", None, 0, 0, now)
        self.store.create_run(run, branch)
        return branch

    def import_transcript(self, name: str, text: str, task: str = "") -> Branch:
        """Save a raw transcript as a new run without interpreting it; a source adapter is needed for agent lanes."""
        if not text.strip():
            raise DomainError("A transcript needs nonempty content")
        at, digest = utc_now(), hashlib.sha256(text.encode()).hexdigest()
        facts = [Fact("environment.updated", {"task": task or name}, at),
                 Fact("observation.recorded", {"type": "imported_trace", "content": text}, at,
                      {"origin": "import", "format": "raw_text", "sha256": digest,
                       "timing": "import time; original timing remains in the trace"})]

        class Transcript:
            def facts(self):
                yield from facts

        branch = self.create_run(name, {"source_type": "saved_trace", "sha256": digest})
        self.ingest(branch.id, Transcript())
        return self.store.branch(branch.id)

    def history(self, branch_id: str, cursor: int | None = None) -> list[Event]:
        return self.store.events(branch_id, cursor)

    def state(self, branch_id: str, cursor: int | None = None) -> State:
        branch = self.store.branch(branch_id)
        cursor = branch.head if cursor is None else cursor
        if not 0 <= cursor <= branch.head:
            raise DomainError("Cursor is outside branch history")
        state = self.store.snapshot(branch_id, cursor) or State(branch_id)
        state.branch_id = branch_id
        for event in self.store.events(branch_id, cursor, after=state.cursor):
            apply(state, event)
        return state

    @staticmethod
    def _event(state: State, fact: Fact) -> Event:
        event = Event(fact.id, state.branch_id, state.cursor + 1, fact.kind,
                      deepcopy(fact.data), fact.occurred_at, utc_now(), deepcopy(fact.source))
        apply(state, event)
        collections = {"agent": "agents", "channel": "channels", "message": "messages",
                       "memory": "memories", "tool": "tools"}
        family = event.kind.split(".")[0]
        if family in collections:
            data = asdict(getattr(state, collections[family])[event.data["id"]])
            event = replace(event, data=data)
        elif family == "environment":
            event = replace(event, data=asdict(state.environment))
        return event

    def ingest(self, branch_id: str, source: Source, *, batch_size: int = 100,
               expected_head: int | None = None) -> int:
        if batch_size < 1:
            raise DomainError("Batch size must be positive")
        state = self.state(branch_id)
        if expected_head is not None and state.cursor != expected_head:
            raise Conflict("Branch changed while the application was producing events")
        expected, count, batch = state.cursor, 0, []
        for fact in source.facts():
            batch.append(self._event(state, fact))
            if len(batch) >= batch_size:
                self.store.append(branch_id, batch, expected)
                self.store.save_snapshot(state)
                count += len(batch)
                expected, batch = state.cursor, []
        if batch:
            self.store.append(branch_id, batch, expected)
            self.store.save_snapshot(state)
            count += len(batch)
        return count

    def fork(self, branch_id: str, cursor: int, name: str) -> Branch:
        parent = self.store.branch(branch_id)
        if not name.strip():
            raise DomainError("Branch name is required")
        if not 0 <= cursor <= parent.head:
            raise DomainError("Fork cursor is outside branch history")
        branch = Branch(new_id(), parent.run_id, name.strip(), parent.id, cursor, cursor, utc_now())
        self.store.fork(branch)
        state = self.state(parent.id, cursor)
        state.branch_id = branch.id
        self.store.save_snapshot(state)
        return branch

    def intervene(self, branch_id: str, kind: str, data: dict, expected_head: int,
                  *, actor: str = "developer") -> Event:
        state = self.state(branch_id)
        if state.cursor != expected_head:
            raise Conflict("Cursor is historical or stale; fork here or refresh before editing")
        event = self.prepare_intervention(state, kind, data, actor=actor)
        self.store.append(branch_id, [event], expected_head)
        self.store.save_snapshot(state)
        return event

    def prepare_intervention(self, state: State, kind: str, data: dict,
                             *, actor: str = "explorer") -> Event:
        """Validate and apply a proposed change to a private state without saving it."""
        if kind not in {"agent.added", "agent.updated", "agent.removed", "environment.updated",
                        "channel.created", "channel.updated", "memory.written"}:
            raise DomainError("This event is not an intervention command")
        return self._event(state, Fact(kind, data, state.occurred_at or utc_now(), {
            "origin": "intervention", "actor": actor, "applied_to_runtime": False,
        }))

    def fork_with_intervention(self, branch_id: str, cursor: int, name: str,
                               intervention: dict | None = None) -> Branch:
        if intervention:
            self.prepare_intervention(self.state(branch_id, cursor), **intervention)
        branch = self.fork(branch_id, cursor, name)
        if intervention:
            self.intervene(branch.id, **intervention, expected_head=cursor, actor="explorer")
        return self.store.branch(branch.id)

    def diff(self, left: str, right: str, left_cursor: int | None = None,
             right_cursor: int | None = None) -> dict:
        if self.store.branch(left).run_id != self.store.branch(right).run_id:
            raise DomainError("Compare branches from the same run")
        return compare(self.state(left, left_cursor), self.state(right, right_cursor))

    def analyze(self, plugin_id: str, branch_id: str, cursor: int, config: dict | None = None) -> dict:
        if plugin_id not in self.plugins:
            raise DomainError("Unknown plugin")
        plugin = self.plugins[plugin_id]
        events = self.history(branch_id, cursor)
        digest = hashlib.sha256(json.dumps([asdict(event) for event in events], sort_keys=True).encode()).hexdigest()
        config = deepcopy(config or {})
        record = {"id": new_id(), "plugin_id": plugin.id, "plugin_version": plugin.version,
                  "branch_id": branch_id, "cursor": cursor, "config": config,
                  "input_digest": digest, "created_at": utc_now(),
                  "output": plugin.run(PluginContext(self, branch_id, cursor), config)}
        self.store.save_analysis(record)
        return record

    def continue_run(self, branch_id: str, steps: int) -> int:
        if not self.runtime or not self.runtime.capabilities().get("continue", False):
            raise DomainError("This application has no execution runtime. Register a Runtime to generate a continuation.")
        if not 1 <= steps <= 1000:
            raise DomainError("Steps must be between 1 and 1000")
        state = self.state(branch_id)
        facts = self.runtime.continue_from(state, steps)

        class Continuation:
            def facts(self):
                yield from facts

        return self.ingest(branch_id, Continuation(), expected_head=state.cursor)

    def checkpoint(self, branch_id: str, cursor: int | None = None, message: str = "Checkpoint") -> str:
        if not self.versions:
            raise DomainError("No version store registered")
        branch = self.store.branch(branch_id)
        cursor = branch.head if cursor is None else cursor
        if branch.parent_id:
            self.checkpoint(branch.parent_id, min(cursor, branch.fork_position), "Fork base")
        state = self.state(branch_id, cursor)
        return self.versions.checkpoint(self.store.run(branch.run_id), replace(branch, head=cursor),
                                       state, self.history(branch_id, cursor), message)

    def capabilities(self) -> dict[str, Any]:
        return {"runtime": self.runtime.capabilities() if self.runtime else {"continue": False},
                "git": self.versions is not None,
                "plugins": [{"id": p.id, "version": p.version} for p in self.plugins.values()]}
