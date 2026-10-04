import ast
import json
from pathlib import Path
import subprocess

import pytest

from swarm_lens import Framework
from swarm_lens.adapters.git import GitVersions
from swarm_lens.plugins import ActivityPlugin


def test_plugin_reads_branch_cursor_and_persists_provenance(framework, branch):
    framework.plugins["activity"] = ActivityPlugin()
    record = framework.analyze("activity", branch.id, 4)
    assert record["output"]["messages"] == 1
    assert record["output"]["memory_items"] == 0
    assert len(record["input_digest"]) == 64
    assert framework.store.analyses(branch.id, 4)[0] == record


def test_plugin_registry_rejects_duplicates_and_records_the_requested_config(framework, branch):
    class Greedy:
        id, version = "greedy", "1"
        def run(self, context, config):
            config["injected"] = True
            return {}
    with pytest.raises(ValueError, match="Duplicate plugin ID"):
        Framework(framework.store, plugins=(Greedy(), Greedy()))
    framework.plugins["greedy"] = Greedy()
    record = framework.analyze("greedy", branch.id, 4, {"threshold": 2})
    assert record["config"] == {"threshold": 2}
    assert framework.store.analyses(branch.id, 4)[0]["config"] == {"threshold": 2}


def test_plugin_can_fork_and_intervene_without_changing_parent(framework, branch):
    class PromptPlugin:
        id, version = "prompt", "1"
        def run(self, context, config):
            child = context.fork("plugin branch")
            context.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": config["prompt"]}, context.cursor)
            return {"branch_id": child.id}
    framework.plugins["prompt"] = PromptPlugin()
    result = framework.analyze("prompt", branch.id, 4, {"prompt": "hello"})
    assert framework.state(result["output"]["branch_id"]).agents["a"].system_prompt == "hello"
    assert framework.state(branch.id).agents["a"].system_prompt is None


def test_git_fork_uses_historical_base_and_does_not_rewind_head(framework, branch, tmp_path):
    versions = GitVersions(tmp_path / "history.git")
    framework.versions = versions
    latest = framework.checkpoint(branch.id)
    historical = framework.checkpoint(branch.id, 4)
    assert latest != historical
    assert versions._git("rev-parse", f"refs/heads/{branch.id}") == latest
    assert versions._git("rev-list", "--parents", "-n", "1", historical).split() == [historical]
    child = framework.fork(branch.id, 4, "child")
    framework.intervene(child.id, "agent.updated", {"id": "a", "system_prompt": "changed"}, 4)
    commit = framework.checkpoint(child.id)
    assert versions._git("rev-parse", commit + "^") == historical
    exported = json.loads(versions._git("show", commit + ":events.json"))
    assert len(exported) == 1
    assert exported[0]["kind"] == "agent.updated"
    assert framework.checkpoint(child.id) == commit
    assert not (versions.path / "index").exists()


def test_core_and_application_do_not_import_infrastructure():
    root = Path(__file__).parents[1] / "src/swarm_lens"
    forbidden = {"fastapi", "sqlite3", "requests", "subprocess", "uvicorn", "examples"}
    for file in [*(root / "core").glob("*.py"), *(root / "application").glob("*.py")]:
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in forbidden for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
                assert ".adapters" not in (node.module or "")
