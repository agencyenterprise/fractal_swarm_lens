"""Counting, binning and spike detection for HTTP calls per website.

A call is a network-like tool call (a browser or fetch tool, an execution tool whose command or code makes a
request, or any tool with an explicit `url` argument) or an observation with a `url` field. URLs merely
mentioned elsewhere (file edits, messages, results) do not count. Each website is counted once per call, so a
call's started and completed events, or several URLs on one site, count once. See docs/observability/http_calls.
"""
from dataclasses import asdict, dataclass
from math import ceil, sqrt
from datetime import datetime, timezone
from statistics import median
import re
from urllib.parse import urlsplit

from swarm_lens.core.models import DomainError

URL = re.compile(r"https?://[^\s\"'<>`(){}\[\]\\|^]+", re.IGNORECASE)
SPIKE_EVENT_LIMIT = 50
URL_FIELDS = frozenset({"url", "uri", "href"})
NETWORK_TOOL = re.compile(r"navigat|brows|fetch|http|visit|goto|open_?url|scrap|crawl|download|curl|wget", re.I)
EXECUTION_TOOL = re.compile(r"bash|shell|exec|command|terminal|python|ipython|jupyter|script|code_?run|run_?code", re.I)
REQUEST = re.compile(
    r"\b(?:curl|wget|aria2c|httpie|https?\s+(?:get|post|put|patch|delete|head))\b"
    r"|\brequests\.(?:get|post|put|patch|delete|head|request|session)\b|\bhttpx\.|\burllib\.request\b|\burlopen\("
    r"|\baiohttp\b|\bfetch\(|\bgit\s+(?:clone|fetch|pull|ls-remote)\b", re.I)


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class HttpCallsConfig:
    bins: int = 40
    spike_min_calls: int = 3
    spike_mads: float = 4.0

    @classmethod
    def from_dict(cls, raw):
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise DomainError("Unknown HTTP calls settings: " + ", ".join(sorted(unknown)))
        config = cls(**raw)
        if not (_integer(config.bins) and _integer(config.spike_min_calls)
                and (_integer(config.spike_mads) or isinstance(config.spike_mads, float))):
            raise DomainError("HTTP calls bins and spike_min_calls must be integers; spike_mads a number")
        if not 2 <= config.bins <= 400 or config.spike_min_calls < 1 or config.spike_mads <= 0:
            raise DomainError("HTTP calls needs 2–400 bins, spike_min_calls ≥ 1 and spike_mads > 0")
        return config

    @classmethod
    def schema(cls):
        return {"type": "object", "additionalProperties": False, "properties": {
            "bins": {"type": "integer", "minimum": 2, "maximum": 400, "default": cls.bins},
            "spike_min_calls": {"type": "integer", "minimum": 1, "default": cls.spike_min_calls},
            "spike_mads": {"type": "number", "exclusiveMinimum": 0, "default": cls.spike_mads}}}


@dataclass(frozen=True)
class Call:
    domain: str
    position: int
    event_id: str
    at: str
    reported: bool


def website(url):
    """The URL's host, lowercased, without port, credentials or a leading "www."; None when it has no host."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".")
    return host[4:] if host.startswith("www.") else host


def strings(value, key=None):
    """(field name, text) for every string in a JSON-like value; list items keep their parent's field name."""
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from strings(item, name)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings(item, key)


def websites(texts):
    """Distinct websites named by URLs in the texts, in first-seen order."""
    found = {}
    for text in texts:
        for match in URL.finditer(text):
            if domain := website(match.group()):
                found.setdefault(domain, None)
    return list(found)


def tool_websites(data):
    """Websites a tool call requests: every argument URL of a browser or fetch tool, URLs in the command or code
    of an execution tool when that text makes a request, and explicit URL fields of any tool."""
    name, fields = data.get("tool_name") or "", list(strings(data.get("arguments")))
    if NETWORK_TOOL.search(name):
        return websites(text for _, text in fields)
    explicit = [text for key, text in fields if str(key).lower() in URL_FIELDS]
    requests = [text for _, text in fields if REQUEST.search(text)] if EXECUTION_TOOL.search(name) else []
    return websites(explicit + requests)


def observation_websites(data):
    candidates = (data.get("url"), (data.get("metadata") or {}).get("url"))
    return websites(value for value in candidates if isinstance(value, str))


def extract_calls(history):
    """One Call per (tool call id or observation event, website), at the call's first recorded event."""
    calls, seen = [], set()
    for event in history:
        data = event.data
        if event.kind.startswith("tool."):
            key, domains = ("tool", data.get("id") or event.id), tool_websites(data)
        elif event.kind == "observation.recorded":
            key, domains = ("event", event.id), observation_websites(data)
        else:
            continue
        reported = bool((data.get("metadata") or {}).get("reconstructed"))
        for domain in domains:
            if (key, domain) not in seen:
                seen.add((key, domain))
                calls.append(Call(domain, event.position, event.id, event.occurred_at, reported))
    return calls


