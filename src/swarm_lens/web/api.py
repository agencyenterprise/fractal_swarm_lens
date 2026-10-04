from collections import Counter
from dataclasses import asdict
from functools import lru_cache
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from swarm_lens import Framework
from swarm_lens.core.models import Conflict, DomainError
from .live import BranchIntervention


class ForkRequest(BaseModel):
    cursor: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=160)
    intervention: BranchIntervention | None = None


class InterventionRequest(BaseModel):
    kind: str
    data: dict
    expected_head: int = Field(ge=0)


class AnalysisRequest(BaseModel):
    plugin_id: str = "activity"
    cursor: int = Field(ge=0)
    config: dict = Field(default_factory=dict)


class TraceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=200_000)
    task: str = Field(default="", max_length=10_000)


class CheckpointRequest(BaseModel):
    cursor: int | None = Field(default=None, ge=0)
    message: str = "Visual explorer checkpoint"


class CommentRequest(BaseModel):
    event_id: str
    author: str
    text: str
    parent_id: str | None = None


class CommentEdit(BaseModel):
    text: str | None = None
    resolved: bool | None = None


BUNDLE_LIMIT_BYTES = 64 * 1024 * 1024
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def read_bundle(request: Request) -> object:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > BUNDLE_LIMIT_BYTES:
            raise HTTPException(413, f"A run bundle is limited to {BUNDLE_LIMIT_BYTES} bytes")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise DomainError("A run bundle must be valid JSON") from exc


