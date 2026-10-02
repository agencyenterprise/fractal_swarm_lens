"""Application-owned extraction. Credentials are read only from environment or stdin."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import gzip
import json
import os
from pathlib import Path
import time

import requests

REPO = "aidigestorg/ai-village"
GOAL = "b11a446c-869f-4cbb-b8dd-7e45c2a01735"


def fetch(output: Path, start: str, end: str):
    output.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or getpass.getpass("Hugging Face token: ")
    headers = {"Authorization": f"Bearer {token}"}
    revision_file = output / "revision.txt"
    if not revision_file.exists():
        response = requests.get(f"https://huggingface.co/api/datasets/{REPO}", headers=headers, timeout=60)
        response.raise_for_status()
        revision_file.write_text(response.json()["sha"])
    revision = revision_file.read_text().strip()

    def rows(table):
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{revision}/{table}.jsonl.gz"
        with requests.get(url, headers=headers, stream=True, timeout=180) as response:
            response.raise_for_status()
            with gzip.GzipFile(fileobj=response.raw) as stream:
                for line in stream:
                    yield json.loads(line)

    for table in ["agents", "chat_rooms", "villages", "village_goals", "agent_goals"]:
        (output / f"{table}.json").write_text(json.dumps(list(rows(table)), ensure_ascii=False))

    def extract(table):
        target = output / f"{table}-selected.json"
        if target.exists():
            return
        selected = []
        report = time.monotonic()
        for index, row in enumerate(rows(table), 1):
            if start <= row["created_at"] < end:
                selected.append(row)
            if time.monotonic() - report > 30:
                print(f"{table}: scanned {index:,}; retained {len(selected):,}", flush=True)
                report = time.monotonic()
        target.write_text(json.dumps(selected, ensure_ascii=False))
        print(f"{table}: retained {len(selected):,}", flush=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(extract, ["events", "chat_messages", "computer_use_turns", "agent_memories"]))
    turns = json.loads((output / "computer_use_turns-selected.json").read_text())
    session_ids = {row["session_id"] for row in turns}
    target = output / "computer_use_sessions-selected.json"
    sessions = json.loads(target.read_text()) if target.exists() else []
    known = {row["id"] for row in sessions}
    if not session_ids <= known:
        sessions = [row for row in rows("computer_use_sessions")
                    if row["id"] in session_ids or start <= row["created_at"] < end]
        target.write_text(json.dumps(sessions, ensure_ascii=False))
    metadata = {"repository": REPO, "revision": revision, "goal_id": GOAL,
                "start": start, "end": end, "selection": "Opening day excerpt of one village-wide goal; time-window membership, not a native task foreign key.",
                "initial_memory": "No pre-window memory snapshot was retrieved; unknown until the first observed revision.",
                "execution": "Recorded observations only; original environment and model internals are not restored."}
    (output / "selection.json").write_text(json.dumps(metadata, indent=2))
    print("Selected session parents resolved; pinned revision", revision, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/source"))
    parser.add_argument("--start", default="2026-01-05 17:34:13.998")
    parser.add_argument("--end", default="2026-01-06 00:00:00")
    args = parser.parse_args()
    fetch(args.output, args.start, args.end)
