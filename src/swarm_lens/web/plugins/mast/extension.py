from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, ConfigDict, Field

from swarm_lens.observability.mast.method import assets
from swarm_lens.web.extensions import WebExtension
from .details import present_trait_details


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str = Field(min_length=1, max_length=100)
    cursor: int = Field(ge=1)
    completeness: Literal["unknown", "complete", "partial"] = "unknown"


def mast_extension(service):
    @asynccontextmanager
    async def lifespan(app):
        service.jobs.recover_interrupted()
        yield

    router = APIRouter(prefix="/api/plugins/mast", tags=["MAST"], lifespan=lifespan)

    def manifest():
        return {"version": service.plugin.version, "title": "MAST trace analysis", "modes": ["saved_trace"],
                "judge": service.plugin.judge.describe(), "upstream_revision": assets()["revision"],
                "max_trace_characters": service.plugin.max_trace_characters, "chunked_analysis": True,
                "workers": service.plugin.workers}

    @router.get("/capabilities")
    def capabilities():
        return extension.describe()

    @router.get("/taxonomy")
    def taxonomy():
        return {key: assets()[key] for key in ("categories", "repository", "revision", "upstream_notes")}

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

    @router.get("/analyses/{job_id}/traits/{code}")
    def trait_details(job_id: str, code: str):
        return present_trait_details(service, job_id, code)

    extension = WebExtension("mast", router, manifest, assets=Path(__file__).parent / "static")
    return extension
