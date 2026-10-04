"""The plugin contract and the service that runs plugins.

Plugins read a branch through a read-only `BranchView` and return results; this service validates and
persists them. A plugin never receives a store handle and never appends to an existing branch.
"""
from collections.abc import Callable, Iterable, Sequence
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import re
from typing import Any, Protocol, runtime_checkable

from swarm_lens.core.findings import Annotation, Metric, Report, decode_findings, encode_output
from swarm_lens.core.models import Branch, DomainError, Event, Fact, State, new_id, utc_now
from .ports import Jobs

PLUGIN_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
STREAM_BATCH = 100
FINISHED = frozenset({"completed", "needs_review"})
PROVIDER_MESSAGES = {
    "AuthenticationError": "The provider rejected the server credentials.",
    "RateLimitError": "The provider rate or quota limit was reached.",
    "BadRequestError": "The provider rejected the model or input. Check the configured model and trace size.",
    "APITimeoutError": "The model request timed out.",
}


@runtime_checkable
class Analyzer(Protocol):
    def analyze(self, view: "BranchView", start: int, end: int, params: Any) -> Iterable[Metric | Annotation | Report]: ...


@runtime_checkable
class StreamingAnalyzer(Protocol):
    def on_events(self, view: "BranchView", events: Sequence[Event], params: Any) -> Iterable[Metric | Annotation]: ...


@runtime_checkable
class Intervention(Protocol):
    def intervene(self, view: "BranchView", at: int, params: Any) -> Sequence[Fact]: ...


CAPABILITIES = {"analyzer": Analyzer, "streaming": StreamingAnalyzer, "intervention": Intervention}


def event_agent_id(event: Event) -> str | None:
    """The agent an event is about: an agent event's own ID, else its sender, actor or owner."""
    if event.kind.startswith("agent."):
        return event.data.get("id")
    return event.data.get("sender_id") or event.data.get("agent_id") or event.data.get("owner_id")


def record_failure(record: dict, exc: Exception, fallback: str, safe: tuple[type[Exception], ...] = ()) -> None:
    """Mark a job failed with a plain message for the UI; the root cause stays in error_detail.

    Messages of DomainError and of exception types listed in `safe` are written by this application and shown as
    they are. Provider messages can contain request details, so they never reach the UI.
    """
    kind = getattr(exc, "provider_error", None) or type(exc).__name__
    message = str(exc) if isinstance(exc, (DomainError, *safe)) else PROVIDER_MESSAGES.get(kind, fallback)
    record.update(status="failed", error=message, error_type=kind, error_detail=getattr(exc, "detail", None))


class BranchView:
    """A read-only window on one branch's history through `head`, plus the findings stored for it.

    It holds read functions only. Plugins are trusted code in the server process, so this is a contract, not a
    sandbox: a plugin gets everything it may use from the view and returns its results.
    """

    def __init__(self, branch_id: str, head: int, *, history: Callable[[], list[Event]],
                 state: Callable[[int], State], analyses: Callable[[], dict[str, dict]]):
        self.branch_id, self.head = branch_id, head
        self._read_history, self._read_state, self._read_analyses = history, state, analyses
        self._history: list[Event] | None = None
        self._analyses: dict[str, dict] | None = None
        self._consulted: set[str] = set()

    def state_at(self, seq: int) -> State:
        """The state after event `seq`, as a private copy."""
        if not 0 <= seq <= self.head:
            raise DomainError(f"Position {seq} is outside this view (0 to {self.head})")
        return self._read_state(seq)

    def events(self, start: int = 1, end: int | None = None, *, kind: str | None = None,
               agent_id: str | None = None) -> list[Event]:
        """Copies of events start..end inclusive; `kind` is an exact kind or a family prefix such as "message."."""
        end = self.head if end is None else end
        if not 1 <= start or end > self.head:
            raise DomainError(f"Events {start}..{end} are outside this view (1 to {self.head})")
        if self._history is None:
            self._history = self._read_history()
        return [replace(event, data=deepcopy(event.data), source=deepcopy(event.source))
                for event in self._history[start - 1:end]
                if (kind is None or event.kind == kind or (kind.endswith(".") and event.kind.startswith(kind)))
                and (agent_id is None or event_agent_id(event) == agent_id)]

    def metrics(self, *, plugin: str | None = None, name: str | None = None,
                agent_id: str | None = None) -> list[Metric]:
        """Stored metrics from analyses that read no event after this view's head."""
        return [metric for metric in self._findings(plugin)[0]
                if name in (None, metric.name) and agent_id in (None, metric.agent_id)]

    def annotations(self, *, plugin: str | None = None, start: int | None = None,
                    end: int | None = None) -> list[Annotation]:
        """Stored annotations from analyses that read no event after this view's head, overlapping start..end."""
        start, end = start or 1, self.head if end is None else end
        return [annotation for annotation in self._findings(plugin)[1]
                if annotation.seq_from <= end and annotation.seq_to >= start]

    @property
    def analyses_read(self) -> list[str]:
        """IDs of the stored analyses whose findings this view handed out; recorded as an analysis input."""
        return sorted(self._consulted)

    def _findings(self, plugin: str | None) -> tuple[list[Metric], list[Annotation]]:
        if self._analyses is None:
            self._analyses = self._read_analyses()
        records = [record for plugin_id, record in self._analyses.items() if plugin in (None, plugin_id)]
        self._consulted.update(record["id"] for record in records)
        return combine(records)


