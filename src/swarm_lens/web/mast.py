from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, ConfigDict, Field

from swarm_lens.observability.mast.method import assets
from .extensions import WebExtension


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str = Field(min_length=1, max_length=100)
    cursor: int = Field(ge=1)
    completeness: Literal["unknown", "complete", "partial"] = "unknown"


class TraceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=200_000)
    task: str = Field(default="", max_length=10_000)


def mast_extension(service):
    @asynccontextmanager
    async def lifespan(app):
        service.jobs.recover_interrupted()
        yield

    router = APIRouter(prefix="/api/plugins/mast", tags=["MAST"], lifespan=lifespan)

    def manifest():
        return {"id": "mast", "version": service.plugin.version, "title": "MAST trace analysis",
                "modes": ["saved_trace"], "api_prefix": "/api/plugins/mast", "ui": {"renderer": "mast"},
                "judge": service.plugin.judge.describe(), "upstream_revision": assets()["revision"],
                "max_trace_characters": service.plugin.max_trace_characters}

    @router.get("/capabilities")
    def capabilities():
        return manifest()

    @router.get("/taxonomy")
    def taxonomy():
        return {key: assets()[key] for key in ("categories", "repository", "revision", "upstream_notes")}

    @router.post("/traces", status_code=201)
    def import_trace(request: TraceRequest):
        return {"branch": asdict(service.import_trace(request.name, request.text, request.task))}

    @router.post("/analyses", status_code=202)
    def analyze(request: AnalysisRequest, background: BackgroundTasks):
        job = service.submit(request.branch_id, request.cursor, request.completeness)
        background.add_task(service.execute, job["id"])
        return job

    @router.post("/preview")
    def preview(request: AnalysisRequest):
        return service.preview(request.branch_id, request.cursor, request.completeness)

    @router.get("/analyses")
    def analyses(branch_id: str = Query(min_length=1, max_length=100)):
        service.framework.store.branch(branch_id)
        return {"jobs": service.jobs.list(branch_id)}

    @router.get("/analyses/{job_id}")
    def analysis(job_id: str):
        return service.jobs.get(job_id)

    return WebExtension("mast", router, manifest)
