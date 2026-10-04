"""What plugins return: findings about a branch's history, and an optional plugin-specific report."""
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
import json
import math
from typing import Any

from .models import DomainError

FINDINGS_FORMAT = "swarm-lens.findings/v1"


@dataclass(frozen=True)
class Metric:
    """A number describing the history at one event position, optionally for one agent."""
    seq: int
    name: str
    value: float
    agent_id: str | None = None
    plugin: str = ""


@dataclass(frozen=True)
class Annotation:
    """A labelled span of event positions, inclusive, optionally scored and citing the events behind it."""
    seq_from: int
    seq_to: int
    label: str
    agent_id: str | None = None
    score: float | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    cited_event_ids: tuple[str, ...] = ()
    plugin: str = ""


@dataclass(frozen=True)
class Report:
    """A plugin's own JSON result for a custom view; at most one per analysis."""
    data: Mapping[str, Any]


Finding = Metric | Annotation


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise DomainError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def _position(value: Any, start: int, end: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not start <= value <= end:
        raise DomainError(f"{what} must be an event position from {start} to {end}, got {value!r}")
    return value


def _json(value: Any, what: str) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DomainError(f"{what} must be JSON serializable without NaN or infinity") from exc


def encode_output(items: list[Any], plugin: str, start: int, end: int) -> dict[str, Any]:
    """Validate a plugin's returned items and stamp them with its ID; anything malformed fails the analysis."""
    metrics, annotations, reports = [], [], []
    for item in items:
        if isinstance(item, Metric):
            if not item.name.strip():
                raise DomainError("A metric needs a name")
            metrics.append({**asdict(item), "seq": _position(item.seq, start, end, "Metric seq"),
                            "value": _number(item.value, f"Metric {item.name!r}"), "plugin": plugin})
        elif isinstance(item, Annotation):
            seq_from = _position(item.seq_from, start, end, "Annotation seq_from")
            seq_to = _position(item.seq_to, seq_from, end, "Annotation seq_to")
            if not item.label.strip():
                raise DomainError("An annotation needs a label")
            annotations.append({**asdict(item), "seq_from": seq_from, "seq_to": seq_to,
                                "score": None if item.score is None else _number(item.score, "Annotation score"),
                                "data": _json(dict(item.data), "Annotation data"),
                                "cited_event_ids": list(item.cited_event_ids), "plugin": plugin})
        elif isinstance(item, Report):
            reports.append(_json(dict(item.data), "A report"))
        else:
            raise DomainError(f"Plugin {plugin} returned {type(item).__name__}; expected Metric, Annotation or Report")
    if len(reports) > 1:
        raise DomainError(f"Plugin {plugin} returned more than one report")
    return {"format": FINDINGS_FORMAT, "metrics": metrics, "annotations": annotations,
            "report": reports[0] if reports else None}


def decode_findings(output: Mapping[str, Any]) -> tuple[list[Metric], list[Annotation]]:
    if output.get("format") != FINDINGS_FORMAT:
        raise DomainError(f"Unsupported findings format: {output.get('format')!r}")
    metrics = [Metric(**item) for item in output["metrics"]]
    annotations = [Annotation(**{**item, "cited_event_ids": tuple(item["cited_event_ids"])})
                   for item in output["annotations"]]
    return metrics, annotations