def combine(records: Iterable[dict]) -> tuple[list[Metric], list[Annotation]]:
    metrics, annotations = [], []
    for record in records:
        found = decode_findings(record["output"])
        metrics += found[0]
        annotations += found[1]
    return metrics, annotations


class PluginService:
    """Registers plugins, validates their params, runs them (inline or as jobs) and answers findings queries.

    `jobs(name, label)` returns the durable job store for one plugin. Without it, only inline analysis and
    interventions work.
    """

    def __init__(self, framework, plugins: Iterable[Any] = (), *,
                 jobs: Callable[[str, str], Jobs] | None = None):
        self.framework, self._job_factory = framework, jobs
        self._plugins: dict[str, Any] = {}
        self._jobs: dict[str, Jobs] = {}
        self._reserved: set[str] = set()
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: Any) -> None:
        plugin_id = getattr(plugin, "id", None)
        if not isinstance(plugin_id, str) or not PLUGIN_ID.fullmatch(plugin_id):
            raise ValueError(f"Plugin ID must be a lowercase letter, then letters, digits and hyphens: {plugin_id!r}")
        if plugin_id in self._plugins or plugin_id in self._reserved:
            raise ValueError(f"Duplicate plugin ID: {plugin_id}")
        if not isinstance(getattr(plugin, "version", None), str):
            raise ValueError(f"Plugin {plugin_id} needs a version string")
        if not self.capabilities(plugin):
            raise ValueError(f"Plugin {plugin_id} implements none of: {', '.join(CAPABILITIES)}")
        self._plugins[plugin_id] = plugin
        if self._job_factory and self._runs(plugin):
            self.jobs(plugin_id)

    def jobs(self, plugin_id: str, label: str | None = None) -> Jobs:
        """The job store of a plugin; web plugins with their own job flow (MAST) take theirs from here too.

        Asking for the store of an unregistered ID reserves that ID, so no method plugin can share its jobs.
        """
        if plugin_id not in self._plugins:
            self._reserved.add(plugin_id)
        if plugin_id not in self._jobs:
            if self._job_factory is None:
                raise DomainError("No job store is configured")
            self._jobs[plugin_id] = self._job_factory(plugin_id.replace("-", "_"), label or self._title(plugin_id))
        return self._jobs[plugin_id]

    @staticmethod
    def capabilities(plugin: Any) -> list[str]:
        return [name for name, protocol in CAPABILITIES.items() if isinstance(plugin, protocol)]

    def plugin(self, plugin_id: str) -> Any:
        if plugin_id not in self._plugins:
            raise DomainError(f"Unknown plugin: {plugin_id}")
        return self._plugins[plugin_id]

    def describe(self) -> list[dict[str, Any]]:
        return [{"id": plugin.id, "version": plugin.version, "title": self._title(plugin.id),
                 "description": getattr(plugin, "description", ""), "capabilities": self.capabilities(plugin),
                 "params_schema": self._schema(plugin)} for plugin in self._plugins.values()]

    def view(self, branch_id: str, head: int) -> BranchView:
        branch = self.framework.store.branch(branch_id)
        if not 0 <= head <= branch.head:
            raise DomainError(f"Position {head} is outside branch history (0 to {branch.head})")
        return BranchView(branch_id, head, history=lambda: self.framework.history(branch_id, head),
                          state=lambda seq: self.framework.state(branch_id, seq),
                          analyses=lambda: self._latest(branch_id, head))

    def analyze(self, plugin_id: str, branch_id: str, start: int, end: int,
                params: dict | None = None) -> dict[str, Any]:
        """Run an analyzer inline and return its analysis record.

        With a job store, the run is also recorded as a completed job, so its findings are shown and readable by
        other plugins exactly like a background run's.
        """
        record = self._run(plugin_id, branch_id, start, end, params)
        if plugin_id in self._jobs:
            self._jobs[plugin_id].create({**self._job(record["plugin_id"], record["plugin_version"], branch_id,
                                                      start, end, record["config"]),
                                          "status": "completed", "analysis": record, "finished_at": utc_now()})
        return record

    def save(self, plugin: Any, view: BranchView, start: int, end: int, config: dict,
             items: Iterable[Any]) -> dict[str, Any]:
        """Persist returned items as an analysis record; for plugins that run their own job flow.

        The input digest covers events 1..end, everything the view could read, read fresh from the store.
        """
        history = self.framework.history(view.branch_id, end)
        digest = hashlib.sha256(json.dumps([asdict(event) for event in history], sort_keys=True).encode()).hexdigest()
        record = {"id": new_id(), "plugin_id": plugin.id, "plugin_version": plugin.version,
                  "branch_id": view.branch_id, "cursor": end, "start": start, "end": end, "config": config,
                  "input_digest": digest, "analyses_read": view.analyses_read, "created_at": utc_now(),
                  "output": encode_output(list(items), plugin.id, start, end, {event.id for event in history})}
        self.framework.store.save_analysis(record)
        return record

    def submit(self, plugin_id: str, branch_id: str, start: int, end: int, params: dict | None = None) -> dict:
        """Queue an analysis job; call `execute` with its ID (the web layer does so in the background)."""
        plugin = self.plugin(plugin_id)
        if not self._runs(plugin):
            raise DomainError(f"Plugin {plugin_id} is not an analyzer")
        self._range(branch_id, start, end)
        record = self._job(plugin.id, plugin.version, branch_id, start, end, self._dump(self._params(plugin, params)))
        self.jobs(plugin_id).create(record)
        return record

    def execute(self, plugin_id: str, job_id: str) -> None:
        jobs = self._store(plugin_id)
        record = jobs.claim(job_id)
        if record is None:
            return
        try:
            analysis = self._run(plugin_id, record["branch_id"], record["start"], record["end"], record["config"])
            record.update(status="completed", analysis=analysis)
        except Exception as exc:
            record_failure(record, exc, f"{self._title(plugin_id)} failed; no findings were saved.")
        record["finished_at"] = utc_now()
        jobs.update(record)

    def recover_interrupted(self) -> None:
        """Mark jobs that a previous server process left queued or running as interrupted; call once at startup."""
        for store in self._jobs.values():
            store.recover_interrupted()

    def job(self, plugin_id: str, job_id: str) -> dict:
        return self._store(plugin_id).get(job_id)

    def branch_jobs(self, branch_id: str, plugin_id: str | None = None) -> list[dict]:
        self.framework.store.branch(branch_id)
        stores = [self._store(plugin_id)] if plugin_id else self._jobs.values()
        return sorted((job for store in stores for job in store.list(branch_id)),
                      key=lambda job: job["created_at"], reverse=True)

    def findings(self, branch_id: str, plugin_id: str | None = None) -> tuple[list[Metric], list[Annotation]]:
        """The findings a branch shows: each plugin's latest finished analysis that the branch can see."""
        latest = self._latest(branch_id, self.framework.store.branch(branch_id).head)
        return combine(record for store_id, record in latest.items() if plugin_id in (None, store_id))

    def intervene(self, plugin_id: str, branch_id: str, at: int, params: dict | None, name: str) -> Branch:
        """Fork at `at` and append the facts the intervention returns; the parent branch never changes."""
        plugin = self.plugin(plugin_id)
        if not isinstance(plugin, Intervention):
            raise DomainError(f"Plugin {plugin_id} is not an intervention")
        validated = self._params(plugin, params)
        facts = list(plugin.intervene(self.view(branch_id, at), at, validated))
        if not facts or not all(isinstance(fact, Fact) for fact in facts):
            raise DomainError(f"Intervention {plugin_id} must return one or more Facts")
        provenance = {"origin": "intervention", "actor": "plugin", "plugin": plugin.id,
                      "plugin_version": plugin.version, "params": self._dump(validated), "applied_to_runtime": False}
        return self.framework.fork_with_facts(branch_id, at, name, [
            replace(fact, source={**fact.source, **provenance}) for fact in facts])

    def _run(self, plugin_id: str, branch_id: str, start: int, end: int, params: dict | None) -> dict[str, Any]:
        plugin = self.plugin(plugin_id)
        if not self._runs(plugin):
            raise DomainError(f"Plugin {plugin_id} is not an analyzer")
        view = self._range(branch_id, start, end)
        validated = self._params(plugin, params)
        config = self._dump(validated)
        if isinstance(plugin, Analyzer):
            items = list(plugin.analyze(view, start, end, validated))
        else:
            events = view.events(start, end)
            items = [item for offset in range(0, len(events), STREAM_BATCH)
                     for item in plugin.on_events(view, events[offset:offset + STREAM_BATCH], validated)]
        return self.save(plugin, view, start, end, config, items)

    @staticmethod
    def _job(plugin_id: str, version: str, branch_id: str, start: int, end: int, config: dict) -> dict[str, Any]:
        return {"id": new_id(), "plugin_id": plugin_id, "plugin_version": version, "branch_id": branch_id,
                "start": start, "end": end, "cursor": end, "created_at": utc_now(), "status": "queued", "config": config}

    def _latest(self, branch_id: str, head: int) -> dict[str, dict]:
        """Per plugin, the latest finished analysis that read no event after `head` of this branch.

        A branch sees its own analyses and its ancestors' analyses that end at or before the fork, because only
        those read history the branch shares.
        """
        latest: dict[str, dict] = {}
        for owner, limit in self._lineage(branch_id, head):
            for store_id, store in self._jobs.items():
                for job in store.list(owner, limit=None):
                    analysis = job.get("analysis")
                    if job["status"] in FINISHED and analysis and analysis["cursor"] <= limit and (
                            store_id not in latest or analysis["created_at"] > latest[store_id]["created_at"]):
                        latest[store_id] = analysis
        return latest

    def _store(self, plugin_id: str) -> Jobs:
        if plugin_id not in self._jobs:
            raise DomainError(f"Plugin {plugin_id} has no jobs")
        return self._jobs[plugin_id]

    def _lineage(self, branch_id: str, head: int) -> list[tuple[str, int]]:
        branch = self.framework.store.branch(branch_id)
        lineage, limit = [(branch.id, head)], head
        while branch.parent_id:
            limit = min(limit, branch.fork_position)
            branch = self.framework.store.branch(branch.parent_id)
            lineage.append((branch.id, limit))
        return lineage

    def _range(self, branch_id: str, start: int, end: int) -> BranchView:
        if not 1 <= start <= end:
            raise DomainError("An analysis needs 1 <= start <= end")
        return self.view(branch_id, end)

    def _title(self, plugin_id: str) -> str:
        return getattr(self._plugins.get(plugin_id), "title", plugin_id)

    @staticmethod
    def _runs(plugin: Any) -> bool:
        return isinstance(plugin, Analyzer | StreamingAnalyzer)

    @staticmethod
    def _schema(plugin: Any) -> dict:
        model = getattr(plugin, "Params", None)
        return model.model_json_schema() if model else {"type": "object", "properties": {}}

    @staticmethod
    def _params(plugin: Any, raw: dict | None) -> Any:
        model = getattr(plugin, "Params", None)
        if model is None:
            if raw:
                raise DomainError(f"Plugin {plugin.id} takes no params")
            return None
        try:
            return model.model_validate(raw or {})
        except ValueError as exc:
            raise DomainError(f"Invalid params for {plugin.id}: {exc}") from exc

    @staticmethod
    def _dump(params: Any) -> dict:
        return {} if params is None else params.model_dump(mode="json", by_alias=True)
