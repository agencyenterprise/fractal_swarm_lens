import ast
import json
from pathlib import Path

from swarm_lens.adapters.git import GitVersions


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
