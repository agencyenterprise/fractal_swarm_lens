import json

import pytest
from fastapi.testclient import TestClient

from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.observability.mast.evidence import generate_evidence, validate_traits
from swarm_lens.observability.mast.method import MastPlugin, assets, history_trace
from swarm_lens.observability.mast.service import MastJobs, MastService
from swarm_lens.web.api import create_app
from swarm_lens.web.mast import mast_extension


LABELS = [{"code": "1.3", "present": True}, {"code": "1.1", "present": False}]
EVENTS = [{"event_id": f"e{i}", "position": i} for i in range(1, 5)]


def response(start="e1", end="e4", supporting=None, counter=None):
    return {"traits": [
        {"code": "1.3", "explanation": "Repeated before a later correction.", "occurrences": [{
            "start_event_id": start, "end_event_id": end,
            "supporting_event_ids": supporting or ["e1", "e2"],
            "counterevidence_event_ids": ["e4"] if counter is None else counter,
            "explanation": "The correction follows two repeated messages."}]},
        {"code": "1.1", "explanation": "The task specification was followed.", "occurrences": []}]}


def test_multiple_occurrences_keep_context_and_counterevidence():
    raw = response()
    raw["traits"][0]["occurrences"].append(response("e2", "e3", ["e3"], [])["traits"][0]["occurrences"][0])
    traits, warnings = validate_traits(json.dumps(raw), LABELS, EVENTS)
    assert not warnings
    detail = traits["1.3"]
    assert detail["status"] == "located"
    assert len(detail["occurrences"]) == 2
    assert detail["occurrences"][0]["end_position"] == 4
    assert detail["occurrences"][0]["counterevidence_event_ids"] == ["e4"]
    assert traits["1.1"]["status"] == "explained"


@pytest.mark.parametrize("change", [
    {"start_event_id": "missing"}, {"end_event_id": "future"},
    {"start_event_id": "e4", "end_event_id": "e1"},
    {"end_event_id": "e2"}, {"supporting_event_ids": []},
    {"supporting_event_ids": ["message-id"]}, {"supporting_event_ids": "e1"},
    {"counterevidence_event_ids": ["other-branch"]},
    {"counterevidence_event_ids": ["e1"]},
])
def test_invalid_references_never_become_occurrences(change):
    raw = response()
    raw["traits"][0]["occurrences"][0].update(change)
    traits, warnings = validate_traits(json.dumps(raw), LABELS, EVENTS)
    assert traits["1.3"]["status"] == "unavailable"
    assert traits["1.3"]["occurrences"] == []
    assert len(warnings) == 1
    assert traits["1.1"]["status"] == "explained"


def test_duplicate_trait_and_absent_trait_occurrences_are_rejected():
    raw = response()
    raw["traits"].append(raw["traits"][0])
    raw["traits"][1]["occurrences"] = raw["traits"][0]["occurrences"]
    traits, warnings = validate_traits(json.dumps(raw), LABELS, EVENTS)
    assert all(item["status"] == "unavailable" for item in traits.values())
    assert len(warnings) == 2


def test_unsupported_positive_stays_explicit_and_unknown_is_not_explained():
    raw = response()
    raw["traits"][0].update(explanation="Later clarification contradicts this judgment.", occurrences=[])
    traits, warnings = validate_traits(json.dumps(raw), LABELS + [{"code": "1.2", "present": None}], EVENTS)
    assert not warnings
    assert traits["1.3"]["status"] == "not_localized"
    assert "contradicts" in traits["1.3"]["explanation"]
    assert traits["1.2"] == {"status": "unavailable", "explanation": "", "occurrences": []}


