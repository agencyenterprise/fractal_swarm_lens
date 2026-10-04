"""Change points through the real app: markers with their delay, the cross-agent band, streaming parity."""
from datetime import datetime, timedelta, timezone
import random

from fastapi.testclient import TestClient
import pytest

from swarm_lens import Fact, PluginService
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.plugins import Annotation, Metric
from swarm_lens.plugins.change_points import ChangePoints, OnlineDetector, Params
from swarm_lens.web.api import create_app
from tests.conftest import Facts

START = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
ROUNDS, SWITCH = 16, 8


def debate(framework, *, switching=("a", "b"), steady=("c",), seed=0, blank=None):
    """Agents write one message per round. `switching` agents write ~5x longer messages from round SWITCH on."""
    rng = random.Random(seed)
    agents = [*switching, *steady]
    facts = [Fact("agent.added", {"id": agent, "name": agent.upper()}, START.isoformat()) for agent in agents]
    facts.append(Fact("channel.created", {"id": "c1", "name": "Debate", "members": agents}, START.isoformat()))
    for round_ in range(ROUNDS):
        for offset, agent in enumerate(agents):
            words = rng.randint(18, 22) * (5 if agent in switching and round_ >= SWITCH else 1)
            facts.append(Fact("message.created", {
                "id": f"{agent}-{round_}", "channel_id": "c1", "sender_id": agent,
                "content": "" if (agent, round_) == blank else "word " * words,
                "metadata": {"round": round_}}, (START + timedelta(minutes=round_, seconds=offset)).isoformat()))
    run = framework.create_run("Debate")
    framework.ingest(run.id, Facts(*facts), batch_size=50)
    return framework.store.branch(run.id)


def position(framework, branch, message_id):
    return next(e.position for e in framework.history(branch.id) if e.data.get("id") == message_id)


@pytest.fixture
def client(framework, tmp_path):
    service = PluginService(framework, (ChangePoints(),),
                            jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    return TestClient(create_app(framework, plugins=service))


def analyze(client, branch, params=None, start=1):
    response = client.post(f"/api/branches/{branch.id}/analyses",
                           json={"plugin_id": "change-points", "start": start, "end": branch.head, "params": params or {}})
    assert response.status_code == 202, response.text
    return client.get(f"/api/analyses/change-points/{response.json()['id']}").json()


def test_verbosity_shift_is_marked_with_its_delay_and_a_band(framework, client):
    branch = debate(framework)
    job = analyze(client, branch)
    assert job["status"] == "completed", job.get("error")
    annotations = client.get(f"/api/branches/{branch.id}/annotations").json()["annotations"]
    changes = {a["agent_id"]: a for a in annotations if a["agent_id"]}
    assert set(changes) == {"a", "b"}
    for agent, change in changes.items():
        assert change["seq_from"] == position(framework, branch, f"{agent}-{SWITCH}")
        delay = change["data"]["delay"]["messages"]
        assert delay >= 1 and change["seq_to"] == position(framework, branch, f"{agent}-{SWITCH + delay}")
        assert change["label"].startswith("Verbosity shift ×") and change["score"] > 3
        assert change["cited_event_ids"][0] == framework.history(branch.id)[change["seq_from"] - 1].id
    [band] = [a for a in annotations if a["agent_id"] is None]
    assert band["data"]["agents"] == ["a", "b"] and band["data"]["round"] == SWITCH
    series = {(s["name"], s["agent_id"]) for s in client.get(f"/api/branches/{branch.id}/series").json()["series"]}
    assert {("characters", "c"), ("messages_since_change", "a")} <= series
    assert job["analysis"]["output"]["report"]["changes"] == 2


def test_streaming_batches_emit_exactly_what_one_analysis_finds(framework):
    branch = debate(framework, seed=3)
    service, plugin, params = PluginService(framework), ChangePoints(), Params()
    view = service.view(branch.id, branch.head)
    whole = [f for f in plugin.analyze(view, 1, branch.head, params) if isinstance(f, Metric | Annotation)]
    events = view.events()
    streamed = [f for offset in range(0, len(events), 7) for f in plugin.on_events(view, events[offset:offset + 7], params)]
    assert streamed == whole and any(isinstance(f, Annotation) for f in streamed)


def test_a_partial_range_keeps_only_findings_inside_it(framework, client):
    branch = debate(framework)
    cut = position(framework, branch, f"a-{SWITCH + 1}")
    job = analyze(client, branch, start=cut)
    assert job["status"] == "completed"
    output = job["analysis"]["output"]
    assert all(m["seq"] >= cut for m in output["metrics"])
    assert all(a["seq_from"] >= cut for a in output["annotations"])


def test_online_alarms_do_not_repeat_a_revised_boundary():
    rng = random.Random(1)
    for _ in range(200):
        detector, reported = OnlineDetector(1 / 30, 1e-2, 2), []
        level = 0.0
        for _ in range(30):
            level += rng.choice([0, 0, 0, 0.6, 1.5])
            boundary = detector.update((level + rng.gauss(0, 0.3),))
            if boundary:
                reported.append(boundary)
        starts = [b.start for b in reported]
        assert all(later - earlier >= 2 for earlier, later in zip(starts, starts[1:]))
        assert all(b.detected - b.start >= 1 for b in reported)


def test_content_signal_needs_an_encoder_and_skips_empty_messages(framework, tmp_path):
    branch = debate(framework, blank=("c", 3))
    no_encoder = TestClient(create_app(framework, plugins=PluginService(
        framework, (ChangePoints(),), jobs=lambda name, label: JobStore(tmp_path / "a.sqlite", name, label))))
    failed = analyze(no_encoder, branch, {"signal": "content"})
    assert failed["status"] == "failed" and "OPENAI_API_KEY" in failed["error"]

    class LengthEncoder:
        calls = 0
        def encode(self, texts):
            LengthEncoder.calls += 1
            assert all(text.strip() for text in texts)
            return [(len(text) / 100.0, 1.0) for text in texts]

    plugin = ChangePoints(LengthEncoder)
    view = PluginService(framework).view(branch.id, branch.head)
    found = list(plugin.analyze(view, 1, branch.head, Params(signal="content")))
    list(plugin.analyze(view, 1, branch.head, Params(signal="content")))
    assert {f.agent_id for f in found if isinstance(f, Annotation) and f.agent_id} == {"a", "b"}
    assert LengthEncoder.calls == 1


def test_params_reject_unknown_signals(framework, client):
    branch = debate(framework)
    response = client.post(f"/api/branches/{branch.id}/analyses",
                           json={"plugin_id": "change-points", "end": branch.head, "params": {"signal": "tone"}})
    assert response.status_code == 400 and "signal" in response.json()["detail"]
