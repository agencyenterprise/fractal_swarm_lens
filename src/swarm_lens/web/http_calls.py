"""The HTTP calls plugin's sub-API: a synchronous, model-free analysis persisted by Framework.analyze."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from swarm_lens.observability.http_calls import HttpCallsConfig, HttpCallsPlugin
from .extensions import WebExtension

PREFIX = f"/api/plugins/{HttpCallsPlugin.id}"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str = Field(min_length=1, max_length=100)
    cursor: int = Field(ge=1)
    config: dict = Field(default_factory=dict)


def http_calls_extension(framework):
    plugin = framework.plugins[HttpCallsPlugin.id]
    router = APIRouter(prefix=PREFIX, tags=["HTTP calls"])

    def manifest():
        return {"id": plugin.id, "version": plugin.version, "title": "Network activity",
                "modes": ["saved_trace"], "api_prefix": PREFIX, "ui": {"renderer": "http_calls"},
                "config": HttpCallsConfig.schema()}

    @router.get("/capabilities")
    def capabilities():
        return manifest()

    @router.post("/analyses")
    def analyze(request: AnalysisRequest):
        return framework.analyze(plugin.id, request.branch_id, request.cursor, request.config)

    return WebExtension(plugin.id, router, manifest)
