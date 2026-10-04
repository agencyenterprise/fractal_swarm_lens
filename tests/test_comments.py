import json

from fastapi.testclient import TestClient
import pytest

from swarm_lens import Framework
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.application import bundle as bundle_format
from swarm_lens.web import api
from swarm_lens.web.api import create_app


@pytest.fixture
def client(framework):
    return TestClient(create_app(framework))


def event_id(framework, branch_id, position):
    return framework.history(branch_id, position)[-1].id


def post_comment(client, branch_id, event, text, *, author="Ada", parent_id=None):
    response = client.post(f"/api/branches/{branch_id}/comments",
                           json={"event_id": event, "author": author, "text": text, "parent_id": parent_id})
    assert response.status_code == 201, response.text
    return response.json()


def texts(client, branch_id):
    return [comment["text"] for comment in client.get(f"/api/branches/{branch_id}/comments").json()["comments"]]


def test_comment_thread_lifecycle(framework, branch, client):
    anchor = event_id(framework, branch.id, 3)
    thread = post_comment(client, branch.id, anchor, "The channel opens here", author="  Ada  ")
    assert thread["position"] == 3 and thread["author"] == "Ada" and thread["resolved"] is False
    assert thread["updated_at"] is None and thread["parent_id"] is None
    reply = post_comment(client, branch.id, anchor, "Agreed", author="Grace", parent_id=thread["id"])
    assert reply["parent_id"] == thread["id"] and reply["position"] == 3
    post_comment(client, branch.id, event_id(framework, branch.id, 1), "Goal is set")
    assert texts(client, branch.id) == ["Goal is set", "The channel opens here", "Agreed"]

    resolved = client.patch(f"/api/comments/{thread['id']}", json={"resolved": True}).json()
    assert resolved["resolved"] is True and resolved["text"] == "The channel opens here" and resolved["updated_at"]
    edited = client.patch(f"/api/comments/{thread['id']}", json={"text": "The channel opens at 3"}).json()
    assert edited["resolved"] is True and edited["text"] == "The channel opens at 3"
    assert client.patch(f"/api/comments/{reply['id']}", json={"resolved": True}).status_code == 400
    assert client.patch(f"/api/comments/{thread['id']}", json={}).status_code == 400

    assert client.delete(f"/api/comments/{thread['id']}").status_code == 204
    assert texts(client, branch.id) == ["Goal is set"]
    assert client.delete(f"/api/comments/{reply['id']}").status_code == 400
    assert framework.store.branch(branch.id).head == 7


def test_comment_validation(framework, branch, client):
    child = framework.fork(branch.id, 4, "child")
    after_fork = event_id(framework, branch.id, 6)
    thread = post_comment(client, branch.id, event_id(framework, branch.id, 2), "Agent joins")
    reply = post_comment(client, branch.id, thread["event_id"], "Yes", parent_id=thread["id"])

    def rejected(branch_id, **changes):
        body = {"event_id": thread["event_id"], "author": "Ada", "text": "note", **changes}
        response = client.post(f"/api/branches/{branch_id}/comments", json=body)
        assert response.status_code == 400, response.text
        return response.json()["detail"]

    assert "branch's history" in rejected(branch.id, event_id="unknown-event")
    assert "branch's history" in rejected(child.id, event_id=after_fork)
    assert "top-level" in rejected(branch.id, parent_id=reply["id"])
    assert "thread's event" in rejected(branch.id, parent_id=thread["id"], event_id=after_fork)
    assert "nonempty text" in rejected(branch.id, text="   ")
    assert "nonempty text" in rejected(branch.id, text="x" * 10_001)
    assert "author" in rejected(branch.id, author=" ")
    assert "author" in rejected(branch.id, author="a" * 81)
    assert client.get("/api/branches/unknown/comments").status_code == 400