def _timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def session_axis(history):
    """Event timestamps when they are usable, else positions: (axis, reason, coordinate by position)."""
    times = [_timestamp(event.occurred_at) for event in history]
    if None in times:
        reason = "some event timestamps are missing or unreadable"
    elif any(later < earlier for earlier, later in zip(times, times[1:])):
        reason = "event timestamps go backwards"
    elif times[-1] == times[0]:
        reason = "all events share one timestamp"
    else:
        return "time", "event timestamps (occurred_at)", dict(zip((event.position for event in history), times))
    return "position", f"event positions, because {reason}", {event.position: event.position for event in history}


def spike_threshold(counts, config):
    """Median and MAD resist the burst itself; √median and 1 keep sparse, Poisson-like traffic from spiking
    on chance clusters when the MAD is zero."""
    center = median(counts)
    mad = median(abs(count - center) for count in counts)
    spread = max(mad, sqrt(center), 1)
    return center, mad, max(config.spike_min_calls, center + config.spike_mads * spread)


def analyze_http_calls(history, raw_config=None):
    config = HttpCallsConfig.from_dict(raw_config or {})
    calls = extract_calls(history)
    result = {"config": asdict(config), "events_scanned": len(history), "total_calls": len(calls),
              "axis": None, "axis_reason": None, "bins": [], "domains": [], "spikes": [],
              "spike_rule": (f"A bin is a spike for a website when its calls ≥ max({config.spike_min_calls}, "
                             f"median + {config.spike_mads:g} × max(MAD, √median, 1)) of that website's calls "
                             "across all bins."),
              "interpretation": "Counts of recorded or reported HTTP targets; not verified network traffic."}
    if not calls:
        return result
    axis, reason, coordinate = session_axis(history)
    low, high = coordinate[history[0].position], coordinate[history[-1].position]
    # A position bin spans at least one whole position, so no bin is empty by construction.
    bins = config.bins if axis == "time" else max(1, min(config.bins, high - low))
    width = (high - low) / bins

    def bin_of(position):
        return min(bins - 1, int((coordinate[position] - low) / width)) if width else 0

    def bin_range(index):
        if axis == "time":
            start, end = (datetime.fromtimestamp(low + edge * width, timezone.utc).isoformat()
                          for edge in (index, index + 1))
            return {"start": start, "end": end}
        end = high if index == bins - 1 else ceil(low + (index + 1) * width) - 1
        return {"start": ceil(low + index * width), "end": end}

    result.update(axis=axis, axis_reason=reason, bins=[bin_range(index) for index in range(bins)])
    by_domain = {}
    for call in calls:
        by_domain.setdefault(call.domain, []).append(call)
    for domain, domain_calls in sorted(by_domain.items(), key=lambda item: (-len(item[1]), item[0])):
        members = [[] for _ in range(bins)]
        for call in domain_calls:
            members[bin_of(call.position)].append(call)
        counts = [len(member) for member in members]
        center, mad, threshold = spike_threshold(counts, config)
        peak = max(range(bins), key=counts.__getitem__)
        spike_bins = [index for index, count in enumerate(counts) if count >= threshold]
        result["domains"].append({
            "domain": domain, "total": len(domain_calls), "reported": sum(call.reported for call in domain_calls),
            "counts": counts, "peak_bin": peak, "peak_count": counts[peak], "spike_bins": spike_bins,
            "baseline": {"median": center, "mad": mad, "threshold": threshold},
            "first_seen": _place(domain_calls[0]), "last_seen": _place(domain_calls[-1])})
        for first, last in consecutive_runs(spike_bins):
            window = [call for index in range(first, last + 1) for call in members[index]]
            result["spikes"].append({
                "domain": domain, "first_bin": first, "last_bin": last, "count": len(window),
                "peak_count": max(counts[first:last + 1]), "threshold": threshold,
                "start": result["bins"][first]["start"], "end": result["bins"][last]["end"],
                "events": [_place(call) for call in window[:SPIKE_EVENT_LIMIT]]})
    result["spikes"].sort(key=lambda spike: (-spike["peak_count"] / spike["threshold"], spike["first_bin"]))
    return result


def consecutive_runs(indices):
    """[(first, last)] for each run of consecutive indices: one burst that straddles bins is one spike."""
    runs = []
    for index in indices:
        if runs and runs[-1][1] == index - 1:
            runs[-1][1] = index
        else:
            runs.append([index, index])
    return [tuple(run) for run in runs]


def _place(call):
    return {"position": call.position, "event_id": call.event_id, "at": call.at}
