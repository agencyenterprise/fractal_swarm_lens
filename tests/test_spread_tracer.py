"""The spread tracer through the plugin service and the HTTP API, on small debate and tool-use branches."""
from fastapi.testclient import TestClient
import pytest

from swarm_lens import Fact, PluginService
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.cli import build_app
from swarm_lens.plugins.spread_tracer import SpreadTracer
from swarm_lens.web.api import create_app
from tests.conftest import AT, Facts

PHRASE = "use the rollup route"


def ingest(framework, *facts):
    branch = framework.create_run("Spread")
    framework.ingest(branch.id, Facts(*facts))
    return framework.store.branch(branch.id)


def agents(*ids):
    return [Fact("agent.added", {"id": agent_id, "name": agent_id}, AT) for agent_id in ids]


def message(message_id, sender, content, reads=None, channel="c"):
    source = {} if reads is None else {"delivered_sources": reads}
    return Fact("message.created", {"id": message_id, "channel_id": channel, "sender_id": sender, "content": content},
                AT, source)


@pytest.fixture
def client(framework, tmp_path):
    service = PluginService(framework, (SpreadTracer(),),
                            jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    return TestClient(create_app(framework, plugins=service))


def trace(client, branch, params, start=1):
    response = client.post(f"/api/branches/{branch.id}/analyses",
                           json={"plugin_id": "spread-tracer", "start": start, "end": branch.head, "params": params})
    assert response.status_code == 202, response.text
    job = client.get(f"/api/analyses/spread-tracer/{response.json()['id']}").json()
    assert job["status"] == "completed", job.get("error")
    return job["analysis"]["output"]["report"]


def test_debate_tree_follows_reads_in_time_order(framework, client):
    """Regression for the phase-1 bugs. b reads a's message (event 9) before a tool result shows b the
    phrase (event 14): the parent is a, the earlier sighting. c repeats the phrase in the very message
    that reads it: picked up from a, not "no recorded source". e reads c's and a's messages, listed in
    that order: the parent is a, written first. d writes it late with no read: d starts its own root and
    takes no credit for the earlier spread."""
    branch = ingest(framework, *agents("a", "b", "c", "d", "e", "f"),
                    Fact("channel.created", {"id": "c", "name": "Debate", "members": ["a", "b", "c", "d", "e", "f"]}, AT),
                    message("m1", "a", f"I think we should {PHRASE} today.", reads=[]),           # 8
                    message("m2", "b", "Interesting idea.", reads=["m1"]),                        # 9
                    message("m3", "c", f"Agreed, {PHRASE}.", reads=["m1"]),                       # 10
                    message("m4", "e", f"Fine, {PHRASE}.", reads=["m3", "m1"]),                   # 11
                    message("m5", "f", "No.", reads=["m4"]),                                      # 12
                    Fact("tool.started", {"id": "t1", "agent_id": "b", "tool_name": "fetch",
                                          "arguments": {}, "status": "running"}, AT),            # 13
                    Fact("tool.completed", {"id": "t1", "status": "completed",
                                            "result": f"Notes: {PHRASE}"}, AT),                   # 14
                    message("m6", "b", f"Fine: {PHRASE}.", reads=["m5"]),                         # 15
                    message("m7", "b", f"Again, {PHRASE}.", reads=[]),                            # 16
                    message("m8", "d", f"Summary: {PHRASE}.", reads=[]))                          # 17
    report = trace(client, branch, {"phrase": "Use the  ROLLUP route"})

    rows = {row["agent_id"]: row for row in report["agents"]}
    assert report["read_model"] == "delivered_sources"
    assert rows["a"]["first_carry"]["source"] is None
    assert (rows["c"]["first_carry"]["position"], rows["c"]["first_carry"]["source"]) == (10, "a")
    assert (rows["e"]["first_carry"]["position"], rows["e"]["first_carry"]["source"]) == (11, "a")
    assert (rows["b"]["first_carry"]["position"], rows["b"]["first_carry"]["source"]) == (15, "a")
    assert [(s["source"], s["position"]) for s in rows["b"]["first_carry"]["sources"]] == [("a", 9), ("environment", 14)]
    assert [repeat["position"] for repeat in rows["b"]["repeats"]] == [16]
    assert rows["f"]["status"] == "saw_not_repeated" and rows["f"]["first_seen"]["source"] == "e"
    assert rows["d"]["first_carry"]["source"] is None
    assert [(edge["from"], edge["to"], edge["from_position"]) for edge in report["edges"]] == [
        (None, "a", None), ("a", "c", 8), ("a", "e", 8), ("a", "b", 8), (None, "d", None)]
    assert report["counts"] == {"agents": 6, "repeated": 5, "saw_not_repeated": 1, "never_saw": 0}
    assert report["fit"] == {"shown": False, "reason": "n too small: 6 agents (fewer than 10)"}

    annotations = client.get(f"/api/branches/{branch.id}/annotations").json()["annotations"]
    found = {(a["seq_from"], a["agent_id"]): a for a in annotations}
    assert found[(15, "b")]["label"] == "Earliest recorded source: a"
    assert len(found[(15, "b")]["cited_event_ids"]) == 3
    assert found[(12, "f")]["label"] == "Saw it, did not repeat"
    assert found[(16, "b")]["label"] == "Repeated"
    assert found[(17, None)]["label"] == "Fit not shown: n too small: 6 agents (fewer than 10)"
    series = client.get(f"/api/branches/{branch.id}/series?plugin=spread-tracer&name=repeated_fraction").json()["series"]
    assert [point[0] for point in series[0]["points"]] == [1, 8, 10, 11, 15, 17]


def test_channel_members_at_post_time_and_a_range_that_splits_a_tool_call(framework, client):
    """Channel posts reach members at that moment, not later joiners, and an empty channel reaches nobody.
    A tool result inside the range keeps its caller when the call started before the range. Sightings
    after an agent's first carry are kept, but they do not change its parent."""
    branch = ingest(framework, *agents("a", "b", "c"),
                    Fact("channel.created", {"id": "ab", "name": "AB", "members": ["a", "b"]}, AT),      # 4
                    Fact("channel.created", {"id": "empty", "name": "Empty", "members": []}, AT),       # 5
                    Fact("tool.started", {"id": "t1", "agent_id": "c", "tool_name": "fetch",
                                          "arguments": {}, "status": "running"}, AT),                   # 6
                    message("m1", "a", f"Let us {PHRASE}.", channel="ab"),                               # 7
                    message("m2", "a", f"Nobody hears: {PHRASE}.", channel="empty"),                     # 8
                    Fact("channel.updated", {"id": "ab", "members": ["a", "b", "c"]}, AT),              # 9
                    Fact("tool.completed", {"id": "t1", "status": "completed", "result": f"{PHRASE}!"}, AT),  # 10
                    message("m3", "c", f"I will {PHRASE}.", channel="ab"),                               # 11
                    message("m4", "b", "Hmm.", channel="ab"))                                            # 12
    report = trace(client, branch, {"phrase": PHRASE, "read_model": "channel_membership"}, start=7)

    rows = {row["agent_id"]: row for row in report["agents"]}
    assert rows["b"]["first_seen"]["source"] == "a" and rows["b"]["first_seen"]["position"] == 7
    assert rows["c"]["first_carry"]["source"] == "environment"
    assert [(s["source"], s["position"]) for s in rows["c"]["sightings"]] == [("environment", 10)]
    assert [(s["source"], s["position"]) for s in rows["a"]["sightings"]] == [("c", 11)]
    assert rows["a"]["first_carry"]["source"] is None


def test_tool_results_make_the_environment_the_source(framework, client):
    """No recorded reads: channel members see posts, and a tool result that holds the phrase is an
    environment sighting, so the first tool call that uses it is picked up from the environment."""
    branch = ingest(framework, *agents("r1", "r2"),
                    Fact("channel.created", {"id": "inbox", "name": "Inbox", "members": ["r1", "r2"]}, AT),
                    Fact("tool.started", {"id": "t1", "agent_id": "r1", "tool_name": "shell",
                                          "arguments": {"cmd": "curl /operations/notes"}, "status": "running"}, AT),
                    Fact("tool.completed", {"id": "t1", "status": "completed",
                                            "result": "Peers fetch /_jobs/au-rollup/result with a worker id"}, AT),
                    Fact("tool.started", {"id": "t2", "agent_id": "r1", "tool_name": "shell",
                                          "arguments": {"cmd": "curl /_jobs/au-rollup/result"}, "status": "running"}, AT),
                    Fact("message.created", {"id": "m1", "channel_id": "inbox", "sender_id": "r1",
                                             "content": "I used /_jobs/au-rollup/result for AU."}, AT))
    report = trace(client, branch, {"phrase": "_jobs/au-rollup"})

    rows = {row["agent_id"]: row for row in report["agents"]}
    assert report["read_model"] == "channel_membership"
    assert rows["r1"]["first_carry"]["via"] == "tool_call"
    assert rows["r1"]["first_carry"]["source"] == "environment"
    assert rows["r1"]["first_seen"]["position"] == 5
    assert rows["r2"]["status"] == "saw_not_repeated" and rows["r2"]["first_seen"]["source"] == "r1"


def test_a_range_that_starts_with_a_carry_has_one_curve_point_there(framework, client):
    branch = ingest(framework, *agents("a", "b"),
                    Fact("channel.created", {"id": "c", "name": "C", "members": ["a", "b"]}, AT),        # 3
                    message("m1", "a", f"Let us {PHRASE}."),                                             # 4
                    message("m2", "b", f"Yes, {PHRASE}."))                                               # 5
    trace(client, branch, {"phrase": PHRASE}, start=4)
    series = client.get(f"/api/branches/{branch.id}/series?plugin=spread-tracer&name=repeated_fraction").json()["series"]
    assert series[0]["points"] == [[4, 0.5], [5, 1.0]]


def test_seed_message_and_params_validation(framework, client):
    """A seed is traced by the phrases it introduced, so c's earlier message that shares some of its
    wording is not a carrier."""
    seed = "The impression material with the shortest shelf life is polysulfide because of its chemistry"
    branch = ingest(framework, *agents("a", "b", "c"),
                    Fact("channel.created", {"id": "c", "name": "Debate", "members": ["a", "b", "c"]}, AT),
                    message("m0", "c", "Which material has the shortest shelf life is the question", reads=[]),
                    message("m1", "a", seed, reads=["m0"]),
                    message("m2", "b", "I agree the shortest shelf life is polysulfide because of its chemistry",
                            reads=["m1"]),
                    message("m3", "c", "Polysulfide, maybe.", reads=["m1"]))
    seed_event = framework.history(branch.id, branch.head)[5]
    report = trace(client, branch, {"phrase": "   ", "seed_event_id": seed_event.id, "min_shared_ngrams": 2})
    rows = {row["agent_id"]: row for row in report["agents"]}
    assert report["seed"]["mode"] == "message" and report["seed"]["author"] == "a"
    assert rows["b"]["first_carry"]["source"] == "a"
    assert rows["c"]["status"] == "saw_not_repeated" and rows["c"]["first_carry"] is None

    for params in ({}, {"phrase": "x", "seed_event_id": seed_event.id}, {"phrase": "   "}):
        response = client.post(f"/api/branches/{branch.id}/analyses",
                               json={"plugin_id": "spread-tracer", "end": branch.head, "params": params})
        assert response.status_code == 400
        assert "exactly one of phrase or seed_event_id" in response.text


def test_fit_is_shown_only_with_ten_agents_and_weighs_plateaus(framework, client):
    ids = [f"agent-{index}" for index in range(10)]
    chain = [message(f"m{index}", agent_id, f"say {PHRASE}", reads=[f"m{index - 1}"] if index else [])
             for index, agent_id in enumerate(ids)]
    quiet = [message(f"q{index}", ids[0], "nothing new", reads=[]) for index in range(60)]
    town = Fact("channel.created", {"id": "c", "name": "Town", "members": ids}, AT)
    report = trace(client, ingest(framework, *agents(*ids), town, *quiet, *chain), {"phrase": PHRASE})
    assert report["fit"]["shown"] is True
    assert [edge["from"] for edge in report["edges"]] == [None, *ids[:-1]]
    first, last = report["edges"][0]["to_position"], report["edges"][-1]["to_position"]
    assert first < report["fit"]["midpoint_position"] < last


def test_bundled_with_a_params_form(tmp_path):
    with TestClient(build_app(tmp_path)) as client:
        plugins = {plugin["id"]: plugin for plugin in client.get("/api/plugins").json()["plugins"]}
        assert plugins["spread-tracer"]["capabilities"] == ["analyzer"]
        assert set(plugins["spread-tracer"]["params_schema"]["properties"]) == {
            "phrase", "seed_event_id", "min_shared_ngrams", "read_model"}