def download_disposition(run_name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", run_name).strip("-.") or "run"
    return f'attachment; filename="{stem}.swarm-lens.json"; filename*=UTF-8\'\'{quote(run_name, safe="")}.swarm-lens.json'


def event_summary(event):
    d = event.data
    cp = d.get('metadata', {}).get('crewai', {})
    resume_point = ({key: cp.get(key) for key in ('next_task', 'total_tasks', 'replay_reason')}
                    if event.kind == 'environment.updated' and event.source.get('origin') == 'crewai'
                    and cp.get('phase') == 'boundary' else None)
    family = event.kind.split(".")[0]
    agent_id = d.get("sender_id") or d.get("agent_id") or d.get("owner_id")
    if family == "agent":
        agent_id = d.get("id")
    text = d.get("content") or d.get("goal") or d.get("tool_name") or d.get("name") or ""
    return {"id": event.id, "position": event.position, "kind": event.kind,
            "at": event.occurred_at, "agent_id": agent_id, "channel_id": d.get("channel_id"),
            "label": d.get("type") or event.kind, "preview": str(text)[:260],
            "intervention": event.source.get("origin") == "intervention",
            "resume_point": resume_point,
            "agent_name": d.get("name") if family == "agent" else None,
            "model": d.get("model") if family == "agent" else None,
            "entity_id": d.get("id"), "reply_to_id": d.get("reply_to_id"),
            "stage_label": d.get("metadata", {}).get("stage_label"),
            "delivered_sources": event.source.get("delivered_sources"),
            "channel_name": d.get("name") if family == "channel" else None}


def create_app(framework: Framework, artifacts=None, *, extensions=(), live=None) -> FastAPI:
    extensions = tuple(extensions)
    if len({extension.id for extension in extensions}) != len(extensions):
        raise ValueError("Duplicate web extension ID")
    @asynccontextmanager
    async def lifespan(app):
        if live:
            live.recover()
        yield
        if live:
            import asyncio
            await asyncio.to_thread(live.close)

    app = FastAPI(title="Swarm Lens", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409 if isinstance(exc, Conflict) else 400)

    @app.middleware("http")
    async def local_mutations(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin mutations are not accepted"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @lru_cache(maxsize=6)
    def history(branch_id, head):
        return framework.history(branch_id, head)

    @lru_cache(maxsize=6)
    def state_at(branch_id, cursor):
        return framework.state(branch_id, cursor)

    @app.get("/api/workspace")
    def workspace():
        runs = framework.store.runs()
        return {"runs": [asdict(run) for run in runs],
                "branches": [asdict(branch) for run in runs for branch in framework.store.branches(run.id)],
                "capabilities": {**framework.capabilities(),
                                 "live": {"enabled": live is not None, "runtimes": list(live.runtimes) if live else []},
                                 "web_plugins": [extension.manifest() for extension in extensions]}}

    @app.post("/api/traces", status_code=201)
    def import_trace(request: TraceRequest):
        return {"branch": asdict(framework.import_transcript(request.name, request.text, request.task))}

    @app.get("/api/branches/{branch_id}/timeline")
    def timeline(branch_id: str):
        branch = framework.store.branch(branch_id)
        return {"branch": asdict(branch), "events": [event_summary(event) for event in history(branch_id, branch.head)]}

    @app.get("/api/branches/{branch_id}/state")
    def state(branch_id: str, cursor: int = Query(ge=0)):
        value = state_at(branch_id, cursor)
        activity = Counter(message.sender_id for message in value.messages.values() if message.sender_id)
        edges = Counter((message.sender_id, message.channel_id) for message in value.messages.values() if message.sender_id)
        return {"branch_id": branch_id, "cursor": cursor, "occurred_at": value.occurred_at,
                "agents": {key: asdict(agent) for key, agent in value.agents.items()},
                "channels": {key: asdict(channel) for key, channel in value.channels.items()},
                "environment": asdict(value.environment),
                "counts": {"messages": len(value.messages), "tools": len(value.tools), "memories": len(value.memories),
                           "agents": sum(agent.active for agent in value.agents.values())},
                "activity": dict(activity),
                "edges": [{"agent_id": aid, "channel_id": cid, "count": count} for (aid, cid), count in edges.items()],
                "memories": [{"id": memory.id, "owner_id": memory.owner_id, "preview": memory.content[:220]}
                             for memory in value.memories.values()]}

    @app.get("/api/branches/{branch_id}/events/{event_id}")
    def event_detail(branch_id: str, event_id: str, cursor: int = Query(ge=0)):
        branch = framework.store.branch(branch_id)
        if cursor > branch.head:
            raise DomainError("Cursor is outside branch history")
        event = next((event for event in history(branch_id, branch.head)
                      if event.id == event_id and event.position <= cursor), None)
        if event is None:
            raise DomainError("Event is not visible at this branch and cursor")
        return asdict(event)

    @app.get("/api/branches/{branch_id}/memory")
    def memory(branch_id: str, memory_id: str, cursor: int = Query(ge=0)):
        value = state_at(branch_id, cursor).memories.get(memory_id)
        if value is None:
            raise DomainError("Memory is not visible at this cursor")
        return asdict(value)

    @app.post("/api/branches/{branch_id}/fork")
    def fork(branch_id: str, request: ForkRequest):
        return asdict(framework.fork_with_intervention(branch_id, request.cursor, request.name,
                      request.intervention.model_dump() if request.intervention else None))

    @app.post("/api/branches/{branch_id}/interventions")
    def intervene(branch_id: str, request: InterventionRequest):
        if live:
            with live.lock:
                live.assert_idle(branch_id)
                return asdict(framework.intervene(branch_id, request.kind, request.data, request.expected_head, actor="explorer"))
        return asdict(framework.intervene(branch_id, request.kind, request.data, request.expected_head, actor="explorer"))

    @app.post("/api/branches/{branch_id}/analyses")
    def analyze(branch_id: str, request: AnalysisRequest):
        return framework.analyze(request.plugin_id, branch_id, request.cursor, request.config)

    @app.post("/api/branches/{branch_id}/checkpoint")
    def checkpoint(branch_id: str, request: CheckpointRequest):
        return {"commit": framework.checkpoint(branch_id, request.cursor, request.message)}

    @app.get("/api/branches/{branch_id}/comments")
    def comments(branch_id: str):
        return {"comments": [asdict(comment) for comment in framework.comments(branch_id)]}

    @app.post("/api/branches/{branch_id}/comments", status_code=201)
    def add_comment(branch_id: str, request: CommentRequest):
        return asdict(framework.comment(branch_id, request.event_id, request.author, request.text, request.parent_id))

    @app.patch("/api/comments/{comment_id}")
    def edit_comment(comment_id: str, request: CommentEdit):
        return asdict(framework.edit_comment(comment_id, text=request.text, resolved=request.resolved))

    @app.delete("/api/comments/{comment_id}", status_code=204)
    def delete_comment(comment_id: str):
        framework.delete_comment(comment_id)
        return Response(status_code=204)

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str):
        bundle = framework.export_run(run_id)
        return JSONResponse(bundle, headers={"Content-Disposition": download_disposition(bundle["run"]["name"])})

    @app.post("/api/runs/import", status_code=201)
    async def import_run(request: Request):
        branch = await run_in_threadpool(framework.import_run, await read_bundle(request))
        return {"branch": asdict(branch)}

    @app.get("/api/compare")
    def compare(left: str, right: str, left_cursor: int | None = None, right_cursor: int | None = None):
        return framework.diff(left, right, left_cursor, right_cursor)

    @app.get("/api/artifacts/{digest}")
    def artifact(digest: str):
        if artifacts is None:
            raise DomainError("No artifact store registered")
        try:
            return Response(artifacts.get(digest), media_type="application/json")
        except (ValueError, FileNotFoundError) as exc:
            raise DomainError("Unknown artifact") from exc

    existing = {(route.path, method) for route in app.routes for method in getattr(route, "methods", ())}
    for extension in extensions:
        for route in extension.router.routes:
            if not route.path.startswith(f"/api/plugins/{extension.id}/"):
                raise ValueError("Plugin routes must use their own API namespace")
            for method in getattr(route, "methods", ()):
                key = (route.path, method)
                if key in existing:
                    raise ValueError("Duplicate plugin route")
                existing.add(key)
        app.include_router(extension.router)

    if live:
        from .live import live_router
        app.include_router(live_router(live, event_summary))

    web = Path(__file__).parent
    app.mount("/assets", StaticFiles(directory=web), name="assets")

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    return app
