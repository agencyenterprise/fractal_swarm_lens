"""Measure SQLite history storage on a synthetic, text-heavy multi-agent run built through the public Framework API.

The run has N agents talking in one channel with LLM-like messages of 1-2 KB. Like ACIArena's conversation
memories, each agent's memory is rewritten with the whole episode transcript so far, so every revision repeats
all prior turns of its episode. A few forks (one nested) continue the conversation after a prompt intervention.

    PYTHONPATH=src python tools/benchmark_storage.py --messages 10000 --seed 7

Results are written as JSON to benchmarks/results/<UTC timestamp>.json with the seed, arguments, git commit,
a fingerprint of the measured source code, the Python and SQLite versions, and the exact command.
"""
import argparse
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import random
import resource
import shlex
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

import swarm_lens
from swarm_lens import Fact, Framework
from swarm_lens.adapters.sqlite import SQLiteHistory
from swarm_lens.application.framework import IterableSource

ROOT = Path(__file__).resolve().parents[1]
STARTED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
WORDS = """the a model agent answer because therefore however we should consider evidence claim reasoning step
first second finally result value function returns error test case input output previous message argue
propose disagree agree point solution approach assume given hence verify check compute total number equals
debate round other agents said correct incorrect mistake revise final position confidence likely unlikely
data source context task goal plan tool call observation memory prompt instruction safety policy risk
analysis summary example note important detail specific general case condition constraint requirement
""".split()


class BudgetExceeded(Exception):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--agents", type=int, default=4)
    parser.add_argument("--messages", type=int, default=100_000, help="messages on the recorded branch")
    parser.add_argument("--min-chars", type=int, default=1000)
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument("--episode-length", type=int, default=12,
                        help="messages per episode; a conversation memory holds its episode so far")
    parser.add_argument("--memory-every", type=int, default=2,
                        help="an agent rewrites its conversation memory after every Nth own turn")
    parser.add_argument("--forks", type=int, default=3, help="forks of the recorded branch, plus one nested fork")
    parser.add_argument("--fork-messages", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=100, help="Framework.ingest batch size")
    parser.add_argument("--cursors", type=int, default=50, help="random cursors to reconstruct")
    parser.add_argument("--max-seconds", type=float, default=900, help="ingest time budget")
    parser.add_argument("--max-db-gb", type=float, default=40, help="database size budget during ingest")
    parser.add_argument("--work-dir", type=Path, help="parent directory for the benchmark database")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmarks" / "results")
    return parser.parse_args(argv)


def llm_text(rng: random.Random, low: int, high: int) -> str:
    """Markdown-ish prose with LLM-like structure; random words keep compression ratios close to real English."""
    target, parts = rng.randint(low, high), []
    while sum(map(len, parts)) < target:
        sentence = " ".join(rng.choice(WORDS) for _ in range(rng.randint(8, 22)))
        prefix = rng.choice(["", "", "", "- ", "**Point:** ", "1. "])
        parts.append(f"{prefix}{sentence.capitalize()}.{rng.choice([' ', ' ', chr(10) * 2])}")
    return "".join(parts)[:target]


def timestamp(seconds: int) -> str:
    return (STARTED_AT + timedelta(seconds=seconds)).isoformat()


def setup_facts(args: argparse.Namespace) -> list[Fact]:
    agents = [f"agent-{index}" for index in range(args.agents)]
    return [Fact("environment.updated", {"task": "Synthetic storage benchmark debate", "goal": "Agree on an answer"},
                 timestamp(0)),
            *[Fact("agent.added", {"id": agent, "name": f"Agent {agent[-1]}", "model": "gpt-bench",
                                   "system_prompt": "You are a careful debater."}, timestamp(0)) for agent in agents],
            Fact("channel.created", {"id": "debate", "name": "Debate", "members": agents}, timestamp(0))]


