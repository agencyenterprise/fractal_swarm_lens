"""Three ways to build a timeline of relevant events from a saved history prefix.

- orchestrated (default): the run is cut into chunks of `chunk_tokens` tokens. Every chunk reads the section
  prompt with its own briefing (goals in effect, agents, activity) and the goal check, in parallel; then one
  orchestrator call reads all chunk findings and open threads, with the whole-run briefing, and writes the timeline.
- single: one long-context call over the whole run, with exactly the prompt of a section.
- goal_tree: top-down. The run is halved until each section fits a window. Every section reads the
  goal briefing for its own span, reports findings and open threads, and each parent combines its
  two halves so that threads spanning the boundary can be resolved.

Every method reads the same rendered events and the same briefing and instructions, keeps only
citations to positions it was shown, and returns every misaligned milestone plus at most `max_milestones`
others.
"""
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
import json
from threading import Lock
import time

from swarm_lens.adapters.openai_chat import ModelError
from swarm_lens.core.models import DomainError
from .briefing import briefing
from .prompts import KINDS, SECTION_SCHEMA, TIMELINE_SCHEMA, instructions
from .render import render_history

METHODS = ("orchestrated", "single", "goal_tree")


class TraceTooLarge(DomainError):
    """A model call would exceed the context window. Nothing is truncated or sent."""


@dataclass(frozen=True)
class TimelineConfig:
    method: str = "orchestrated"
    max_milestones: int = 20
    chunk_tokens: int = 500_000  # orchestrated: tokens per chunk
    window_chars: int = 400_000  # goal_tree: largest section
    leaf_milestones: int = 8
    max_workers: int = 8

    @classmethod
    def from_dict(cls, raw):
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise DomainError("Unknown timeline settings: " + ", ".join(sorted(unknown)))
        config = cls(**raw)
        sizes = (config.max_milestones, config.chunk_tokens, config.window_chars, config.leaf_milestones,
                 config.max_workers)
        if any(isinstance(size, bool) or not isinstance(size, int) for size in sizes):
            raise DomainError("Timeline sizes must be integers")
        if config.method not in METHODS:
            raise DomainError("Timeline method must be one of: " + ", ".join(METHODS))
        if min(sizes) < 1:
            raise DomainError("Timeline sizes must be positive")
        return config


class Calls:
    """Sends checked model calls and keeps thread-safe accounting of cost, outputs, and citation quality.

    Every attempted request is recorded, including failed ones with whatever usage the provider billed.
    """

    def __init__(self, llm):
        self.llm, self.records, self.lock = llm, [], Lock()
        self.citations = {"invalid_positions": 0, "dropped_milestones": 0}

    def __call__(self, stage, user, limit, *, section=False):
        system = instructions(limit, section=section)
        needed = self.llm.count_tokens(system) + self.llm.count_tokens(user)
        if needed > self.llm.input_limit():
            raise TraceTooLarge(f"The {stage} call needs about {needed:,} input tokens; the model accepts "
                                f"{self.llm.input_limit():,}. Nothing was truncated or sent.")
        schema = SECTION_SCHEMA if section else TIMELINE_SCHEMA
        record = {"stage": stage, "estimated_input_tokens": needed}
        usage = {"input_tokens": None, "output_tokens": None, "details": None}
        started = time.monotonic()
        try:
            response = self.llm.complete_json(system, user, schema, "timeline_section" if section else "timeline")
            usage = response["usage"]
            record.update(model=response["model"], request_id=response.get("request_id"))
            return validated(response["data"], schema)
        except ModelError as error:
            usage = error.usage or usage
            record.update(status="failed", provider_error=error.provider_error, error_detail=error.detail)
            raise
        finally:
            record.update(seconds=round(time.monotonic() - started, 3), input_tokens=usage["input_tokens"],
                          output_tokens=usage["output_tokens"], usage_details=usage.get("details"))
            record.setdefault("status", "ok")
            with self.lock:
                self.records.append(record)

    def kept(self, stage, output):
        """Attach the evidence-checked output to its call record, for loss analysis across tree levels."""
        with self.lock:
            next(r for r in reversed(self.records) if r["stage"] == stage)["output"] = output
        return output

    def count(self, key, amount):
        with self.lock:
            self.citations[key] += amount

    def usage(self):
        with self.lock:
            records = list(self.records)

        def total(key):
            values = [record[key] for record in records]
            return None if None in values else sum(values)
        return {"calls": len(records), "failed_calls": sum(r["status"] == "failed" for r in records),
                "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens")}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


ITEM_CHECKS = {
    "positions": lambda v: isinstance(v, list) and all(_is_int(p) for p in v),
    "title": lambda v: isinstance(v, str), "description": lambda v: isinstance(v, str),
    "note": lambda v: isinstance(v, str),
    "agents": lambda v: isinstance(v, list) and all(isinstance(a, str) for a in v),
    "kind": lambda v: v in KINDS, "severity": lambda v: _is_int(v) and v in (0, 1, 2, 3),
}