class Judge:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail, self.prompts = payload, fail, []

    def describe(self):
        return {"ready": True, "model": "fake"}

    def complete(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith("Explain the supplied MAST judgments"):
            if self.fail:
                raise RuntimeError("private-provider-details")
            return {"text": json.dumps(self.payload), "model": "fake", "finish_reason": "stop"}
        return {"text": "A. Repetition.\nB. no\nC.\n" + "\n".join(
            f"{c['code']} {'yes' if c['code'] == '1.3' else 'no'}" for c in assets()["categories"]),
            "model": "fake", "finish_reason": "stop"}


def test_evidence_failure_preserves_original_assessment(framework, branch):
    plugin = MastPlugin(Judge(fail=True))
    framework.plugins[plugin.id] = plugin
    record = framework.analyze("mast", branch.id, 4)
    output = record["output"]
    assert output["parse_status"] == "complete"
    assert next(x for x in output["labels"] if x["code"] == "1.3")["present"] is True
    assert output["evidence"]["status"] == "unavailable"
    assert "private-provider-details" not in json.dumps(record)


def test_unfinished_evidence_response_is_not_accepted(framework, branch):
    judge = Judge()
    judge.complete = lambda prompt: {"text": json.dumps(response()), "finish_reason": "length"}
    result = generate_evidence(judge, history_trace(framework.history(branch.id, 4)), LABELS, [])
    assert result["status"] == "unavailable" and not result["traits"]


def test_api_presents_frozen_evidence_and_library_matches(framework, branch, tmp_path):
    child = framework.fork(branch.id, 4, "Evidence child")
    event_id = framework.history(child.id, 4)[-1].id
    payload = response(event_id, event_id, [event_id], [])
    for category in assets()["categories"]:
        if category["code"] not in {"1.3", "1.1"}:
            payload["traits"].append({"code": category["code"], "explanation": "Absent in the saved prefix.", "occurrences": []})
    plugin = MastPlugin(Judge(payload))
    framework.plugins[plugin.id] = plugin
    direct = framework.analyze("mast", child.id, 4)
    service = MastService(framework, plugin, MastJobs(tmp_path / "mast.sqlite"), FileArtifacts(tmp_path / "artifacts"))
    job = service.submit(child.id, 4, "partial")
    framework.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": "future child"}, 4)
    service.execute(job["id"])
    assert service.jobs.get(job["id"])["analysis"]["output"]["evidence"]["traits"] == direct["output"]["evidence"]["traits"]
    with TestClient(create_app(framework, service.artifacts, extensions=(mast_extension(service),))) as client:
        path = f"/api/plugins/mast/analyses/{job['id']}/traits/1.3"
        detail = client.get(path).json()
        assert detail["cursor"] == 4 and detail["branch_id"] == child.id
        event = detail["occurrences"][0]["events"][0]
        assert event["event_id"] == event_id and event["message_id"] == "m1"
        assert event["text"] == "first" and event["role"] == "supporting"
        assert "future child" not in json.dumps(detail)
        assert client.get(path.replace("1.3", "9.9")).status_code == 400
        calls = len(plugin.judge.prompts)
        client.get(path)
        assert len(plugin.judge.prompts) == calls, "Opening a disclosure must never call the judge"
        saved = service.jobs.get(job["id"])
        del saved["analysis"]["output"]["evidence"]
        service.jobs.update(saved)
        legacy = client.get(path).json()
        assert legacy["status"] == "unavailable" and legacy["occurrences"] == []
        assert "shared by all traits" in legacy["notice"]


def test_frozen_prefix_rejects_a_real_future_parent_event(framework, branch):
    prefix = framework.history(branch.id, 4)
    first, future = prefix[-1].id, framework.history(branch.id, 7)[-1].id
    events = [{"event_id": event.id, "position": event.position} for event in prefix]
    traits, warnings = validate_traits(json.dumps(response(first, future, [first], [])), LABELS, events)
    assert warnings and not traits["1.3"]["occurrences"]


def test_presenter_keeps_non_message_context_and_later_counterevidence(framework, branch, tmp_path):
    from swarm_lens.web.mast_details import present_trait_details

    history = framework.history(branch.id)
    first, last = history[3].id, history[-1].id
    plugin = MastPlugin(Judge(response(first, last, [first], [last])))
    service = MastService(framework, plugin, MastJobs(tmp_path / "mast.sqlite"), FileArtifacts(tmp_path / "artifacts"))
    job = service.submit(branch.id, 7, "partial")
    service.execute(job["id"])
    detail = present_trait_details(service, job["id"], "1.3")
    context = detail["occurrences"][0]["events"]
    assert [event["position"] for event in context] == [4, 5, 6, 7]
    assert [event["role"] for event in context] == ["supporting", "context", "context", "counterevidence"]
    assert context[1]["kind"] == "memory.written" and context[1]["text"] == "old"
    assert context[-1]["text"] == "future"
