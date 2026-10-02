import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from swarm_lens.core.models import Branch, Conflict, DomainError, Event, Run, State


class GitVersions:
    """Portable immutable checkpoints; SQLite remains the operational history."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not (self.path / "HEAD").exists():
            self.path.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "--bare", str(self.path)], check=True, capture_output=True)

    def _git(self, *args, input=None, check=True):
        env = {**os.environ, "GIT_AUTHOR_NAME": "Swarm Lens", "GIT_AUTHOR_EMAIL": "local@swarm-lens",
               "GIT_COMMITTER_NAME": "Swarm Lens", "GIT_COMMITTER_EMAIL": "local@swarm-lens"}
        result = subprocess.run(["git", "--git-dir", str(self.path), *args], input=input,
                                text=True, capture_output=True, env=env)
        if check and result.returncode:
            raise DomainError(result.stderr.strip())
        return result.stdout.strip() if result.returncode == 0 else None

    def checkpoint(self, run: Run, branch: Branch, state: State, events: list[Event], message: str) -> str:
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in branch.id):
            raise DomainError("Invalid branch identifier")
        immutable_ref = f"refs/checkpoints/{branch.id}/{state.cursor}"
        existing = self._git("rev-parse", "--verify", immutable_ref, check=False)
        if existing:
            return existing
        own = [asdict(event) for event in events if event.branch_id == branch.id]
        files = {
            "manifest.json": {"schema_version": 1, "run": asdict(run), "branch": asdict(branch), "cursor": state.cursor},
            "state.json": state.to_dict(),
            "events.json": own,
        }
        entries = []
        for name, value in sorted(files.items()):
            blob = self._git("hash-object", "-w", "--stdin", input=json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))
            entries.append(f"100644 blob {blob}\t{name}\n")
        tree = self._git("mktree", input="".join(entries))
        ref = f"refs/heads/{branch.id}"
        old = self._git("rev-parse", "--verify", ref, check=False)
        refs = self._git("for-each-ref", "--format=%(refname) %(objectname)", f"refs/checkpoints/{branch.id}/")
        earlier = [(int(line.split()[0].rsplit("/", 1)[1]), line.split()[1])
                   for line in refs.splitlines() if int(line.split()[0].rsplit("/", 1)[1]) < state.cursor]
        parent = max(earlier)[1] if earlier else None
        if not parent and branch.parent_id:
            parent = self._git("rev-parse", "--verify", f"refs/checkpoints/{branch.parent_id}/{min(state.cursor, branch.fork_position)}")
        arguments = ["commit-tree", tree]
        if parent:
            arguments += ["-p", parent]
        commit = self._git(*arguments, input=message + "\n")
        old_cursor = json.loads(self._git("show", f"{old}:manifest.json"))["cursor"] if old else -1
        update = f"update {ref} {commit} {old or '0' * 40}\n" if state.cursor > old_cursor else ""
        transaction = f"start\ncreate {immutable_ref} {commit}\n{update}prepare\ncommit\n"
        try:
            self._git("update-ref", "--stdin", input=transaction)
        except DomainError as exc:
            raise Conflict("Git checkpoint changed concurrently; retry the checkpoint") from exc
        return commit