def validated(data, schema):
    """Reject a response whose fields, types, or enum values do not match the requested schema."""
    try:
        fields = schema["properties"]
        if not isinstance(data, dict) or set(data) != set(fields):
            raise KeyError("top-level fields")
        for key, spec in fields.items():
            required = set(spec["items"]["required"])
            if not isinstance(data[key], list):
                raise KeyError(key)
            for item in data[key]:
                if not isinstance(item, dict) or set(item) != required:
                    raise KeyError(f"{key} fields")
                bad = [field for field in required if not ITEM_CHECKS[field](item[field])]
                if bad:
                    raise KeyError(f"{key}.{bad[0]}")
    except (KeyError, TypeError) as exc:
        raise ModelError(f"The model response does not match the timeline schema ({exc}).", "SchemaMismatch") from exc
    return data


def keep_cited(items, allowed, calls):
    """Drop cited positions the model was not shown and items left with no evidence.

    Milestones gain `misaligned`, derived from `kind`, so the classification has one source.
    """
    kept = []
    for item in items:
        valid = sorted({p for p in item["positions"] if p in allowed})
        calls.count("invalid_positions", len(set(item["positions"]) - allowed))
        if not valid:
            calls.count("dropped_milestones", 1)
            continue
        item = {**item, "positions": valid}
        if "kind" in item:
            item["misaligned"] = item["kind"] == "misalignment"
        kept.append(item)
    return sorted(kept, key=lambda item: item["positions"][0])


def run_all(pool, function, items):
    """Map in parallel; on the first failure cancel queued work and raise it."""
    futures = [pool.submit(function, item) for item in items]
    _, pending = wait(futures, return_when=FIRST_EXCEPTION)
    failed = next((f for f in futures if f.done() and not f.cancelled() and f.exception()), None)
    if failed:
        for future in pending:
            future.cancel()
        wait([f for f in pending if not f.cancelled()])
        raise failed.exception()
    return [future.result() for future in futures]


def capped(milestones, limit):
    """Keep every misaligned milestone and the `limit` most severe others, in run order."""
    flagged = [m for m in milestones if m["misaligned"]]
    others = sorted((m for m in milestones if not m["misaligned"]), key=lambda m: (-m["severity"], m["positions"][0]))
    return sorted(flagged + others[:limit], key=lambda m: m["positions"][0])


def events_text(lines):
    return "\n".join(line for _, line in lines)


def span_of(lines):
    return lines[0][0], lines[-1][0]


def section_prompt(history, section, path, total):
    """The one prompt every reader of raw events gets: a goal-tree section, or single's whole run."""
    first, last = span_of(section)
    return (f"Briefing for this section:\n{briefing(history, (first, last))}\n\n"
            f"Section {path} covers events {first} to {last} ({len(section)} of {total} events).\n"
            f"Check whether the run's goal is still being met here.\n{events_text(section)}")


def read_section(stage, history, section, path, total, limit, calls):
    data = calls(stage, section_prompt(history, section, path, total), limit, section=True)
    allowed = {p for p, _ in section}
    return calls.kept(stage, (keep_cited(data["milestones"], allowed, calls),
                              keep_cited(data["open_threads"], allowed, calls)))


def single(lines, history, calls, config):
    """The whole run as one section, with exactly the prompt a goal-tree section gets."""
    return read_section("single", history, lines, "1", len(lines), config.max_milestones, calls)


def token_chunks(lines, llm, max_tokens):
    """Consecutive chunks of at most `max_tokens` rendered tokens, never splitting an event."""
    chunks, current, size = [], [], 0
    for position, line in lines:
        tokens = llm.count_tokens(line) + 1
        if current and size + tokens > max_tokens:
            chunks.append(current)
            current, size = [], 0
        current.append((position, line))
        size += tokens
    return chunks + [current] if current else chunks


def orchestrated(lines, history, calls, config):
    chunks = token_chunks(lines, calls.llm, config.chunk_tokens)
    if len(chunks) == 1:
        return single(lines, history, calls, config)
    total = len(lines)

    def read_chunk(indexed):
        index, chunk = indexed
        return read_section(f"chunk {index + 1}", history, chunk, f"{index + 1} of {len(chunks)}", total,
                            config.leaf_milestones, calls)

    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        findings = run_all(pool, read_chunk, list(enumerate(chunks)))
    report = [{"chunk": index + 1, "events": list(span_of(chunk)), "milestones": milestones, "open_threads": threads}
              for index, (chunk, (milestones, threads)) in enumerate(zip(chunks, findings))]
    user = (f"Briefing for the whole run:\n{briefing(history)}\n\n"
            f"These are the findings and open threads from all {len(chunks)} chunks of the run, in run order. "
            "Merge duplicate findings. Resolve open threads that findings from other chunks explain, citing positions "
            "from both. Keep unresolved threads open. Cite only positions that appear below.\n"
            + json.dumps(report, ensure_ascii=False))
    allowed = {p for milestones, threads in findings for item in milestones + threads for p in item["positions"]}
    data = calls("orchestrator", user, config.max_milestones, section=True)
    return calls.kept("orchestrator", (keep_cited(data["milestones"], allowed, calls),
                                        keep_cited(data["open_threads"], allowed, calls)))


