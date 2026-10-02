import argparse
from dataclasses import asdict
import json
from pathlib import Path

from swarm_lens import Framework
from swarm_lens.adapters.artifacts import FileArtifacts
from swarm_lens.adapters.git import GitVersions
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.plugins import ActivityPlugin
from .source import VillageSource


def build(data: Path, source: Path):
    framework = Framework(SQLiteHistory(data / "history.sqlite"), versions=GitVersions(data / "history.git"), plugins=(ActivityPlugin(),))
    if framework.store.runs():
        raise SystemExit("This database already has a run. Choose a new --data directory to import again.")
    village = VillageSource(source, FileArtifacts(data / "artifacts"))
    branch = framework.create_run("AI Village · Elect a village leader", {
        **village.selection, "application": "examples.ai_village", "attribution": "AI Digest / AI Village", "dataset_url": "https://huggingface.co/datasets/aidigestorg/ai-village",
    })
    count = framework.ingest(branch.id, village)
    framework.analyze("activity", branch.id, count)
    commit = framework.checkpoint(branch.id)
    report = {"run_id": branch.run_id, "branch_id": branch.id, "events": count,
              "git_commit": commit, "selection": village.selection}
    (data / "import-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/source"))
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()
    build(args.data, args.source)
