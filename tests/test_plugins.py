"""The plugin contract through the real FastAPI app: discovery, params, jobs, findings and interventions."""
from dataclasses import asdict
import sys

from fastapi.testclient import TestClient
import pytest

from examples.plugins.reference import MessageLength, SilenceAgent
from swarm_lens import PluginService
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.cli import build_app
from swarm_lens.plugins import Annotation, Metric
from swarm_lens.plugins.activity import ActivityPlugin
from swarm_lens.web.api import create_app


def client_for(framework, tmp_path, *plugins):
    service = PluginService(framework, plugins, jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    return TestClient(create_app(framework, plugins=service))


def run(client, branch_id, plugin_id, end, params=None, start=1):
    response = client.post(f"/api/branches/{branch_id}/analyses",
                           json={"plugin_id": plugin_id, "start": start, "end": end, "params": params or {}})
    assert response.status_code == 202, response.text
    return client.get(f"/api/analyses/{plugin_id}/{response.json()['id']}").json()


def install_entry_point(tmp_path, monkeypatch):
    """A real installed distribution whose entry point names a plugin factory."""
    site = tmp_path / "site"
    (site / "installed_plugin-1.0.dist-info").mkdir(parents=True)
    (site / "installed_plugin-1.0.dist-info" / "METADATA").write_text("Name: installed-plugin\nVersion: 1.0\n")
    (site / "installed_plugin-1.0.dist-info" / "entry_points.txt").write_text(
        "[swarm_lens.plugins]\ncounter = installed_plugin:create\n")
    (site / "installed_plugin.py").write_text(
        "from swarm_lens.plugins import Metric\n"
        "class Counter:\n"
        "    id, version = 'event-counter', '1'\n"
        "    def analyze(self, view, start, end, params):\n"
        "        return [Metric(end, 'events', end - start + 1)]\n"
        "def create(services):\n"
        "    return Counter()\n")
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delitem(sys.modules, "installed_plugin", raising=False)


def test_discovery_from_entry_points_and_flags_with_params_schema(tmp_path, monkeypatch):
    install_entry_point(tmp_path, monkeypatch)
    with TestClient(build_app(tmp_path, plugin_specs=["examples.plugins.reference:create"])) as client:
        plugins = {plugin["id"]: plugin for plugin in client.get("/api/plugins").json()["plugins"]}
        assert list(plugins) == ["activity", "event-counter", "message-length", "silence-agent"]
        assert plugins["message-length"]["capabilities"] == ["analyzer"]
        assert plugins["silence-agent"]["capabilities"] == ["intervention"]
        assert plugins["message-length"]["params_schema"]["properties"]["long_message"]["default"] == 500
        assert plugins["event-counter"]["params_schema"] == {"type": "object", "properties": {}}
        assert client.get("/api/workspace").json()["capabilities"]["web_plugins"][0]["id"] == "mast"
    with pytest.raises(ValueError, match="Duplicate plugin ID: message-length"):
        build_app(tmp_path / "again", plugin_specs=["examples.plugins.reference:create"] * 2)


def test_params_are_validated_before_a_job_exists(framework, branch, tmp_path):
    client = client_for(framework, tmp_path, MessageLength())
    url = f"/api/branches/{branch.id}/analyses"
    bad = client.post(url, json={"plugin_id": "message-length", "end": 7, "params": {"long_message": 0}})
    assert bad.status_code == 400 and "long_message" in bad.json()["detail"]
    assert client.post(url, json={"plugin_id": "message-length", "end": 7, "params": {"typo": 1}}).status_code == 400
    assert client.post(url, json={"plugin_id": "message-length", "end": 8}).status_code == 400
    assert client.post(url, json={"plugin_id": "missing", "end": 7}).status_code == 400
    assert client.get(url).json() == {"jobs": []}


def test_analyzer_job_feeds_series_annotations_and_forks_that_share_its_input(framework, branch, tmp_path):
    client = client_for(framework, tmp_path, MessageLength())
    job = run(client, branch.id, "message-length", 7, {"long_message": 5})
    assert job["status"] == "completed" and job["config"] == {"long_message": 5}
    series = client.get(f"/api/branches/{branch.id}/series").json()["series"]
    assert series == [{"plugin": "message-length", "name": "characters", "agent_id": "a",
                       "points": [[4, 5.0], [7, 6.0]], "downsampled": False}]
    annotations = client.get(f"/api/branches/{branch.id}/annotations", params={"from": 5}).json()["annotations"]
    event_id = framework.history(branch.id)[6].id
    assert annotations == [{"seq_from": 7, "seq_to": 7, "label": "Long message", "agent_id": "a", "score": 1.2,
                            "data": {"characters": 6}, "cited_event_ids": [event_id], "plugin": "message-length"}]
    assert framework.store.analyses(branch.id, 7)[0] == job["analysis"]

    late, early = framework.fork(branch.id, 7, "after"), framework.fork(branch.id, 4, "before")
    assert len(client.get(f"/api/branches/{late.id}/annotations").json()["annotations"]) == 1
    assert client.get(f"/api/branches/{early.id}/series").json()["series"] == []
    run(client, branch.id, "message-length", 7, {"long_message": 100})
    assert client.get(f"/api/branches/{branch.id}/annotations").json()["annotations"] == []


def test_activity_counts_become_per_agent_series(framework, branch, tmp_path):
    client = client_for(framework, tmp_path, ActivityPlugin())
    run(client, branch.id, "activity", 7)
    assert client.get(f"/api/branches/{branch.id}/series", params={"name": "messages"}).json()["series"] == [
        {"plugin": "activity", "name": "messages", "agent_id": "a", "points": [[4, 1.0], [7, 2.0]], "downsampled": False}]


def test_series_downsampling_keeps_extremes(framework, branch, tmp_path):
    class Wave:
        id, version = "wave", "1"
        def analyze(self, view, start, end, params):
            return [Metric(seq, "wave", 100 if seq == 5 else seq % 2) for seq in range(start, end + 1)]
    client = client_for(framework, tmp_path, Wave())
    run(client, branch.id, "wave", 7)
    [series] = client.get(f"/api/branches/{branch.id}/series", params={"max_points": 4}).json()["series"]
    assert series["downsampled"] and len(series["points"]) <= 4 and [5, 100.0] in series["points"]


def test_intervention_forks_with_returned_events_and_never_touches_the_parent(framework, branch, tmp_path):
    client = client_for(framework, tmp_path, SilenceAgent())
    url = f"/api/branches/{branch.id}/interventions/silence-agent"
    response = client.post(url, json={"at": 4, "name": "Without A", "params": {"agent_id": "a"}})
    assert response.status_code == 201, response.text
    child = response.json()
    assert (child["parent_id"], child["fork_position"], child["head"]) == (branch.id, 4, 5)
    added = framework.history(child["id"])[-1]
    assert added.kind == "agent.removed" and added.source["plugin"] == "silence-agent"
    assert added.source["origin"] == "intervention" and added.source["params"] == {"agent_id": "a"}
    assert not framework.state(child["id"]).agents["a"].active
    assert framework.state(branch.id).agents["a"].active and framework.store.branch(branch.id).head == 7

    branches = len(framework.store.branches(branch.run_id))
    refused = client.post(url, json={"at": 1, "name": "Too early", "params": {"agent_id": "a"}})
    assert refused.status_code == 400 and "No active agent" in refused.json()["detail"]
    assert len(framework.store.branches(branch.run_id)) == branches


def test_plugins_cannot_change_history_and_malformed_results_fail_loudly(framework, branch, tmp_path):
    class Vandal:
        id, version = "vandal", "1"
        def analyze(self, view, start, end, params):
            view.state_at(end).agents["a"].name = "renamed"
            view.events(start, end)[0].data["goal"] = "rewritten"
            return [Metric(end + 1, "beyond", 1)]
    client = client_for(framework, tmp_path, Vandal())
    before = [asdict(event) for event in framework.history(branch.id)]
    job = run(client, branch.id, "vandal", 4)
    assert job["status"] == "failed" and "position from 1 to 4" in job["error"] and "analysis" not in job
    assert [asdict(event) for event in framework.history(branch.id)] == before
    assert framework.state(branch.id).agents["a"].name == "Agent A"
    assert framework.store.analyses(branch.id, 4) == []
    assert not hasattr(PluginService(framework).view(branch.id, 4), "fork")


def test_streaming_analyzer_runs_on_demand_and_views_read_earlier_findings(framework, branch, tmp_path):
    class Running:
        id, version = "running", "1"
        def __init__(self):
            self.batches = []
        def on_events(self, view, events, params):
            self.batches.append([event.position for event in events])
            return [Metric(event.position, "seen", event.position) for event in events]

    class Echo:
        id, version = "echo", "1"
        def analyze(self, view, start, end, params):
            return [Annotation(metric.seq, metric.seq, f"seen {metric.value:g}") for metric in view.metrics(plugin="running")]

    running = Running()
    client = client_for(framework, tmp_path, running, Echo())
    run(client, branch.id, "running", 6, start=2)
    assert running.batches == [[2, 3, 4, 5, 6]]
    run(client, branch.id, "echo", 7)
    labels = [item["label"] for item in client.get(f"/api/branches/{branch.id}/annotations",
                                                    params={"plugin": "echo"}).json()["annotations"]]
    assert labels == ["seen 2", "seen 3", "seen 4", "seen 5", "seen 6"]
    jobs = client.get(f"/api/branches/{branch.id}/analyses").json()["jobs"]
    assert [job["plugin_id"] for job in jobs] == ["echo", "running"]
