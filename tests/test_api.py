from fastapi.testclient import TestClient

from swarm_lens.web.api import create_app


def test_api_scrubs_forks_and_compares(framework, branch):
    client = TestClient(create_app(framework))
    response = client.get(f"/api/branches/{branch.id}/state", params={"cursor": 4})
    assert response.json()["counts"]["messages"] == 1
    child = client.post(f"/api/branches/{branch.id}/fork", json={"cursor": 4, "name": "api fork"}).json()
    response = client.post(f"/api/branches/{child['id']}/interventions", json={
        "kind": "agent.updated", "data": {"id": "a", "system_prompt": "new"}, "expected_head": 4,
    })
    assert response.status_code == 200
    diff = client.get("/api/compare", params={"left": branch.id, "right": child["id"], "left_cursor": 4}).json()
    assert diff["agents"]["changed"][0]["after"]["system_prompt"] == "new"
    assert client.post(f"/api/branches/{child['id']}/interventions", json={
        "kind": "agent.updated", "data": {"id": "a", "name": "stale"}, "expected_head": 4,
    }).status_code == 409


def test_event_detail_cannot_leak_future(framework, branch):
    client = TestClient(create_app(framework))
    future = framework.history(branch.id)[-1]
    assert client.get(f"/api/branches/{branch.id}/events/{future.id}", params={"cursor": 4}).status_code == 400
    child = framework.fork(branch.id, 4, "child")
    assert client.get(f"/api/branches/{child.id}/events/{future.id}", params={"cursor": 4}).status_code == 400


def test_foreign_origin_cannot_mutate_local_state(framework, branch):
    client = TestClient(create_app(framework))
    assert client.post(f"/api/branches/{branch.id}/fork", json={"cursor": 4, "name": "bad"},
                       headers={"Origin": "https://unrelated.example"}).status_code == 403
    for method in ("PATCH", "DELETE"):
        assert client.request(method, "/api/comments/any", json={"resolved": True},
                              headers={"Origin": "https://unrelated.example"}).status_code == 403


def test_raw_transcript_import_keeps_the_text_without_interpreting_it(framework):
    client = TestClient(create_app(framework))
    assert client.post("/api/traces", json={"name": " ", "text": ""}).status_code == 422
    response = client.post("/api/traces", json={"name": "Pasted chat", "text": "A: hi\nB: hello", "task": "Greet"})
    assert response.status_code == 201
    branch = response.json()["branch"]
    run = next(run for run in framework.store.runs() if run.id == branch["run_id"])
    assert run.metadata["source_type"] == "saved_trace"
    history = framework.history(branch["id"])
    assert [event.kind for event in history] == ["environment.updated", "observation.recorded"]
    assert history[1].data["content"] == "A: hi\nB: hello" and history[0].data["task"] == "Greet"