def test_branch_sees_ancestor_comments_only_up_to_its_fork(framework, branch, client):
    post_comment(client, branch.id, event_id(framework, branch.id, 3), "Before the fork")
    late = post_comment(client, branch.id, event_id(framework, branch.id, 6), "After the fork")
    child = framework.fork(branch.id, 4, "child")
    framework.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": "Verify"}, 4)
    framework.intervene(child.id, "environment.updated", {"goal": "Verify first"}, 5)
    framework.intervene(child.id, "environment.updated", {"goal": "Verify twice"}, 6)
    on_inherited = post_comment(client, child.id, event_id(framework, child.id, 2), "Child note on inherited event")
    post_comment(client, child.id, event_id(framework, child.id, 5), "Child's own event")
    shared = client.get(f"/api/branches/{child.id}/comments").json()["comments"][1]
    post_comment(client, child.id, shared["event_id"], "Child answers the parent", parent_id=shared["id"])
    grandchild = framework.fork(child.id, 3, "grandchild")

    assert texts(client, branch.id) == ["Before the fork", "After the fork"]
    assert texts(client, child.id) == ["Child note on inherited event", "Before the fork",
                                       "Child answers the parent", "Child's own event"]
    assert texts(client, grandchild.id) == ["Child note on inherited event", "Before the fork",
                                            "Child answers the parent"]
    assert on_inherited["branch_id"] == child.id
    reply = {"event_id": late["event_id"], "author": "Ada", "text": "hidden", "parent_id": late["id"]}
    assert client.post(f"/api/branches/{child.id}/comments", json=reply).status_code == 400


def build_shared_run(framework, client, branch):
    child = framework.fork(branch.id, 5, "Prompt change")
    framework.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": "Check your work"}, 5)
    grandchild = framework.fork(child.id, 6, "Removal")
    framework.intervene(grandchild.id, "agent.removed", {"id": "a"}, 6)
    framework.intervene(branch.id, "environment.updated", {"goal": "Later parent goal"}, 7)
    thread = post_comment(client, branch.id, event_id(framework, branch.id, 4), "First message", author="Ada")
    post_comment(client, branch.id, thread["event_id"], "Root reply", author="Grace", parent_id=thread["id"])
    post_comment(client, child.id, thread["event_id"], "Child reply", author="Linus", parent_id=thread["id"])
    client.patch(f"/api/comments/{thread['id']}", json={"resolved": True})
    post_comment(client, child.id, event_id(framework, child.id, 6), "Prompt lands here", author="Ada")
    post_comment(client, grandchild.id, event_id(framework, grandchild.id, 1), "Goal at start", author="Grace")
    post_comment(client, branch.id, event_id(framework, branch.id, 8), "Parent only", author="Ada")


def run_snapshot(framework, run_id):
    """Everything a teammate should see after import, expressed without database ids."""
    branches = framework.store.branches(run_id)
    names = {branch.id: branch.name for branch in branches}
    comments = {comment.id: comment for branch in branches for comment in framework.comments(branch.id)}

    def comment_view(comment):
        return (names[comment.branch_id], comment.position, comment.author, comment.text, comment.created_at,
                comment.updated_at, comment.resolved, comments[comment.parent_id].text if comment.parent_id else None)

    def state_view(branch_id):
        state = framework.state(branch_id).to_dict()
        del state["branch_id"]
        return state

    return {branch.name: {
        "parent": names.get(branch.parent_id), "fork_position": branch.fork_position, "head": branch.head,
        "events": [(event.kind, event.data, event.occurred_at, event.source) for event in framework.history(branch.id)],
        "state": state_view(branch.id),
        "comments": [comment_view(comment) for comment in framework.comments(branch.id)],
    } for branch in branches}


