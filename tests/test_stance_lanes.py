"""Stance lanes through the real app: params, flips toward read messages, holdouts, run pattern, LLM path."""
from fastapi.testclient import TestClient
import pytest

from swarm_lens import Fact, PluginService
from swarm_lens.adapters.jobs import JobStore
from swarm_lens.plugins.stance_lanes import LLMStanceLabeller, StanceLanes
from swarm_lens.web.api import create_app
from tests.conftest import AT, Facts

OPTION = r"\(([A-Z])\)"


def debate(framework, rounds, *, delivered=True, tool_before=None):
    """Agents x, y, z, ... answer in turn each round; each reads the latest message of every other agent.

    `tool_before` maps a message id to tool arguments the author passes just before writing it.
    """
    agents = "xyzw"[:len(rounds[0])]
    branch = framework.create_run("Debate")
    facts = [*(Fact("agent.added", {"id": agent, "name": agent}, AT) for agent in agents),
             Fact("channel.created", {"id": "d", "name": "Debate", "members": list(agents)}, AT),
             Fact("message.created", {"id": "task", "channel_id": "d", "content": "Pick A or B."}, AT)]
    latest: dict[str, str] = {}
    for number, answers in enumerate(rounds):
        for agent, answer in zip(agents, answers):
            message = f"{agent}{number}"
            if message in (tool_before or {}):
                facts.append(Fact("tool.started", {"id": f"tool-{message}", "agent_id": agent, "tool_name": "search",
                                                   "arguments": {"query": tool_before[message]}, "status": "running"}, AT))
            read = [latest[peer] for peer in agents if peer != agent and peer in latest]
            facts.append(Fact("message.created", {"id": message, "channel_id": "d", "sender_id": agent,
                                                  "content": f"I reason at length. The answer is ({answer})."},
                              AT, {"delivered_sources": read} if delivered else {}))
            latest[agent] = message
    framework.ingest(branch.id, Facts(*facts))
    return framework.store.branch(branch.id)


def client_for(framework, tmp_path, plugin):
    service = PluginService(framework, (plugin,), jobs=lambda name, label: JobStore(tmp_path / "jobs.sqlite", name, label))
    return TestClient(create_app(framework, plugins=service))


def run(client, branch, params, start=1):
    response = client.post(f"/api/branches/{branch.id}/analyses",
                           json={"plugin_id": "stance-lanes", "start": start, "end": branch.head, "params": params})
    assert response.status_code == 202, response.text
    return client.get(f"/api/analyses/stance-lanes/{response.json()['id']}").json()


def analyze(client, branch, params, start=1):
    job = run(client, branch, params, start)
    assert job["status"] == "completed", job.get("error")
    return job["analysis"]["output"]


def messages(framework, branch):
    return {event.data.get("id"): event for event in framework.history(branch.id)}


def test_a_minority_holdout_that_others_adopt_is_a_cascade_with_cited_read_messages(framework, tmp_path):
    branch = debate(framework, ["BAA", "BBB", "BBB"])
    client = client_for(framework, tmp_path, StanceLanes())
    output = analyze(client, branch, {"pattern": OPTION, "answer_key": "(A)"})
    events = messages(framework, branch)

    flips = [a for a in output["annotations"] if a["label"].startswith("Flip")]
    assert [(f["agent_id"], f["data"]["from"], f["data"]["to"], f["data"]["toward_agent"]) for f in flips] == [
        ("y", "a", "b", "x"), ("z", "a", "b", None)]
    assert flips[0]["cited_event_ids"] == [events["y1"].id, events["x1"].id]
    assert flips[1]["data"]["toward_agents"] == {"x": 0.5, "y": 0.5}
    assert {f["data"]["transition"] for f in flips} == {"C->W"} and flips[0]["data"]["read_basis"] == "delivered_sources"
    [holdout] = [a for a in output["annotations"] if a["label"] == "Holdout"]
    assert (holdout["agent_id"], holdout["seq_from"], holdout["cited_event_ids"]) == ("x", events["x1"].position, [events["x1"].id])
    [pattern] = [a for a in output["annotations"] if a["label"].startswith("Run pattern")]
    assert pattern["label"] == "Run pattern: cascade" and pattern["data"]["agents"] == ["x"]
    assert pattern["data"]["first_stances_differ"] is True

    series = {s["agent_id"]: s["points"] for s in client.get(f"/api/branches/{branch.id}/series",
                                                             params={"name": "stance"}).json()["series"]}
    assert [value for _, value in series["y"]] == [2.0, 1.0, 1.0]  # stance 1 = "b" (seen first), 2 = "a"
    report = output["report"]
    assert report["typology"]["kind"] == "cascade" and report["stances"] == ["b", "a"]
    assert [lane["holdout"] for lane in report["lanes"]] == [True, False, False]
    assert report["lanes"][1]["cells"][0]["correct"] is True and report["lanes"][1]["cells"][1]["correct"] is False
    assert report["flips"][0]["read"]["x"] == {"seq": events["x1"].position, "event_id": events["x1"].id}


