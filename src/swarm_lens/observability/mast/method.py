from functools import lru_cache
import hashlib
from importlib.resources import files
import json
import re

from swarm_lens.core.models import DomainError
from .trace import encode_trace, FORMAT
from .evidence import generate_evidence


@lru_cache(maxsize=1)
def assets():
    return json.loads(files(__package__).joinpath("assets.json").read_text())


def make_prompt(trace):
    values = {"trace": trace, "definitions": assets()["definitions"], "examples": assets()["examples"]}
    return "".join(part["text"] if "text" in part else values[part["slot"]]
                   for part in assets()["prompt_parts"])


def parse_response(raw):
    """Never turn an absent or ambiguous answer into a negative classification."""
    cleaned = raw.replace("**", "").replace("@@", "").strip()
    answers = {}
    pattern = r"(?<![\w.])(?:C\.?\s*)?([123]\.\d+)\b([^\n]*?)(?=(?:[123]\.\d+\b)|\n|$)"
    for match in re.finditer(pattern, cleaned):
        answer = match.group(2).rsplit(":", 1)[-1].strip()
        value = re.fullmatch(r"(yes|no)[.!]?", answer, re.I)
        values = [value.group(1)] if value else []
        answers.setdefault(match.group(1), []).append(values)
    labels, warnings = [], []
    for category in assets()["categories"]:
        rows = answers.get(category["code"], [])
        value = rows[0][0].lower() == "yes" if len(rows) == 1 and len(rows[0]) == 1 else None
        if value is None:
            warnings.append(f"Missing or ambiguous answer for {category['code']}")
        labels.append({"code": category["code"], "label": category["label"], "group": category["group"],
                       "present": value})
    completion = re.search(r"(?:^|\n)\s*B[.:]\s*([^\n]*?)(?=\n\s*C[.:]|$)", cleaned, re.I | re.S)
    values = re.findall(r"\b(yes|no)\b", completion.group(1), re.I) if completion else []
    task_completed = values[0].lower() == "yes" if len(values) == 1 else None
    summary = re.search(r"(?:^|\n)\s*A[.:]\s*(.*?)(?=\n\s*B[.:])", cleaned, re.I | re.S)
    if task_completed is None:
        warnings.append("Missing or ambiguous task-completion answer")
    if not summary or not summary.group(1).strip():
        warnings.append("Missing summary")
    extras = sorted(set(answers) - {c["code"] for c in labels})
    if extras:
        warnings.append("Ignored non-taxonomy codes: " + ", ".join(extras))
    return {"summary": summary.group(1).strip() if summary else "", "task_completed": task_completed,
            "labels": labels, "warnings": warnings, "parse_status": "needs_review" if warnings else "complete"}


def history_document(history, completeness="unknown"):
    if not history:
        raise DomainError("Choose a saved trace with at least one event")
    if not any(e.kind.startswith(("message.", "tool.", "memory.")) or
               (e.kind == "observation.recorded" and e.data.get("content")) for e in history):
        raise DomainError("Choose a saved prefix containing conversation or execution data")
    if completeness not in {"unknown", "complete", "partial"}:
        raise DomainError("Unknown trace completeness")
    # Do not add run metadata, source labels, or previous analysis records to the judge input.
    records = [{"event_id": e.id, "position": e.position, "kind": e.kind, "at": e.occurred_at,
                "data": e.data} for e in history]
    return {"trace_completeness": completeness,
            "boundary_note": "The end of this saved snapshot alone is not evidence of task termination.",
            "events": records}


def history_trace(history, completeness="unknown"):
    return json.dumps(encode_trace(history_document(history, completeness)), ensure_ascii=False)


class MastPlugin:
    id, version = "mast", "0.3.0"

    def __init__(self, judge, *, max_trace_characters=4_000_000):
        self.judge = judge
        self.max_trace_characters = max_trace_characters

    def prepare_input(self, history, config):
        document = history_document(history, config.get("completeness", "unknown"))
        trace = json.dumps(encode_trace(document), ensure_ascii=False)
        prompt = make_prompt(trace)
        preview = {"format": FORMAT, "event_count": len(history),
                   "message_count": sum(e.kind == "message.created" for e in history),
                   "memory_snapshot_count": sum(e.kind == "memory.written" for e in history),
                   "original_characters": len(json.dumps(document, ensure_ascii=False, sort_keys=True)),
                   "prepared_characters": len(trace), "max_trace_characters": self.max_trace_characters,
                   "can_analyze": True, "reason": None}
        if len(trace) > self.max_trace_characters:
            preview.update(can_analyze=False, reason=(
                f"The complete trace still has {len(trace):,} characters after removing duplicate storage. "
                f"The configured limit is {self.max_trace_characters:,}. Nothing was truncated or sent."))
        elif hasattr(self.judge, "input_budget"):
            preview.update(self.judge.input_budget(prompt))
        return trace, prompt, preview

    def prepare(self, history, config):
        trace, prompt, preview = self.prepare_input(history, config)
        if not preview["can_analyze"]:
            raise DomainError(preview["reason"])
        return trace, prompt

    def evaluate(self, trace, prompt):
        response = self.judge.complete(prompt)
        raw = response["text"]
        output = parse_response(raw)
        if response.get("finish_reason") != "stop":
            output["warnings"].append("Provider did not finish normally; all classifications need review")
            output["parse_status"] = "needs_review"
        output.update({"raw_response": raw, "judge": {k: v for k, v in response.items() if k != "text"},
                       "upstream": {key: assets()[key] for key in ("repository", "revision", "file_sha256", "upstream_notes")},
                       "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                       "trace_sha256": hashlib.sha256(trace.encode()).hexdigest(),
                       "judgment_kind": "llm_assessment", "mode": "saved_trace", "human_reviewed": False})
        output["evidence"] = generate_evidence(self.judge, trace, output["labels"], assets()["categories"])
        return output

    def run(self, context, config):
        trace, prompt = self.prepare(context.history(), config)
        return self.evaluate(trace, prompt)
