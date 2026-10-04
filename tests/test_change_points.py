"""Change points through the real app: markers with their delay, bands, every signal, streaming parity."""
from datetime import datetime, timedelta, timezone
import random

from fastapi.testclient import TestClient
import pytest

from swarm_lens import Fact, PluginService
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.plugins import Annotation, Metric
from swarm_lens.plugins.change_points import ChangePoints, OnlineDetector, Params, create
from swarm_lens.web.api import create_app
from tests.conftest import Facts

START = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
ROUNDS, SWITCH = 16, 8


def debate(framework, *, switching=("a", "b"), steady=("c",), seed=0, blank=None, rounds=True):
    """Agents write one message per round, a minute apart. `switching` agents write ~5x longer messages from
    round SWITCH on. 4 setup events, then 3 messages per round, so a-r is at position 5 + 3r."""
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
                "metadata": {"round": round_} if rounds else {}},
                (START + timedelta(minutes=round_, seconds=offset)).isoformat()))
    return ingest(framework, facts)


def solo(framework, gaps, recorded=None):
    """One agent added at START, then one message after each gap (seconds), each right after a memory write by the
    same agent, which latency must ignore; `recorded` maps message index -> recorded latency."""
    facts = [Fact("agent.added", {"id": "a", "name": "A"}, START.isoformat()),
             Fact("channel.created", {"id": "c1", "name": "Solo", "members": ["a"]}, START.isoformat())]
    at = START
    for index, gap in enumerate(gaps):
        at += timedelta(seconds=gap)
        metadata = {"latency_seconds": recorded[index]} if recorded and index in recorded else {}
        facts.append(Fact("memory.written", {"id": "notes", "owner_id": "a", "content": str(index)},
                          (at - timedelta(seconds=1)).isoformat()))
        facts.append(Fact("message.created", {"id": f"m{index}", "channel_id": "c1", "sender_id": "a",
                                              "content": "same length", "metadata": metadata}, at.isoformat()))
    return ingest(framework, facts)


def ingest(framework, facts):
    run = framework.create_run("Fixture")
    framework.ingest(run.id, Facts(*facts), batch_size=50)
    return framework.store.branch(run.id)


def position(framework, branch, message_id):
    return next(e.position for e in framework.history(branch.id) if e.data.get("id") == message_id)