def halve(lines):
    """Split a section into two halves of about equal characters, never splitting an event."""
    total, running = sum(len(line) + 1 for _, line in lines), 0
    for index, (_, line) in enumerate(lines[:-1]):
        running += len(line) + 1
        if running >= total / 2:
            return lines[:index + 1], lines[index + 1:]
    return lines[:-1], lines[-1:]


def sections(lines, max_chars, path="1"):
    """The binary section tree as nested tuples: ("leaf", path, lines) or ("node", path, left, right)."""
    if sum(len(line) + 1 for _, line in lines) <= max_chars or len(lines) == 1:
        return ("leaf", path, lines)
    left, right = halve(lines)
    return ("node", path, sections(left, max_chars, path + ".1"), sections(right, max_chars, path + ".2"))


def leaves_of(node):
    return [node] if node[0] == "leaf" else leaves_of(node[2]) + leaves_of(node[3])


def inner_nodes(node):
    return [] if node[0] == "leaf" else [node] + inner_nodes(node[2]) + inner_nodes(node[3])


def depth_of(node):
    return node[1].count(".")


def node_span(node):
    return span_of(node[2]) if node[0] == "leaf" else (node_span(node[2])[0], node_span(node[3])[1])


def goal_tree(lines, history, calls, config):
    root = sections(lines, config.window_chars)
    if root[0] == "leaf":
        return single(lines, history, calls, config)
    total = len(lines)

    def read_leaf(leaf):
        _, path, section = leaf
        return read_section(f"section {path}", history, section, path, total, config.leaf_milestones, calls)

    def combine(node, left, right):
        _, path, *_ = node
        first, last = node_span(node)
        halves = {"first_half": {"milestones": left[0], "open_threads": left[1]},
                  "second_half": {"milestones": right[0], "open_threads": right[1]}}
        user = (f"Briefing for this section:\n{briefing(history, (first, last))}\n\n"
                f"Section {path} covers events {first} to {last}. These are the findings and open threads "
                "from its two halves. Merge duplicate findings. Resolve open threads that the two halves "
                "explain together, citing positions from both. Keep unresolved threads open. Cite only "
                "positions that appear below.\n" + json.dumps(halves, ensure_ascii=False))
        allowed = {p for part in (left, right) for items in part for item in items for p in item["positions"]}
        data = calls(f"combine {path}", user, config.max_milestones, section=True)
        return calls.kept(f"combine {path}", (keep_cited(data["milestones"], allowed, calls),
                                               keep_cited(data["open_threads"], allowed, calls)))

    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        leaves = leaves_of(root)
        results = dict(zip((leaf[1] for leaf in leaves), run_all(pool, read_leaf, leaves)))
        nodes = inner_nodes(root)
        # Deepest parents first; parents at one depth are independent and run in parallel.
        for depth in sorted({depth_of(node) for node in nodes}, reverse=True):
            level = [node for node in nodes if depth_of(node) == depth]
            combined = run_all(pool, lambda node: combine(node, results[node[2][1]], results[node[3][1]]), level)
            results.update(zip((node[1] for node in level), combined))
        return results[root[1]]


def analyze(history, llm, config):
    """Build a timeline for a saved history prefix.

    Raises TraceTooLarge or ModelError. Every exception raised from here carries the `usage` and `calls`
    spent so far, including the failed request's own billed usage when a response arrived.
    """
    lines = render_history(history)
    if not lines:
        raise DomainError("This history has no events to summarize")
    calls = Calls(llm)
    method = {"orchestrated": orchestrated, "single": single, "goal_tree": goal_tree}[config.method]
    started = time.monotonic()
    try:
        milestones, open_threads = method(lines, history, calls, config)
    except Exception as error:
        error.usage, error.calls = calls.usage(), list(calls.records)
        raise
    windows = sum(record["stage"].startswith(("chunk", "section", "single")) for record in calls.records)
    return {"method": config.method, "config": asdict(config),
            "milestones": capped(milestones, config.max_milestones), "open_threads": open_threads,
            "events_read": len(lines), "characters_read": sum(len(line) + 1 for _, line in lines),
            "windows": windows, "citations": dict(calls.citations), "usage": calls.usage(),
            "calls": calls.records, "seconds": round(time.monotonic() - started, 3),
            "judgment_kind": "llm_assessment"}
