from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from swarm_lens import Fact, Framework
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.observability.http_calls import HttpCallsPlugin
from swarm_lens.web.api import create_app
from swarm_lens.web.http_calls import http_calls_extension
from tests.conftest import Facts

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(minute):
    return (START + timedelta(minutes=minute)).isoformat()


def tool(call_id, minute, name, arguments, kind="tool.recorded", **data):
    return Fact(kind, {"id": call_id, "agent_id": "a", "tool_name": name, "arguments": arguments,
                       "status": "completed", **data}, at(minute))


def browsing_run():
    """GitHub twice every six minutes, docs fetches, a burst of eight navigations at minute 31, and URLs that are
    only mentioned (a message, a file edit, search results), which are not calls."""
    facts = [Fact("agent.added", {"id": "a", "name": "Agent A"}, at(0)),
             Fact("channel.created", {"id": "c", "name": "Shared", "members": ["a"]}, at(0)),
             Fact("message.created", {"id": "m", "channel_id": "c", "sender_id": "a",
                                      "content": "Mentioning https://not-a-call.example is not a call"}, at(0))]
    for minute in range(1, 60, 3):
        call = {"command": f"curl -s https://api.github.com/repos/x/{minute}"}
        facts += [tool(f"gh-{minute}", minute, "bash", call, "tool.started", status="running"),
                  Fact("tool.completed", {"id": f"gh-{minute}", "status": "completed",
                                          "result": "see https://api.github.com/rate_limit"}, at(minute))]
    facts += [tool(f"burst-{index}", 31, "browser_navigate", {"url": f"https://WWW.Evil.example:443/p/{index}"})
              for index in range(8)]
    facts += [Fact("observation.recorded", {"type": "page_load", "url": "https://docs.python.org/3/"}, at(40)),
              tool("script", 44, "execute_python", {"code": "import requests\nrequests.get('https://docs.python.org/3/')"}),
              tool("search", 45, "web_search", {"query": "python"}, result={"links": ["https://pypi.org/project/x"]}),
              tool("edit", 46, "edit_file", {"path": "client.py",
                                             "patch": "+    requests.get('https://patched.example/api')  # see https://pypi.org"}),
              tool("read", 47, "execute_command", {"command": "cat README.md  # https://readme.example"}),
              Fact("observation.recorded", {"type": "done"}, at(60))]
    return sorted(facts, key=lambda fact: fact.occurred_at)


def client_with_run(tmp_path, facts):
    framework = Framework(SQLiteHistory(tmp_path / "history.sqlite"), plugins=(HttpCallsPlugin(),))
    branch = framework.create_run("Browsing")
    framework.ingest(branch.id, Facts(*facts))
    client = TestClient(create_app(framework, extensions=(http_calls_extension(framework),)))
    return client, framework.store.branch(branch.id), framework


def analyze(client, branch, config=None):
    response = client.post("/api/plugins/http_calls/analyses",
                           json={"branch_id": branch.id, "cursor": branch.head, "config": config or {}})
    assert response.status_code == 200, response.text
    return response.json()


def test_counts_calls_per_website_over_time_and_finds_the_burst(tmp_path):
    client, branch, framework = client_with_run(tmp_path, browsing_run())
    record = analyze(client, branch, {"bins": 10})
    output = record["output"]

    assert output["axis"] == "time"
    assert len(output["bins"]) == 10
    assert output["bins"][0]["start"] == at(0) and output["bins"][-1]["end"] == at(60)
    domains = {domain["domain"]: domain for domain in output["domains"]}
    assert [domain["domain"] for domain in output["domains"]] == ["api.github.com", "evil.example", "docs.python.org"]
    assert {name: domain["total"] for name, domain in domains.items()} == {
        "api.github.com": 20, "evil.example": 8, "docs.python.org": 2}
    assert output["total_calls"] == 30
    assert domains["api.github.com"]["counts"] == [2] * 10
    assert domains["evil.example"]["counts"] == [0, 0, 0, 0, 0, 8, 0, 0, 0, 0]

    [spike] = output["spikes"]
    burst = [event for event in framework.history(branch.id) if event.data.get("id", "").startswith("burst-")]
    assert (spike["domain"], spike["first_bin"], spike["last_bin"], spike["count"]) == ("evil.example", 5, 5, 8)
    assert spike["threshold"] == 4
    assert (spike["start"], spike["end"]) == (at(30), at(36))
    assert [event["position"] for event in spike["events"]] == [event.position for event in burst]
    assert spike["events"][0]["event_id"] == burst[0].id
    assert framework.store.analyses(branch.id, branch.head)[0]["id"] == record["id"]


def test_run_without_http_calls_is_empty_and_equal_timestamps_bin_by_position(tmp_path):
    quiet = [fact for fact in browsing_run() if fact.kind in ("agent.added", "channel.created", "message.created")]
    client, branch, _ = client_with_run(tmp_path, quiet)
    output = analyze(client, branch)["output"]
    assert (output["total_calls"], output["domains"], output["spikes"], output["bins"]) == (0, [], [], [])

    same_time = [Fact(fact.kind, fact.data, at(0)) for fact in browsing_run()]
    client, branch, _ = client_with_run(tmp_path / "same", same_time)
    output = analyze(client, branch, {"bins": 400})["output"]
    assert output["axis"] == "position" and "share one timestamp" in output["axis_reason"]
    assert output["bins"][0] == {"start": 1, "end": 1} and output["bins"][-1]["end"] == branch.head
    assert len(output["bins"]) == branch.head - 1


def test_manifest_is_advertised_and_bad_settings_are_rejected(tmp_path):
    client, branch, _ = client_with_run(tmp_path, browsing_run())
    [manifest] = client.get("/api/workspace").json()["capabilities"]["web_plugins"]
    assert manifest["id"] == "http_calls" and manifest["ui"] == {"renderer": "http_calls"}
    assert manifest["title"] == "Network activity"
    assert manifest["modes"] == ["saved_trace"] and manifest["config"]["properties"]["bins"]["default"] == 40
    response = client.post("/api/plugins/http_calls/analyses",
                           json={"branch_id": branch.id, "cursor": branch.head, "config": {"bins": 1}})
    assert response.status_code == 400