def client_for(framework, tmp_path, plugin):
    service = PluginService(framework, (plugin,),
                            jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    return TestClient(create_app(framework, plugins=service))


@pytest.fixture
def client(framework, tmp_path):
    return client_for(framework, tmp_path, create(None))


def analyze(client, branch, params=None, start=1):
    response = client.post(f"/api/branches/{branch.id}/analyses",
                           json={"plugin_id": "change-points", "start": start, "end": branch.head, "params": params or {}})
    assert response.status_code == 202, response.text
    return client.get(f"/api/analyses/change-points/{response.json()['id']}").json()


def annotations(client, branch):
    return client.get(f"/api/branches/{branch.id}/annotations").json()["annotations"]


def test_verbosity_shift_is_marked_with_its_delay_and_a_band(framework, client):
    branch = debate(framework)
    job = analyze(client, branch)
    assert job["status"] == "completed", job.get("error")
    found = annotations(client, branch)
    changes = {a["agent_id"]: a for a in found if a["agent_id"]}
    assert set(changes) == {"a", "b"}
    for agent, change in changes.items():
        assert change["seq_from"] == position(framework, branch, f"{agent}-{SWITCH}")
        delay = change["data"]["delay"]["messages"]
        assert delay >= 1 and change["seq_to"] == position(framework, branch, f"{agent}-{SWITCH + delay}")
        assert change["label"].startswith("Verbosity shift ×") and change["score"] > 3
        assert "probability" not in change["data"] and 0 < change["data"]["run_length_mass"] <= 1
        assert change["cited_event_ids"][0] == framework.history(branch.id)[change["seq_from"] - 1].id
    [band] = [a for a in found if a["agent_id"] is None]
    assert band["data"]["agents"] == ["a", "b"] and band["data"]["round"] == SWITCH
    series = {(s["name"], s["agent_id"]) for s in client.get(f"/api/branches/{branch.id}/series").json()["series"]}
    assert {("characters", "c"), ("messages_since_change", "a")} <= series
    assert job["analysis"]["output"]["report"]["changes"] == 2


@pytest.mark.parametrize(("window", "banded"), [(10, True), (29, False)])
def test_without_rounds_bands_use_one_based_blocks_of_events(framework, client, window, banded):
    branch = debate(framework, rounds=False)
    assert [position(framework, branch, f"{agent}-{SWITCH}") for agent in "ab"] == [29, 30]
    assert analyze(client, branch, {"band_window": window})["status"] == "completed"
    assert any(a["agent_id"] is None for a in annotations(client, branch)) is banded


def test_latency_falls_back_to_the_gap_since_the_agents_previous_message(framework, client):
    branch = solo(framework, [60] * 8 + [600] * 8, recorded={3: 61.0})
    job = analyze(client, branch, {"signal": "latency"})
    assert job["status"] == "completed", job.get("error")
    [series] = [s for s in client.get(f"/api/branches/{branch.id}/series").json()["series"]
                if s["name"] == "latency_seconds"]
    assert [value for _, value in series["points"]] == [60.0] * 3 + [61.0] + [60.0] * 4 + [600.0] * 8
    [change] = annotations(client, branch)
    assert change["seq_from"] == position(framework, branch, "m8") and change["label"].startswith("Latency shift ×")


def test_one_transition_with_a_revised_boundary_is_reported_once():
    """Raw MAP starts here move 9 -> 10 after the first report; that revision is not a second change."""
    series = [0.17, -0.08, 0.22, -0.04, -0.63, 0.14, -0.22, 0.28, 0.4, 0.65,
              2.18, 1.98, 2.24, 2.24, 1.49, 1.94, 1.78, 2.33, 2.11, 2.18]
    assert [b.start for b in run_detector(series)] == [9]


def test_two_real_transitions_are_both_reported():
    rng = random.Random(4)
    series = [level + rng.gauss(0, 0.3) for level in [0] * 8 + [2] * 8 + [0] * 8]
    assert [b.start for b in run_detector(series)] == [8, 16]


def run_detector(series):
    detector = OnlineDetector(1 / 30, 1e-2, 2)
    return [boundary for x in series if (boundary := detector.update((x,)))]


def test_streaming_batches_emit_exactly_what_one_analysis_finds(framework):
    branch = debate(framework, seed=3)
    plugin, params = ChangePoints(), Params()
    view = PluginService(framework).view(branch.id, branch.head)
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


def test_content_without_credentials_fails_with_a_message_the_user_can_act_on(framework, client, tmp_path,
                                                                              monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    failed = analyze(client, debate(framework), {"signal": "content"})
    assert failed["status"] == "failed" and "OPENAI_API_KEY" in failed["error"]


def test_content_signal_marks_shifts_skips_empty_messages_and_memoizes(framework, tmp_path):
    class LengthEncoder:
        calls = 0

        def encode(self, texts):
            LengthEncoder.calls += 1
            assert all(text.strip() for text in texts)
            return [(len(text) / 100.0, 1.0) for text in texts]

    branch = debate(framework, blank=("c", 3))
    client = client_for(framework, tmp_path, ChangePoints(LengthEncoder))
    for _ in range(2):
        assert analyze(client, branch, {"signal": "content"})["status"] == "completed"
    found = annotations(client, branch)
    assert {a["agent_id"] for a in found if a["agent_id"]} == {"a", "b"}
    assert all(a["label"] == "Content shift" for a in found if a["agent_id"])
    assert LengthEncoder.calls == 1


def test_params_reject_unknown_signals(framework, client):
    branch = debate(framework)
    response = client.post(f"/api/branches/{branch.id}/analyses",
                           json={"plugin_id": "change-points", "end": branch.head, "params": {"signal": "tone"}})
    assert response.status_code == 400 and "signal" in response.json()["detail"]