def conversation(args: argparse.Namespace, rng: random.Random, count: int, prefix: str) -> Iterator[Fact]:
    """`count` messages round-robin; each memory revision repeats its episode's transcript so far."""
    turns = [0] * args.agents
    episode: list[str] = []
    for index in range(count):
        if index % args.episode_length == 0:
            episode = []
        speaker = index % args.agents
        content = llm_text(rng, args.min_chars, args.max_chars)
        at = timestamp(index + 1)
        yield Fact("message.created", {"id": f"{prefix}m-{index}", "channel_id": "debate",
                                       "sender_id": f"agent-{speaker}", "content": content}, at)
        episode.append(f"agent-{speaker}: {content}")
        turns[speaker] += 1
        if turns[speaker] % args.memory_every == 0:
            yield Fact("memory.written", {"id": f"memory-{speaker}", "owner_id": f"agent-{speaker}",
                                          "content": "\n\n".join(episode)}, at)


def database_bytes(path: Path) -> int:
    return sum(file.stat().st_size for file in path.parent.glob(path.name + "*"))


def budgeted(facts: Iterator[Fact], path: Path, args: argparse.Namespace) -> Iterator[Fact]:
    started, limit = time.perf_counter(), args.max_db_gb * 1e9
    for count, fact in enumerate(facts, 1):
        yield fact
        if count % 1000 == 0:
            elapsed, size = time.perf_counter() - started, database_bytes(path)
            if count % 10_000 == 0:
                print(f"ingested {count} facts in {elapsed:.0f}s, database {size / 1e6:.0f} MB", file=sys.stderr)
            if elapsed > args.max_seconds or size > limit:
                raise BudgetExceeded(f"budget exceeded after {count} facts: {elapsed:.0f}s, {size / 1e9:.2f} GB")


