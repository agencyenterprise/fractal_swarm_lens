"""Influence ribbon through the real app: plugin service, HTTP API, findings, guard and embedding cache."""
from fastapi.testclient import TestClient
import numpy as np
import pytest

from swarm_lens import Fact, Framework, PluginService
from swarm_lens.adapters.git import GitVersions
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.application.framework import IterableSource
from swarm_lens.cli import build_app
from swarm_lens.observability.caspian.estimator import gaussian_cmi
from swarm_lens.observability import influence as method
from swarm_lens.plugins.influence_ribbon import CachedEmbedder, InfluenceRibbon
from swarm_lens.web.api import create_app

AT = "2026-01-01T12:00:00+00:00"
AGENTS = ("a", "b", "c")


class NumberEncoder:
    """Embeds "<agent> says <number>" on one diagonal direction (rank one); counts what it encodes."""
    encoded = 0

    def encode(self, texts):
        NumberEncoder.encoded += len(texts)
        return [(0.6 * float(text.split()[-1]), 0.8 * float(text.split()[-1]), 0.0) for text in texts]


def recorded(framework, name, facts):
    branch = framework.create_run(name)
    framework.ingest(branch.id, IterableSource(facts))
    return framework.store.branch(branch.id)


def debate(rounds=30, seed=0, repeat=None, noise=0.5, deliveries="latest"):
    """Round-robin a -> b -> c; b follows a's message of the same round, plus noise. `deliveries` records the
    latest message of each other agent, every earlier one newest first, or nothing."""
    rng, latest, sent, facts = np.random.default_rng(seed), {}, [], [
        *(Fact("agent.added", {"id": agent, "name": agent.upper()}, AT) for agent in AGENTS),
        Fact("channel.created", {"id": "debate", "name": "Debate", "members": list(AGENTS)}, AT)]
    for round_ in range(rounds):
        for agent in AGENTS:
            if agent == repeat:
                value = 1.0
            elif agent == "b" and "a" in latest:
                value = 0.7 * latest["a"][1] + noise * rng.normal()
            else:
                value = rng.normal()
            message_id = f"{agent}{round_}"
            reads = {"latest": [latest[other][0] for other in AGENTS if other != agent and other in latest],
                     "all": [m for m in reversed(sent) if not m.startswith(agent)], "none": []}[deliveries]
            facts.append(Fact("message.created", {"id": message_id, "channel_id": "debate", "sender_id": agent,
                                                  "content": f"{agent} says {value:.5f}"}, AT,
                              {"delivered_sources": reads}))
            latest[agent] = (message_id, value)
            sent.append(message_id)
    return facts


