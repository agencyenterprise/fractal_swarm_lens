"""Core live endpoints, plus a resumable stream of committed event summaries."""
import asyncio
from dataclasses import asdict
import json
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from swarm_lens.core.models import DomainError, Fact


class CaptureRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    metadata: dict = Field(default_factory=dict)


class FactRequest(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    kind: str
    data: dict
    occurred_at: str
    source: dict = Field(default_factory=dict)


class IngestRequest(BaseModel):
    expected_head: int = Field(ge=0)
    facts: list[FactRequest] = Field(min_length=1, max_length=500)


class FinishRequest(BaseModel):
    status: str


class ExecuteRequest(BaseModel):
    expected_head: int = Field(ge=0)
    steps: int = Field(default=1, ge=1, le=100)


class StartRequest(BaseModel):
    request_id: UUID


class BranchIntervention(BaseModel):
    kind: str
    data: dict


class ExecutionPreviewRequest(BaseModel):
    cursor: int = Field(ge=0)
    intervention: BranchIntervention | None = None


class ForkExecuteRequest(ExecutionPreviewRequest):
    request_id: UUID
    cursor: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=160)
    steps: int = Field(default=1, ge=1, le=100)


def live_router(service, summarize):
    router = APIRouter()

    @router.get('/api/live/apps')
    def apps():
        return service.apps()

    @router.post('/api/live/apps/{runtime_id}/start', status_code=202)
    def start(runtime_id: str, request: StartRequest):
        return service.submit_start(runtime_id, str(request.request_id))

    @router.post('/api/live/runs')
    def create(request: CaptureRequest):
        return service.create_capture(request.client_id, request.name, request.metadata)

    @router.post('/api/live/branches/{branch_id}/events')
    def ingest(branch_id: str, request: IngestRequest):
        if len(request.model_dump_json()) > 8_000_000:
            raise DomainError('Capture batch exceeds 8 MB; submit smaller batches')
        return service.append(branch_id, [Fact(**fact.model_dump()) for fact in request.facts], request.expected_head)

    @router.post('/api/live/branches/{branch_id}/finish')
    def finish(branch_id: str, request: FinishRequest):
        return service.finish_capture(branch_id, request.status)

    @router.get('/api/branches/{branch_id}/execution')
    def execution(branch_id: str, cursor: int = Query(ge=0)):
        return service.describe(branch_id, cursor)

    @router.post('/api/branches/{branch_id}/execute', status_code=202)
    def execute(branch_id: str, request: ExecuteRequest):
        return service.submit(branch_id, request.expected_head, request.steps)

    @router.post('/api/branches/{branch_id}/fork-execute', status_code=202)
    def fork_execute(branch_id: str, request: ForkExecuteRequest):
        return service.submit_fork(branch_id, request.cursor, request.name, request.steps, str(request.request_id),
                                   request.intervention.model_dump() if request.intervention else None)

    @router.post('/api/branches/{branch_id}/execution/preview')
    def preview(branch_id: str, request: ExecutionPreviewRequest):
        return service.describe(branch_id, request.cursor,
                                request.intervention.model_dump() if request.intervention else None)

    @router.get('/api/executions/{job_id}')
    def job(job_id: str):
        return service.job(job_id)

    def updates(branch_id, after):
        branch = service.framework.store.branch(branch_id)
        if not 0 <= after <= branch.head:
            raise DomainError('Live cursor is outside branch history')
        events = service.framework.store.events(branch_id, min(branch.head, after+200), after=after)
        return {'type': 'events', 'branch': asdict(branch), 'events': [summarize(e) for e in events],
                'cursor': events[-1].position if events else after,
                'capture': service.capture(branch_id), 'jobs': service.jobs(branch_id)}

    @router.get('/api/live/branches/{branch_id}/events')
    def catchup(branch_id: str, after: int = Query(default=0, ge=0)):
        return updates(branch_id, after)

    @router.websocket('/api/live/branches/{branch_id}/stream')
    async def stream(websocket: WebSocket, branch_id: str, after: int = 0):
        origin = websocket.headers.get('origin')
        expected_scheme = 'https' if websocket.url.scheme == 'wss' else 'http'
        if origin and origin.rstrip('/') != f'{expected_scheme}://{websocket.headers.get("host")}':
            await websocket.close(code=1008)
            return
        await websocket.accept()
        last_status = None
        try:
            while True:
                payload = await asyncio.to_thread(updates, branch_id, after)
                status = json.dumps([payload['capture'], payload['jobs']], sort_keys=True)
                if payload['events'] or status != last_status:
                    await websocket.send_json(payload)
                    after, last_status = payload['cursor'], status
                if after < payload['branch']['head']:
                    continue
                # The database is the durable queue: reconnects cannot miss a commit.
                # One page in flight bounds memory even for slow browsers.
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=.3)
                except asyncio.TimeoutError:
                    pass
        except WebSocketDisconnect:
            pass
        except DomainError as exc:
            await websocket.send_json({'type': 'error', 'detail': str(exc)})
            await websocket.close(code=1008)
    return router
