"""The timeline web extension, exercised through the real FastAPI application with a fake model."""
import pytest
from fastapi.testclient import TestClient

from conftest import AT, Facts
from swarm_lens import Fact
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.observability.timeline import TimelinePlugin
from swarm_lens.web.api import create_app
from swarm_lens.web.timeline_report import TimelineService, timeline_extension
from test_timeline import FakeLLM


class ReadyLLM(FakeLLM):
    ready = True

    def describe(self):
        return {"model": "fake-model", "ready": self.ready, "context_window": 1_000_000,
                "reason": None if self.ready else "Configure OPENAI_API_KEY on the server."}


@pytest.fixture
def run(framework):
    branch = framework.create_run("Run")
    framework.ingest(branch.id, Facts(
        Fact("environment.updated", {"task": "Add numbers", "goal": "Report the sum"}, AT),
        Fact("agent.added", {"id": "a", "name": "Agent A", "system_prompt": "Be careful."}, AT),
        Fact("channel.created", {"id": "c", "name": "Shared", "members": ["a"]}, AT),
        Fact("message.created", {"id": "m1", "channel_id": "c", "sender_id": "a", "content": "2+2=4"}, AT),
        Fact("message.created", {"id": "m2", "channel_id": "c", "sender_id": "a", "content": "hide the SECRET"}, AT),
        Fact("message.created", {"id": "m3", "channel_id": "c", "sender_id": "a", "content": "future"}, AT)))
    return framework.store.branch(branch.id)


def client_for(framework, tmp_path, llm):
    framework.plugins["timeline"] = TimelinePlugin(llm)
    service = TimelineService(framework, JobStore(tmp_path / "timeline.sqlite", "timeline", "timeline"))
    return TestClient(create_app(framework, extensions=(timeline_extension(service),))), service


def test_manifest_preview_and_completed_job_persist_an_analysis(framework, run, tmp_path):
    llm = ReadyLLM()
    client, service = client_for(framework, tmp_path, llm)
    with client:
        manifest = client.get("/api/workspace").json()["capabilities"]["web_plugins"][0]
        assert manifest == client.get("/api/plugins/timeline/capabilities").json()
        assert manifest["id"] == "timeline" and manifest["ui"] == {"renderer": "timeline"}
        assert manifest["api_prefix"] == "/api/plugins/timeline" and manifest["model"]["model"] == "fake-model"
        properties = manifest["config"]["properties"]
        assert properties["method"]["default"] == "orchestrated" and properties["chunk_tokens"]["default"] == 500_000

        payload = {"branch_id": run.id, "cursor": 5}
        preview = client.post("/api/plugins/timeline/preview", json=payload).json()
        assert preview["events"] == 5 and preview["windows"] == 1 and preview["ready"]
        assert preview["estimated_tokens"] > 0 and not llm.prompts

        submitted = client.post("/api/plugins/timeline/analyses", json={**payload, "config": {"method": "single"}})
        assert submitted.status_code == 202 and submitted.json()["status"] == "queued"
        job = client.get(f"/api/plugins/timeline/analyses/{submitted.json()['id']}").json()
        assert job["status"] == "completed" and job["config"]["method"] == "single"
        output = job["analysis"]["output"]
        assert [m["positions"] for m in output["milestones"] if m["misaligned"]] == [[5]]
        assert "future" not in llm.prompts[0]
        assert framework.store.analyses(run.id, 5) == [job["analysis"]]
        assert client.get("/api/plugins/timeline/analyses", params={"branch_id": run.id}).json()["jobs"] == [job]


def test_failed_job_records_safe_message_detail_and_partial_usage(framework, run, tmp_path):
    client, service = client_for(framework, tmp_path, ReadyLLM(fail_on="SECRET"))
    with client:
        submitted = client.post("/api/plugins/timeline/analyses",
                                json={"branch_id": run.id, "cursor": 5, "config": {"method": "single"}})
        job = client.get(f"/api/plugins/timeline/analyses/{submitted.json()['id']}").json()
    assert job["status"] == "failed" and "analysis" not in job
    assert job["error"] == "Timeline analysis failed; no milestones were inferred."
    assert job["error_type"] == "IncompleteResponse"
    assert job["usage"]["calls"] == job["usage"]["failed_calls"] == 1 and job["usage"]["input_tokens"] > 0
    assert job["calls"][0]["status"] == "failed"
    assert not framework.store.analyses(run.id, 5)


def test_requests_are_validated_before_a_job_is_created(framework, run, tmp_path):
    llm = ReadyLLM()
    client, service = client_for(framework, tmp_path, llm)
    url = "/api/plugins/timeline/analyses"
    with client:
        assert client.post(url, json={"branch_id": run.id, "cursor": 99}).status_code == 400
        assert client.post(url, json={"branch_id": run.id, "cursor": 5, "config": {"method": "bfs"}}).status_code == 400
        assert client.post(url, json={"branch_id": run.id, "cursor": 5, "live": True}).status_code == 422
        assert client.post(url, json={"branch_id": run.id, "cursor": 5},
                           headers={"Origin": "https://unrelated.example"}).status_code == 403
        llm.ready = False
        assert client.post("/api/plugins/timeline/preview", json={"branch_id": run.id, "cursor": 5}).json()["ready"] is False
        assert client.post(url, json={"branch_id": run.id, "cursor": 5}).status_code == 400
    assert not service.jobs.list(run.id) and not llm.prompts


def test_startup_marks_pending_jobs_interrupted_without_calling_the_model(framework, run, tmp_path):
    llm = ReadyLLM()
    client, service = client_for(framework, tmp_path, llm)
    job = service.submit(run.id, 5, {})
    with client:
        assert client.get(f"/api/plugins/timeline/analyses/{job['id']}").json()["status"] == "interrupted"
    service.execute(job["id"])
    assert not llm.prompts


def test_plugin_routes_stay_in_their_namespace(framework, tmp_path):
    from fastapi import APIRouter
    from swarm_lens.web.extensions import WebExtension

    _, service = client_for(framework, tmp_path, ReadyLLM())
    extension = timeline_extension(service)
    assert all(route.path.startswith("/api/plugins/timeline/") for route in extension.router.routes)
    stray = APIRouter()
    stray.get("/api/plugins/other/capabilities")(lambda: {})
    with pytest.raises(ValueError, match="namespace"):
        create_app(framework, extensions=(WebExtension("timeline", stray, extension.manifest),))
