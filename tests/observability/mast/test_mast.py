import json
import threading

import pytest
from fastapi.testclient import TestClient

from swarm_lens import PluginService
from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.core.models import DomainError
from swarm_lens.observability.mast import MastPlugin
from swarm_lens.observability.mast.method import assets, make_prompt, parse_response
from swarm_lens.observability.mast.service import MastService
from swarm_lens.web.api import create_app
from swarm_lens.web.plugins.mast import mast_extension


def assessment(*, named=False):
    rows = ["A. Agent A repeats an already completed step.", "B. no", "C."]
    for category in assets()["categories"]:
        label = " " + category["label"] + ":" if named else ""
        value = "yes" if category["code"] == "1.3" else "no"
        rows.append(f"{category['code']}{label} {value}")
    return "\n".join(rows)


class FakeJudge:
    def __init__(self, text=None, ready=True):
        self.text = assessment() if text is None else text
        self.prompts = []
        self.ready = ready

    def describe(self):
        return {"model": "test-judge", "provider": "test", "ready": self.ready, "reason": "Configure credentials"}

    def complete(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith("Explain the supplied MAST judgments"):
            text = json.dumps({"traits": [{"code": c["code"], "explanation": "No localized support in this fixture.",
                                           "occurrences": []} for c in assets()["categories"]]})
            return {"text": text, "model": "test-judge", "finish_reason": "stop"}
        return {"text": self.text, "model": "test-judge", "finish_reason": "stop"}


@pytest.fixture
def service(framework, tmp_path):
    plugins = PluginService(framework, jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    return MastService(plugins, MastPlugin(FakeJudge()), FileArtifacts(tmp_path / "artifacts"))


def app(service):
    return create_app(service.framework, service.artifacts, plugins=service.plugins,
                      extensions=(mast_extension(service),))


def test_bundled_assets_match_pinned_notebook_and_retain_ambiguities():
    from tools.build_mast_assets import build
    assert assets() == build()
    assert len(assets()["categories"]) == 14
    mismatch = [c["code"] for c in assets()["categories"] if c["label"] != c["definition_label"]]
    assert mismatch == ["3.2", "3.3"]
    prompt = make_prompt("UNIQUE_TRACE {examples} {definitions}")
    assert "UNIQUE_TRACE {examples} {definitions}" in prompt
    assert assets()["definitions"] in prompt
    assert assets()["examples"] in prompt


@pytest.mark.parametrize("named", [False, True])
def test_parse_upstream_formats_without_matching_no_in_category_name(named):
    output = parse_response(assessment(named=named))
    assert output["parse_status"] == "complete"
    assert output["task_completed"] is False
    assert [c["code"] for c in output["labels"] if c["present"]] == ["1.3"]


def test_missing_duplicate_and_ambiguous_labels_are_unknown():
    text = assessment().replace("1.1 no", "1.1 yes or no").replace("2.1 no", "") + "\n3.1 yes"
    output = parse_response(text)
    labels = {c["code"]: c["present"] for c in output["labels"]}
    assert labels["1.1"] is labels["2.1"] is labels["3.1"] is None
    assert output["parse_status"] == "needs_review"
    assert len(output["warnings"]) == 3


def test_api_discovery_import_and_analysis_persist_after_restart(service):
    with TestClient(app(service)) as client:
        manifest = client.get("/api/workspace").json()["capabilities"]["web_plugins"][0]
        assert manifest["id"] == "mast" and manifest["modes"] == ["saved_trace"]
        assert "export function install" in client.get(manifest["ui"]["module"]).text
        assert client.get("/api/plugins/mast/taxonomy").json()["revision"] == assets()["revision"]
        trace = client.post("/api/traces", json={"name": "Saved conversation", "text": "A: Done.\nA: Done."})
        assert trace.status_code == 201
        branch = trace.json()["branch"]
        assert not service.plugin.judge.prompts
        submitted = client.post("/api/plugins/mast/analyses", json={"branch_id": branch["id"], "cursor": branch["head"]})
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]
        record = client.get(f"/api/plugins/mast/analyses/{job_id}").json()
        assert record["status"] == "completed"
        assert record["analysis"]["output"]["report"]["human_reviewed"] is False
        assert len(service.plugin.judge.prompts) == 2
        assert service.framework.store.analyses(branch["id"], branch["head"])[0] == record["analysis"]
        assert client.get("/api/plugins/caspian/capabilities").status_code == 404
    with TestClient(app(service)) as client:
        jobs = client.get("/api/plugins/mast/analyses", params={"branch_id": branch["id"]}).json()["jobs"]
        assert jobs[0]["id"] == job_id and jobs[0]["status"] == "completed"
        assert len(service.plugin.judge.prompts) == 2


def test_frozen_prefix_and_fork_never_include_future_parent_events(service, branch):
    child = service.framework.fork(branch.id, 4, "Fork")
    job = service.submit(child.id, 4, "partial")
    service.framework.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": "PRIVATE_FUTURE_SENTINEL"}, 4)
    service.execute(job["id"])
    trace = json.loads(service.artifacts.get(job["trace_artifact"]))["text"]
    assert "PRIVATE_FUTURE_SENTINEL" not in trace
    assert '"content": "future"' not in trace
    assert json.loads(trace)["events"][-1]["position"] == 4
    result = service.jobs.get(job["id"])
    assert result["analysis"]["cursor"] == 4
    assert result["analysis"]["branch_id"] == child.id
    assert result["config"]["completeness"] == "partial"


def test_oversize_trace_rejected_before_provider_call(service, branch):
    service.plugin.max_trace_characters = 5
    with pytest.raises(DomainError, match="Nothing was truncated or sent"):
        service.submit(branch.id, 4, "unknown")
    assert not service.plugin.judge.prompts
    assert not service.jobs.list(branch.id)


def test_preview_checks_frozen_input_without_sending_or_creating_job(service, branch):
    with TestClient(app(service)) as client:
        payload = {"branch_id": branch.id, "cursor": 4, "completeness": "partial"}
        response = client.post("/api/plugins/mast/preview", json=payload)
        assert response.status_code == 200
        assert response.json()["event_count"] == 4
        assert response.json()["can_analyze"]
        service.plugin.max_trace_characters = 5
        response = client.post("/api/plugins/mast/preview", json=payload)
        assert response.status_code == 200
        assert not response.json()["can_analyze"]
        assert not service.jobs.list(branch.id)
        assert not service.plugin.judge.prompts


def test_invalid_provider_output_is_saved_for_review_not_scored_negative(service, branch):
    service.plugin.judge.text = "No valid structured response"
    job = service.submit(branch.id, 4, "unknown")
    service.execute(job["id"])
    result = service.jobs.get(job["id"])
    assert result["status"] == "needs_review"
    report = result["analysis"]["output"]["report"]
    assert all(label["present"] is None for label in report["labels"])
    assert report["raw_response"] == service.plugin.judge.text
    assert result["analysis"]["output"]["annotations"] == []


def test_provider_error_has_no_fabricated_results_or_sensitive_detail(service, branch):
    def fail(prompt):
        raise ValueError("secret-key-or-private-provider-detail")
    service.plugin.judge.complete = fail
    job = service.submit(branch.id, 4, "unknown")
    service.execute(job["id"])
    result = service.jobs.get(job["id"])
    assert result["status"] == "failed" and "analysis" not in result
    assert "secret-key" not in json.dumps(result)


def test_startup_marks_pending_jobs_interrupted_without_repeating_model_call(service, branch):
    queued = service.submit(branch.id, 4, "unknown")
    running = service.submit(branch.id, 4, "unknown")
    service.jobs.claim(running["id"])
    service.jobs.recover_interrupted()
    for job in (queued, running):
        assert service.jobs.get(job["id"])["status"] == "interrupted"
        service.execute(job["id"])
    assert not service.plugin.judge.prompts


def test_job_claim_is_atomic(service, branch):
    job = service.submit(branch.id, 4, "unknown")
    results = []
    threads = [threading.Thread(target=lambda: results.append(service.jobs.claim(job["id"]))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(result is not None for result in results) == 1


def test_validation_disabled_credentials_and_origin_policy(service, branch):
    with TestClient(app(service)) as client:
        url = "/api/plugins/mast/analyses"
        payload = {"branch_id": branch.id, "cursor": 4}
        assert client.post(url, json=payload, headers={"Origin": "https://unrelated.example"}).status_code == 403
        assert client.post(url, json={**payload, "cursor": 999}).status_code == 400
        assert client.post(url, json={**payload, "live": True}).status_code == 422
        assert client.post("/api/traces", json={"name": " ", "text": ""}).status_code == 422
        service.plugin.judge.ready = False
        assert client.post(url, json=payload).status_code == 400
        assert not service.plugin.judge.prompts


def test_duplicate_extension_ids_rejected(service):
    extension = mast_extension(service)
    with pytest.raises(ValueError, match="Duplicate"):
        create_app(service.framework, extensions=(extension, extension))
