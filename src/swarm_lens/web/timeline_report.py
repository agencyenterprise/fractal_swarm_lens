"""The timeline plugin's sub-API: on-demand saved-trace analyses run as durable background jobs."""
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, ConfigDict, Field

from swarm_lens.adapters.jobs import record_failure
from swarm_lens.core.models import DomainError, new_id, utc_now
from swarm_lens.observability.timeline import TimelineConfig, TraceTooLarge
from swarm_lens.observability.timeline.method import METHODS, leaves_of, sections, token_chunks
from swarm_lens.observability.timeline.render import render_history
from .extensions import WebExtension

PREFIX = "/api/plugins/timeline"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str = Field(min_length=1, max_length=100)
    cursor: int = Field(ge=1)
    config: dict = Field(default_factory=dict)


def config_schema():
    defaults = asdict(TimelineConfig())
    properties = {"method": {"type": "string", "enum": list(METHODS), "default": defaults.pop("method")}}
    properties.update({key: {"type": "integer", "minimum": 1, "default": value} for key, value in defaults.items()})
    return {"type": "object", "properties": properties, "additionalProperties": False}


def planned_windows(lines, llm, config):
    """How many sections read raw events, before the orchestrator or combine calls."""
    if config.method == "orchestrated":
        return len(token_chunks(lines, llm, config.chunk_tokens))
    if config.method == "goal_tree":
        return len(leaves_of(sections(lines, config.window_chars)))
    return 1


class TimelineService:
    def __init__(self, framework, jobs, plugin_id="timeline"):
        self.framework, self.jobs, self.plugin = framework, jobs, framework.plugins[plugin_id]

    def preview(self, branch_id, cursor, raw_config):
        config = TimelineConfig.from_dict(raw_config)
        lines = render_history(self.framework.history(branch_id, cursor))
        model = self.plugin.llm.describe()
        reason = model["reason"] or (None if lines else "This history has no events to summarize.")
        return {"branch_id": branch_id, "cursor": cursor, "config": asdict(config), "events": len(lines),
                "estimated_tokens": sum(self.plugin.llm.count_tokens(line) + 1 for _, line in lines),
                "windows": planned_windows(lines, self.plugin.llm, config) if lines else 0,
                "ready": reason is None, "reason": reason, "model": model}

    def submit(self, branch_id, cursor, raw_config):
        config = TimelineConfig.from_dict(raw_config)
        model = self.plugin.llm.describe()
        if not model["ready"]:
            raise DomainError(model["reason"])
        self.framework.history(branch_id, cursor)  # Rejects an unknown branch or a cursor beyond its head.
        record = {"id": new_id(), "plugin_id": self.plugin.id, "plugin_version": self.plugin.version,
                  "branch_id": branch_id, "cursor": cursor, "created_at": utc_now(), "status": "queued",
                  "config": asdict(config), "model": model}
        self.jobs.create(record)
        return record

    def execute(self, job_id):
        record = self.jobs.claim(job_id)
        if record is None:
            return
        try:
            analysis = self.framework.analyze(self.plugin.id, record["branch_id"], record["cursor"], record["config"])
            record.update(status="completed", analysis=analysis)
        except Exception as exc:
            record_failure(record, exc, "Timeline analysis failed; no milestones were inferred.",
                           safe=(TraceTooLarge,))
            record.update(usage=getattr(exc, "usage", None), calls=getattr(exc, "calls", None))
        record["finished_at"] = utc_now()
        self.jobs.update(record)


def timeline_extension(service):
    @asynccontextmanager
    async def lifespan(app):
        service.jobs.recover_interrupted()
        yield

    router = APIRouter(prefix=PREFIX, tags=["Timeline"], lifespan=lifespan)

    def manifest():
        return {"id": "timeline", "version": service.plugin.version, "title": "Timeline of relevant events",
                "modes": ["saved_trace"], "api_prefix": PREFIX, "ui": {"renderer": "timeline"},
                "model": service.plugin.llm.describe(), "config": config_schema()}

    @router.get("/capabilities")
    def capabilities():
        return manifest()

    @router.post("/preview")
    def preview(request: AnalysisRequest):
        return service.preview(request.branch_id, request.cursor, request.config)

    @router.post("/analyses", status_code=202)
    def analyze(request: AnalysisRequest, background: BackgroundTasks):
        job = service.submit(request.branch_id, request.cursor, request.config)
        background.add_task(service.execute, job["id"])
        return job

    @router.get("/analyses")
    def analyses(branch_id: str = Query(min_length=1, max_length=100)):
        service.framework.store.branch(branch_id)
        return {"jobs": service.jobs.list(branch_id)}

    @router.get("/analyses/{job_id}")
    def analysis(job_id: str):
        return service.jobs.get(job_id)

    return WebExtension("timeline", router, manifest)