def test_history_before_the_range_and_agents_without_a_lane_still_explain_flips(framework, tmp_path):
    branch = debate(framework, ["BAA", "BBB"], tool_before={"y1": "is (A) or (C) right?"})
    events = messages(framework, branch)
    output = analyze(client_for(framework, tmp_path, StanceLanes()), branch,
                     {"pattern": OPTION, "min_messages": 1}, start=events["y1"].position)
    report = output["report"]
    assert [lane["agent_id"] for lane in report["lanes"]] == ["y", "z"]  # x wrote nothing in the range
    assert [(f["agent_id"], f["from"], f["to"], f["toward"]) for f in report["flips"]] == [
        ("y", "a", "b", {"x": 1.0}), ("z", "a", "b", {"x": 0.5, "y": 0.5})]  # tool text never overrides the message
    assert all(a["seq_from"] >= events["y1"].position for a in output["annotations"])


@pytest.mark.parametrize("rounds, kind, holdouts, unlabelled", [
    (["AAA", "AAA"], "unanimous", [], []),
    (["BAA", "BAA"], "stalemate", ["x", "y", "z"], []),
    (["BAA", "AAA"], "dissenter gives in", ["y", "z"], []),
    (["BAA", "BAB"], "mixed", ["x", "y"], []),
    (["AB", "AA"], "split resolved", ["x"], []),
    (["AAA", "ABA", "AAA"], "wavered", ["x", "z"], []),
    (["A-A", "A-A"], "unanimous", [], ["y"]),
    (["BA"], "stalemate", ["y"], []),  # y's only message followed reading x's different stance
])
def test_run_pattern_and_holdouts_need_no_ground_truth(framework, tmp_path, rounds, kind, holdouts, unlabelled):
    report = analyze(client_for(framework, tmp_path, StanceLanes()), debate(framework, rounds),
                     {"pattern": OPTION, "min_messages": 1})["report"]
    assert report["typology"]["kind"] == kind and report["typology"]["unlabelled"] == unlabelled
    assert [lane["agent_id"] for lane in report["lanes"] if lane["holdout"]] == holdouts


def test_params_are_checked_before_a_job_exists(framework, tmp_path):
    branch = debate(framework, ["AAA"])
    client = client_for(framework, tmp_path, StanceLanes())
    url = f"/api/branches/{branch.id}/analyses"
    for params, message in [({}, "needs a pattern"), ({"pattern": "[A-Z]"}, "capture group"),
                            ({"pattern": "("}, "Invalid stance pattern"),
                            ({"stance_source": "llm"}, "needs a stance question")]:
        response = client.post(url, json={"plugin_id": "stance-lanes", "end": branch.head, "params": params})
        assert response.status_code == 400 and message in response.json()["detail"], params


LLM = {"stance_source": "llm", "stance_question": "Which option does the agent commit to?"}


def test_llm_labels_are_normalized_cached_and_fall_back_to_timing_without_read_sets(framework, tmp_path):
    branch = debate(framework, ["BAA", "BBA"], delivered=False)
    replies = iter([{"stances": [{"i": 0, "stance": "(B) Melanoma"}, {"i": 1, "stance": '"A."'},
                                 {"i": 2, "stance": "a"}, {"i": 3, "stance": "b"}, {"i": 4, "stance": "(b)"}]},
                    {"stances": [{"i": 5, "stance": None}]}])
    calls = []

    def complete(messages):
        calls.append(messages)
        return next(replies)

    client = client_for(framework, tmp_path, StanceLanes(LLMStanceLabeller(tmp_path / "cache", complete=complete)))
    report = analyze(client, branch, LLM)["report"]
    assert "Which option does the agent commit to?" in calls[0][1]["content"]
    assert len(calls) == 2 and calls[1][-1]["content"] == "You skipped messages [5]. Label exactly those."
    assert [[c["stance"] for c in lane["cells"]] for lane in report["lanes"]] == [["b", "b"], ["a", "b"], ["a", "a"]]
    assert report["lanes"][2]["cells"][1]["stated"] is False
    assert report["flips"][0]["basis"] == "timing" and report["flips"][0]["toward"] == {"x": 1.0}

    analyze(client, branch, LLM)
    assert len(calls) == 2


def test_a_malformed_llm_reply_fails_the_job_instead_of_becoming_a_stance(framework, tmp_path):
    branch = debate(framework, ["BA"])
    labeller = LLMStanceLabeller(tmp_path / "cache", complete=lambda messages: {
        "stances": [{"i": 0, "stance": {"answer": "B"}}, {"i": 1, "stance": "a"}]})
    job = run(client_for(framework, tmp_path, StanceLanes(labeller)), branch, LLM)
    assert job["status"] == "failed" and "not text" in job["error"]
