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


def test_web_plugin_serves_its_own_module_and_owns_only_its_namespace(framework, tmp_path):
    from fastapi import APIRouter
    import pytest
    from swarm_lens.web.extensions import WebExtension

    (tmp_path / "index.js").write_text("export function install(host, manifest) {}")
    router = APIRouter(prefix="/api/plugins/notes")

    @router.get("/hello")
    def hello():
        return {"hello": "notes"}

    extension = WebExtension("notes", router, lambda: {"title": "Notes", "id": "spoofed", "ui": None}, assets=tmp_path)
    client = TestClient(create_app(framework, extensions=(extension,)))
    manifest = client.get("/api/workspace").json()["capabilities"]["web_plugins"][0]
    assert manifest == {"title": "Notes", "id": "notes", "api_prefix": "/api/plugins/notes",
                        "ui": {"module": "/assets/plugins/notes/index.js"}}
    assert client.get(manifest["ui"]["module"]).text.startswith("export function install")
    assert client.get("/api/plugins/notes/hello").json() == {"hello": "notes"}
    assert client.get("/assets/ui.js").status_code == 200

    with pytest.raises(ValueError, match="lowercase"):
        WebExtension("../notes", router, dict)
    with pytest.raises(ValueError, match="index.js"):
        WebExtension("notes", router, dict, assets=tmp_path / "missing")
    stray = APIRouter()
    stray.add_api_route("/api/branches/x", hello)
    with pytest.raises(ValueError, match="must start with /api/plugins/other/"):
        create_app(framework, extensions=(WebExtension("other", stray, dict),))


def notes_plugin(services):
    from fastapi import APIRouter
    from swarm_lens.web.extensions import WebExtension

    router = APIRouter(prefix="/api/plugins/notes")

    @router.get("/runs")
    def runs():
        return {"runs": len(services.framework.store.runs()), "data": str(services.data)}

    return WebExtension("notes", router, lambda: {"title": "Notes"})


def test_cli_composition_serves_bundled_and_configured_plugins(tmp_path):
    from swarm_lens.cli import build_app

    with TestClient(build_app(tmp_path, plugin_specs=[f"{__name__}:notes_plugin"])) as client:
        capabilities = client.get("/api/workspace").json()["capabilities"]
        assert [plugin["id"] for plugin in client.get("/api/plugins").json()["plugins"]] == ["activity", "change-points"]
        assert capabilities["live"]["runtimes"] == ["crewai-trace"]
        mast, notes = capabilities["web_plugins"]
        assert (mast["id"], notes["id"], notes["ui"]) == ("mast", "notes", None)
        assert client.get(mast["ui"]["module"]).status_code == 200
        assert client.get("/api/plugins/notes/runs").json() == {"runs": 0, "data": str(tmp_path)}
        assert client.get("/").status_code == 200