@pytest.fixture
def app(framework, tmp_path):
    plugin = InfluenceRibbon(CachedEmbedder(tmp_path / "embeddings.sqlite", NumberEncoder, "numbers/v1"))
    service = PluginService(framework, (plugin,), jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    return TestClient(create_app(framework, plugins=service))


def analyze(client, branch_id, end, start=1, params=None):
    response = client.post(f"/api/branches/{branch_id}/analyses",
                           json={"plugin_id": "influence-ribbon", "start": start, "end": end, "params": params or {}})
    assert response.status_code == 202, response.text
    return client.get(f"/api/analyses/influence-ribbon/{response.json()['id']}").json()


def edge(report, source, target):
    return next(c for c in report["couplings"] if (c["source"], c["target"]) == (source, target))


def test_driver_shows_as_directional_coupling_with_gain_tracks_and_pair_annotations(framework, app):
    branch = recorded(framework, "Debate", debate())
    NumberEncoder.encoded = 0
    job = analyze(app, branch.id, branch.head)
    assert job["status"] == "completed", job.get("error")
    report = job["analysis"]["output"]["report"]
    assert report["label"] == "Exploratory predictive coupling, not an attack detector"
    assert report["exposure"] == {"recorded": 90, "assumed": 0}
    driver = edge(report, "a", "b")
    assert driver["n"] == 29 and driver["te_nats"] > 0.5
    others = [c["te_nats"] for c in report["couplings"] if (c["source"], c["target"]) != ("a", "b")]
    assert all(value is not None and value < driver["te_nats"] / 5 for value in others)
    assert "p" not in driver and "p_value" not in driver

    series = app.get(f"/api/branches/{branch.id}/series", params={"name": "gain from a", "agent": "b"}).json()["series"]
    [gain] = series
    assert len(gain["points"]) == 29
    assert np.mean([value for _, value in gain["points"]]) == pytest.approx(driver["te_nats"])

    assert NumberEncoder.encoded == 90
    fork = framework.fork(branch.id, branch.head, "Same history")
    assert analyze(app, fork.id, fork.head)["status"] == "completed"
    assert NumberEncoder.encoded == 90, "a rerun over the same messages must come from the embedding cache"
    pairs = app.get(f"/api/branches/{fork.id}/annotations", params={"plugin": "influence-ribbon"}).json()["annotations"]
    [pair] = [a for a in pairs if a["data"]["source"] == "a" and a["agent_id"] == "b"]
    assert pair["label"].startswith("a → b: ") and pair["score"] == pytest.approx(driver["te_nats"])
    assert len(pair["cited_event_ids"]) == 29 and len(pairs) == 6


def test_repeated_messages_are_reported_as_insufficient_data_not_huge_values(framework, app):
    branch = recorded(framework, "Stubborn", debate(repeat="c"))
    report = analyze(app, branch.id, branch.head)["analysis"]["output"]["report"]
    assert [edge(report, "c", target)["insufficient"] for target in ("a", "b")] == ["the source's messages do not vary"] * 2
    assert report["outgoing"]["c"] == {"te_nats": None, "pairs_reported": 0, "pairs": 2}

    copied = recorded(framework, "Copy", debate(noise=0.0))
    report = analyze(app, copied.id, copied.head)["analysis"]["output"]["report"]
    assert edge(report, "a", "b")["te_nats"] is None
    assert "almost exactly predictable" in edge(report, "a", "b")["insufficient"]

    short = recorded(framework, "Short", debate(rounds=6))
    report = analyze(app, short.id, short.head)["analysis"]["output"]["report"]
    assert {c["te_nats"] for c in report["couplings"]} == {None}
    assert report["couplings"][0]["insufficient"] == "5 target messages, needs at least 12"


def test_exposure_range_and_dimensions_follow_the_trace_not_list_order(framework, app):
    def report(branch, **kwargs):
        job = analyze(app, branch.id, branch.head, **kwargs)
        assert job["status"] == "completed", job.get("error")
        return job["analysis"]["output"]["report"]

    latest = report(recorded(framework, "Latest", debate()))
    every = report(recorded(framework, "Every earlier message", debate(deliveries="all")))
    assert every["couplings"] == latest["couplings"]
    assert report(recorded(framework, "Same", debate()), params={"components": 3})["method"]["effective_components"] == 1

    unread = recorded(framework, "Nobody reads", debate(deliveries="none"))
    nothing = report(unread)
    assert {c["insufficient"] for c in nothing["couplings"]} == {"no target message read the source and every other agent"}
    assert app.get(f"/api/branches/{unread.id}/series").json()["series"] == []
    assert app.get(f"/api/branches/{unread.id}/annotations").json()["annotations"] == []

    branch = recorded(framework, "Late window", debate())
    window = report(branch, start=46)
    assert edge(window, "a", "b")["n"] == 16 and edge(window, "a", "b")["te_nats"] is not None
    ids = {event.id for event in framework.history(branch.id)}
    pairs = app.get(f"/api/branches/{branch.id}/annotations").json()["annotations"]
    assert all(pair["seq_from"] >= 46 and set(pair["cited_event_ids"]) <= ids for pair in pairs)


def test_estimate_equals_gaussian_conditional_mutual_information():
    rng = np.random.default_rng(1)
    x, z, y = rng.normal(size=(41, 1)), rng.normal(size=(41, 1)), rng.normal(size=(41, 1))
    for t in range(1, 41):
        y[t] = 0.5 * y[t - 1] + 0.6 * x[t] + 0.3 * z[t] + 0.4 * rng.normal()
    reader = [method.Utterance("y", t, f"e{t}", "", {"x": t, "z": t}, method.RECORDED) for t in range(41)]
    series = {"y": reader, "x": reader, "z": reader}
    estimate = method.coupling(series, {"y": y, "x": x, "z": z}, "x", "y", method.Guard())
    covariance = np.cov(np.hstack([x[1:], y[1:], y[:-1], z[1:]]), rowvar=False, bias=True)
    assert estimate.te == pytest.approx(gaussian_cmi(covariance, 1, 1, shrinkage=0.0, jitter=1e-12), rel=1e-6)


def test_identical_embeddings_are_insufficient_data_without_dividing_by_zero():
    series = {agent: [method.Utterance(agent, 3 * t + i, f"{agent}{t}", "same", {o: t for o in AGENTS if o != agent},
                                       method.RECORDED) for t in range(24)] for i, agent in enumerate(AGENTS)}
    with np.errstate(all="raise"):
        features = method.project(series, lambda text: [0.1, 0.3, 0.7], 3)
        found = method.couplings(series, features, method.Guard())
    assert {c.reason for c in found} == {"only 1 distinct message combinations"}


def test_bundled_factory_registers_and_fails_loudly_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    framework = Framework(SQLiteHistory(tmp_path / "history.sqlite"), versions=GitVersions(tmp_path / "history.git"))
    branch = recorded(framework, "Debate", debate())
    with TestClient(build_app(tmp_path)) as client:
        plugin = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "influence-ribbon")
        assert "not an attack detector" in plugin["description"]
        assert plugin["params_schema"]["properties"]["components"]["default"] == 1
        job = analyze(client, branch.id, branch.head)
        assert job["status"] == "failed" and "OPENAI_API_KEY" in job["error"]