def build_run(framework: Framework, args: argparse.Namespace, path: Path) -> tuple[str, dict]:
    rng = random.Random(args.seed)
    root = framework.create_run("Storage benchmark")
    framework.ingest(root.id, IterableSource(setup_facts(args)), batch_size=args.batch_size)
    started = time.perf_counter()
    recorded = budgeted(conversation(args, rng, args.messages, ""), path, args)
    framework.ingest(root.id, IterableSource(recorded), batch_size=args.batch_size)
    recorded_seconds = time.perf_counter() - started
    started = time.perf_counter()
    head = framework.store.branch(root.id).head
    forks = [framework.fork(root.id, head * k // (args.forks + 1), f"Fork {k}") for k in range(1, args.forks + 1)]
    if forks:
        nested_parent = forks[0]
        continue_fork(framework, args, rng, nested_parent, "f1-")
        nested_head = framework.store.branch(nested_parent.id).head
        forks.append(framework.fork(nested_parent.id, nested_head - args.fork_messages // 2, "Nested fork"))
    for number, fork in enumerate(forks[1:], 2):
        continue_fork(framework, args, rng, fork, f"f{number}-")
    return root.run_id, {"recorded_seconds": round(recorded_seconds, 3),
                     "fork_seconds": round(time.perf_counter() - started, 3)}


def continue_fork(framework: Framework, args: argparse.Namespace, rng: random.Random, fork, prefix: str) -> None:
    head = framework.store.branch(fork.id).head
    framework.intervene(fork.id, "agent.updated", {"id": "agent-0", "system_prompt": "Verify before agreeing."}, head)
    framework.ingest(fork.id, IterableSource(conversation(args, rng, args.fork_messages, prefix)),
                     batch_size=args.batch_size)


def table_bytes(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        rows = db.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC").fetchall()
    return dict(rows)


def reconstruct_times(framework: Framework, run_id: str, args: argparse.Namespace) -> dict:
    rng = random.Random(f"{args.seed}-cursors")
    branches = framework.store.branches(run_id)
    seconds = []
    for _ in range(args.cursors):
        branch = rng.choice(branches)
        cursor = rng.randint(0, branch.head)
        started = time.perf_counter()
        framework.state(branch.id, cursor)
        seconds.append(time.perf_counter() - started)
    return {"n": len(seconds), "p50_s": round(statistics.median(seconds), 4),
            "p95_s": round(statistics.quantiles(seconds, n=20, method="inclusive")[18], 4), "max_s": round(max(seconds), 4)}


def timeline_time(framework: Framework, branch_id: str) -> dict:
    from fastapi.testclient import TestClient
    from swarm_lens.web.api import create_app
    client = TestClient(create_app(framework))
    started = time.perf_counter()
    response = client.get(f"/api/branches/{branch_id}/timeline")
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    return {"seconds": round(elapsed, 3), "payload_bytes": len(response.content)}


def round_trip(framework: Framework, run_id: str, work: Path) -> dict:
    started = time.perf_counter()
    bundle = json.dumps(framework.export_run(run_id), ensure_ascii=False)
    exported = time.perf_counter()
    target = work / "imported.sqlite"
    Framework(SQLiteHistory(target)).import_run(json.loads(bundle))
    finished = time.perf_counter()
    return {"export_s": round(exported - started, 3), "import_s": round(finished - exported, 3),
            "bundle_bytes": len(bundle.encode()), "imported_database_bytes": database_bytes(target)}


def peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def provenance(args: argparse.Namespace) -> dict:
    def git(*command):
        return subprocess.run(["git", "-C", str(ROOT), *command], capture_output=True, text=True,
                              check=True).stdout.strip()
    package = Path(swarm_lens.__file__).parent
    source = hashlib.sha256()
    for file in sorted(package.rglob("*")):
        if file.suffix in (".py", ".sql"):
            source.update(file.relative_to(package).as_posix().encode() + file.read_bytes())
    return {"seed": args.seed, "args": {key: str(value) if isinstance(value, Path) else value
                                        for key, value in vars(args).items()},
            "command": shlex.join([sys.executable, *sys.argv]), "git_commit": git("rev-parse", "HEAD"),
            "git_dirty": bool(git("status", "--porcelain")), "package_path": str(package),
            "source_sha256": source.hexdigest(), "python": sys.version, "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform()}


def measure(args: argparse.Namespace, work: Path) -> dict:
    path = work / "history.sqlite"
    framework = Framework(SQLiteHistory(path))
    started = time.perf_counter()
    try:
        run_id, phases = build_run(framework, args, path)
    except BudgetExceeded as exc:
        return {"status": "budget_exceeded", "detail": str(exc), "seconds": round(time.perf_counter() - started, 1),
                "database_bytes": database_bytes(path), "tables_bytes": table_bytes(path),
                "peak_rss_bytes": peak_rss_bytes()}
    ingest = {"seconds": round(time.perf_counter() - started, 3), **phases}
    events = sum(branch.head - branch.fork_position for branch in framework.store.branches(run_id))
    tables = table_bytes(path)
    result = {"status": "completed", "events_stored": events, "ingest": ingest,
              "database_bytes": database_bytes(path), "tables_bytes": tables,
              "snapshot_bytes": tables.get("snapshots", 0), "peak_rss_after_ingest_bytes": peak_rss_bytes()}
    result["reconstruct"] = reconstruct_times(framework, run_id, args)
    result["timeline"] = timeline_time(framework, framework.store.branches(run_id)[0].id)
    result["round_trip"] = round_trip(framework, run_id, work)
    result["peak_rss_bytes"] = peak_rss_bytes()
    return result


def main(argv: list[str] | None = None) -> Path:
    args = parse_args(argv)
    started_at = datetime.now(timezone.utc)
    record = {"started_at": started_at.isoformat(), **provenance(args)}
    work = Path(tempfile.mkdtemp(prefix="swarm-lens-bench-", dir=args.work_dir))
    try:
        record["result"] = measure(args, work)
    finally:
        shutil.rmtree(work)
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(record, indent=2) + "\n")
    print(output)
    return output


if __name__ == "__main__":
    main()