def test_export_import_round_trip_preserves_history_branches_and_threads(framework, branch, client, tmp_path):
    build_shared_run(framework, client, branch)
    response = client.get(f"/api/runs/{branch.run_id}/export")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="Experiment.swarm-lens.json"')
    bundle = response.json()
    assert (bundle["format"], bundle["version"]) == ("swarm-lens.run", 1)
    assert [len(entry["events"]) for entry in bundle["branches"]] == [8, 1, 1]

    other = Framework(SQLiteHistory(tmp_path / "teammate" / "history.sqlite"))
    imported = TestClient(create_app(other)).post("/api/runs/import", json=bundle)
    assert imported.status_code == 201, imported.text
    root = imported.json()["branch"]
    assert root["name"] == "Recorded" and root["parent_id"] is None
    run = other.store.run(root["run_id"])
    assert (run.name, run.metadata) == ("Experiment", framework.store.run(branch.run_id).metadata)

    original = run_snapshot(framework, branch.run_id)
    assert run_snapshot(other, run.id) == original
    assert [view[3] for view in original["Recorded"]["comments"]] == ["First message", "Root reply", "Parent only"]
    assert [view[3] for view in original["Removal"]["comments"]] == [
        "Goal at start", "First message", "Root reply", "Child reply", "Prompt lands here"]
    assert original["Removal"]["comments"][1][6] is True

    again = client.post("/api/runs/import", json=bundle)
    assert again.status_code == 201
    assert run_snapshot(framework, again.json()["branch"]["run_id"]) == original


def test_import_rejects_bad_bundles_without_writing(framework, branch, client, monkeypatch):
    bundle = client.get(f"/api/runs/{branch.run_id}/export").json()
    child = framework.fork(branch.id, 2, "child")
    framework.intervene(child.id, "environment.updated", {"goal": "fork"}, 2)
    valid = client.get(f"/api/runs/{branch.run_id}/export").json()
    runs = framework.store.runs()

    unknown_version = client.post("/api/runs/import", json={**bundle, "version": 2})
    assert unknown_version.status_code == 400 and "Unsupported run bundle" in unknown_version.json()["detail"]
    valid["branches"][1]["events"].append({"kind": "message.created", "occurred_at": "2026-01-01T12:00:00+00:00",
                                           "data": {"id": "x", "channel_id": "missing", "content": "x"}, "source": {}})
    invalid_child_event = client.post("/api/runs/import", json=valid)
    assert invalid_child_event.status_code == 400 and "channel" in invalid_child_event.json()["detail"]
    bundle["comments"] = [{"branch_key": "branch-0", "event_position": 8, "author": "Ada", "text": "late",
                           "created_at": "2026-01-01T12:00:00Z", "resolved": False, "replies": []}]
    assert client.post("/api/runs/import", json=bundle).status_code == 400
    assert client.post("/api/runs/import", content=b"{not json").status_code == 400
    non_finite = json.dumps(valid).replace('"source": {}', '"source": {"score": 1e400}', 1)
    assert client.post("/api/runs/import", content=non_finite).status_code == 400
    monkeypatch.setattr(api, "BUNDLE_LIMIT_BYTES", 64)
    assert client.post("/api/runs/import", json=bundle).status_code == 413
    assert framework.store.runs() == runs


@pytest.mark.parametrize("limit, message", [("MAX_EVENTS", "events"), ("MAX_BRANCHES", "branches"),
                                            ("MAX_FORK_DEPTH", "nest")])
def test_import_limits_are_checked_before_replay(framework, branch, client, monkeypatch, limit, message):
    child = framework.fork(branch.id, 3, "child")
    framework.fork(child.id, 2, "grandchild")
    bundle = client.get(f"/api/runs/{branch.run_id}/export").json()
    monkeypatch.setattr(bundle_format, limit, {"MAX_EVENTS": 6, "MAX_BRANCHES": 2, "MAX_FORK_DEPTH": 1}[limit])

    def replay_must_not_start(*args):
        raise AssertionError("the reducer ran before the limits were checked")

    monkeypatch.setattr(Framework, "_event", staticmethod(replay_must_not_start))
    runs = framework.store.runs()
    response = client.post("/api/runs/import", json=bundle)
    assert response.status_code == 400 and message in response.json()["detail"]
    assert framework.store.runs() == runs
