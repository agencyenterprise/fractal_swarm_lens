from collections import defaultdict
from collections.abc import Iterator
import hashlib
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any

from swarm_lens.core.models import (Branch, Comment, Conflict, DomainError, Event, Fact, Run, State, comment_author,
                                    comment_text, new_id, utc_now)
from swarm_lens.core.reducer import apply, compare
from .bundle import RunBundle, export_bundle, parse_bundle
from .ports import HistoryStore, Runtime, Source, VersionStore


IMPORT_SNAPSHOT_INTERVAL = 100


class IterableSource:
    """A source over facts the caller already has; it reads them lazily, once."""

    def __init__(self, facts):
        self._facts = facts

    def facts(self):
        yield from self._facts


class Framework:
    def __init__(self, store: HistoryStore, *, versions: VersionStore | None = None,
                 runtime: Runtime | None = None):
        self.store, self.versions, self.runtime = store, versions, runtime

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
        branch = self.create_run(name, {"source_type": "saved_trace", "sha256": digest})
        self.ingest(branch.id, IterableSource(facts))
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

    def fork_with_facts(self, branch_id: str, cursor: int, name: str, facts: list[Fact]) -> Branch:
        """Fork at `cursor` and append `facts`, after checking them against a private copy of the fork's state."""
        state = self.state(branch_id, cursor)
        for fact in facts:
            self._event(state, fact)
        branch = self.fork(branch_id, cursor, name)
        self.ingest(branch.id, IterableSource(facts), expected_head=cursor)
        return self.store.branch(branch.id)

    def diff(self, left: str, right: str, left_cursor: int | None = None,
             right_cursor: int | None = None) -> dict:
        if self.store.branch(left).run_id != self.store.branch(right).run_id:
            raise DomainError("Compare branches from the same run")
        return compare(self.state(left, left_cursor), self.state(right, right_cursor))

    def continue_run(self, branch_id: str, steps: int) -> int:
        if not self.runtime or not self.runtime.capabilities().get("continue", False):
            raise DomainError("This application has no execution runtime. Register a Runtime to generate a continuation.")
        if not 1 <= steps <= 1000:
            raise DomainError("Steps must be between 1 and 1000")
        state = self.state(branch_id)
        facts = self.runtime.continue_from(state, steps)
        return self.ingest(branch_id, IterableSource(facts), expected_head=state.cursor)

    def comments(self, branch_id: str) -> list[Comment]:
        return self.store.comments(branch_id)

    def comment(self, branch_id: str, event_id: str, author: str, text: str,
                parent_id: str | None = None) -> Comment:
        if parent_id is None:
            position = self._anchor_position(branch_id, event_id)
        else:
            thread = self._visible_thread(branch_id, parent_id)
            if thread.event_id != event_id:
                raise DomainError("A reply anchors to its thread's event")
            position = thread.position
        comment = Comment(new_id(), branch_id, event_id, position, comment_author(author), comment_text(text),
                          utc_now(), parent_id)
        self.store.add_comment(comment)
        return comment

    def _anchor_position(self, branch_id: str, event_id: str) -> int:
        position = next((event.position for event in self.history(branch_id) if event.id == event_id), None)
        if position is None:
            raise DomainError("A comment anchors to an event in this branch's history")
        return position

    def _visible_thread(self, branch_id: str, comment_id: str) -> Comment:
        thread = next((comment for comment in self.comments(branch_id) if comment.id == comment_id), None)
        if thread is None:
            raise DomainError("The comment being answered is not visible on this branch")
        if thread.parent_id is not None:
            raise DomainError("Replies answer a thread's top-level comment")
        return thread

    def edit_comment(self, comment_id: str, *, text: str | None = None, resolved: bool | None = None) -> Comment:
        if text is None and resolved is None:
            raise DomainError("An edit changes the text, the resolved flag, or both")
        comment = self.store.comment(comment_id)
        if resolved is not None and comment.parent_id is not None:
            raise DomainError("Only a thread's top-level comment can be resolved")
        edited = replace(comment, text=comment.text if text is None else comment_text(text),
                         resolved=comment.resolved if resolved is None else resolved, updated_at=utc_now())
        self.store.update_comment(edited)
        return edited

    def delete_comment(self, comment_id: str) -> None:
        self.store.delete_comment(comment_id)

    def export_run(self, run_id: str) -> dict[str, Any]:
        return export_bundle(self.store.run_contents(run_id))

    def import_run(self, raw: Any) -> Branch:
        """Recreate a bundled run as a new run in one store transaction, so a failure leaves nothing behind."""
        bundle = parse_bundle(raw)
        now = utc_now()
        run = Run(new_id(), bundle.name, now, bundle.metadata)
        ids = {branch.key: new_id() for branch in bundle.branches}
        branches = [Branch(ids[branch.key], run.id, branch.name, ids.get(branch.parent_key), branch.fork_position,
                           branch.length, now) for branch in bundle.branches]
        self.store.import_run(run, branches, self._replay(bundle, ids), self._imported_comments(bundle, ids))
        return self.store.branch(branches[0].id)

    @classmethod
    def _replay(cls, bundle: RunBundle, ids: dict[str, str]) -> Iterator[tuple[list[Event], State]]:
        """Apply each branch's own events once; a child starts from a copy of its parent's state at the fork."""
        wanted = defaultdict(set)
        for child in bundle.branches[1:]:
            wanted[bundle.owner(child.parent_key, child.fork_position).key].add(child.fork_position)
        fork_states: dict[tuple[str, int], State] = {}
        for branch in bundle.branches:
            if branch.parent_key is None:
                state = State(ids[branch.key])
            else:
                base = bundle.owner(branch.parent_key, branch.fork_position)
                state = deepcopy(fork_states[base.key, branch.fork_position])
                state.branch_id = ids[branch.key]
            if state.cursor in wanted[branch.key]:
                fork_states[branch.key, state.cursor] = deepcopy(state)
            batch = []
            for fact in branch.facts:
                batch.append(cls._event(state, fact))
                if state.cursor in wanted[branch.key]:
                    fork_states[branch.key, state.cursor] = deepcopy(state)
                if len(batch) == IMPORT_SNAPSHOT_INTERVAL:
                    yield batch, state
                    batch = []
            if batch:
                yield batch, state

    @staticmethod
    def _imported_comments(bundle: RunBundle, ids: dict[str, str]) -> list[Comment]:
        comments = []
        for thread in bundle.threads:
            event_id = bundle.event_id(thread.branch_key, thread.event_position)
            top = Comment(new_id(), ids[thread.branch_key], event_id, thread.event_position, thread.author,
                          thread.text, thread.created_at, None, thread.resolved, thread.updated_at)
            comments.append(top)
            comments += [Comment(new_id(), ids[reply.branch_key], event_id, top.position, reply.author, reply.text,
                                 reply.created_at, top.id, False, reply.updated_at) for reply in thread.replies]
        return comments

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
                "git": self.versions is not None}
