"""HTTP routes for registered plugins: discovery, analysis jobs, findings and interventions."""
from dataclasses import asdict
from itertools import groupby

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, Field

from swarm_lens.application.plugins import PluginService
from swarm_lens.core.findings import Metric

MAX_SERIES_POINTS = 2000


class AnalysisRequest(BaseModel):
    plugin_id: str
    start: int = Field(default=1, ge=1)
    end: int = Field(ge=1)
    params: dict = Field(default_factory=dict)


class PluginInterventionRequest(BaseModel):
    at: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=160)
    params: dict = Field(default_factory=dict)


def downsample(points: list[tuple[int, float]], limit: int) -> list[tuple[int, float]]:
    """Keep each bucket's lowest and highest point, in position order, so spikes survive."""
    if len(points) <= limit:
        return points
    size = -(-len(points) // max(1, limit // 2))
    kept = []
    for offset in range(0, len(points), size):
        bucket = points[offset:offset + size]
        kept += sorted({min(bucket, key=lambda point: point[1]), max(bucket, key=lambda point: point[1])})
    return kept


def series(metrics: list[Metric], limit: int) -> list[dict]:
    """One series per plugin, metric name and agent, as (seq, value) points."""
    key = lambda metric: (metric.plugin, metric.name, metric.agent_id or "")
    result = []
    for (plugin, name, agent), group in groupby(sorted(metrics, key=lambda m: (*key(m), m.seq)), key=key):
        points = [(metric.seq, metric.value) for metric in group]
        kept = downsample(points, limit)
        result.append({"plugin": plugin, "name": name, "agent_id": agent or None,
                       "points": [list(point) for point in kept], "downsampled": len(kept) < len(points)})
    return result


def plugins_router(plugins: PluginService) -> APIRouter:
    router = APIRouter(tags=["Plugins"])

    @router.get("/api/plugins")
    def registered():
        return {"plugins": plugins.describe()}

    @router.post("/api/branches/{branch_id}/analyses", status_code=202)
    def analyze(branch_id: str, request: AnalysisRequest, background: BackgroundTasks):
        job = plugins.submit(request.plugin_id, branch_id, request.start, request.end, request.params)
        background.add_task(plugins.execute, job["plugin_id"], job["id"])
        return job

    @router.get("/api/branches/{branch_id}/analyses")
    def analyses(branch_id: str, plugin_id: str | None = None):
        return {"jobs": plugins.branch_jobs(branch_id, plugin_id)}

    @router.get("/api/analyses/{plugin_id}/{job_id}")
    def analysis(plugin_id: str, job_id: str):
        return plugins.job(plugin_id, job_id)

    @router.get("/api/branches/{branch_id}/series")
    def metric_series(branch_id: str, plugin: str | None = None, name: str | None = None,
                      agent: str | None = None, max_points: int = Query(MAX_SERIES_POINTS, ge=2, le=20_000)):
        metrics = [metric for metric in plugins.findings(branch_id, plugin)[0]
                   if name in (None, metric.name) and agent in (None, metric.agent_id)]
        return {"series": series(metrics, max_points)}

    @router.get("/api/branches/{branch_id}/annotations")
    def annotations(branch_id: str, plugin: str | None = None,
                    start: int | None = Query(None, alias="from", ge=1), end: int | None = Query(None, alias="to", ge=1)):
        found = plugins.findings(branch_id, plugin)[1]
        return {"annotations": [asdict(annotation) for annotation in found
                                if (start is None or annotation.seq_to >= start)
                                and (end is None or annotation.seq_from <= end)]}

    @router.post("/api/branches/{branch_id}/interventions/{plugin_id}", status_code=201)
    def intervene(branch_id: str, plugin_id: str, request: PluginInterventionRequest):
        return asdict(plugins.intervene(plugin_id, branch_id, request.at, request.params, request.name))

    return router
