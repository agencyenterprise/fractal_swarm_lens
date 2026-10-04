"""The portable run bundle: a run's branch tree, own events, and comment threads keyed without database ids."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any

from swarm_lens.core.models import Branch, Comment, DomainError, Event, Fact, comment_author, comment_text, utc_now
from .ports import RunContents

FORMAT = "swarm-lens.run"
VERSION = 1
MAX_BRANCHES = 256
MAX_FORK_DEPTH = 64
MAX_EVENTS = 200_000


@dataclass(frozen=True)
class BundleBranch:
    key: str
    name: str
    parent_key: str | None
    fork_position: int
    facts: tuple[Fact, ...]

    @property
    def length(self) -> int:
        return self.fork_position + len(self.facts)


@dataclass(frozen=True)
class BundleReply:
    branch_key: str
    author: str
    text: str
    created_at: str
    updated_at: str | None


@dataclass(frozen=True)
class BundleThread:
    branch_key: str
    event_position: int
    author: str
    text: str
    created_at: str
    updated_at: str | None
    resolved: bool
    replies: tuple[BundleReply, ...]


@dataclass(frozen=True)
class RunBundle:
    name: str
    metadata: dict[str, Any]
    branches: tuple[BundleBranch, ...]
    threads: tuple[BundleThread, ...]
    by_key: dict[str, BundleBranch] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "by_key", {branch.key: branch for branch in self.branches})

    def owner(self, key: str, position: int) -> BundleBranch:
        """The branch whose own events hold `position` of `key`'s effective history (the root for position 0)."""
        branch = self.by_key[key]
        while branch.parent_key is not None and position <= branch.fork_position:
            branch = self.by_key[branch.parent_key]
        return branch

    def event_id(self, key: str, position: int) -> str:
        owner = self.owner(key, position)
        return owner.facts[position - owner.fork_position - 1].id


def export_bundle(contents: RunContents) -> dict[str, Any]:
    ordered = _parents_first(contents.branches)
    keys = {branch.id: f"branch-{index}" for index, branch in enumerate(ordered)}
    replies = defaultdict(list)
    for comment in contents.comments:
        if comment.parent_id:
            replies[comment.parent_id].append(comment)
    threads = sorted((comment for comment in contents.comments if comment.parent_id is None),
                     key=lambda comment: (comment.position, comment.created_at, comment.id))
    run = contents.run
    return {
        "format": FORMAT, "version": VERSION, "exported_at": utc_now(),
        "run": {"name": run.name, "metadata": deepcopy(run.metadata), "created_at": run.created_at},
        "branches": [{"key": keys[branch.id], "name": branch.name, "parent_key": keys.get(branch.parent_id),
                      "fork_position": branch.fork_position,
                      "events": [_export_event(event) for event in contents.own_events[branch.id]]}
                     for branch in ordered],
        "comments": [{**_export_note(thread, keys), "event_position": thread.position, "resolved": thread.resolved,
                      "replies": [_export_note(reply, keys)
                                  for reply in sorted(replies[thread.id], key=lambda reply: reply.created_at)]}
                     for thread in threads],
    }


def _parents_first(branches: list[Branch]) -> list[Branch]:
    children = defaultdict(list)
    for branch in branches:
        children[branch.parent_id].append(branch)
    ordered = list(children[None])
    for branch in ordered:
        ordered.extend(children[branch.id])
    return ordered


def _export_event(event: Event) -> dict[str, Any]:
    return {"kind": event.kind, "data": deepcopy(event.data), "occurred_at": event.occurred_at,
            "source": deepcopy(event.source)}


def _export_note(comment: Comment, keys: dict[str, str]) -> dict[str, Any]:
    return {"branch_key": keys[comment.branch_id], "author": comment.author, "text": comment.text,
            "created_at": comment.created_at, "updated_at": comment.updated_at}


def parse_bundle(raw: Any) -> RunBundle:
    """Check a bundle's structure, size, branch tree, and comment anchors; event semantics are left to the reducer."""
    _require(isinstance(raw, dict), "A run bundle must be a JSON object")
    if raw.get("format") != FORMAT or raw.get("version") != VERSION:
        raise DomainError(f"Unsupported run bundle {raw.get('format')!r} version {raw.get('version')!r}; "
                          f"expected {FORMAT!r} version {VERSION}")
    try:
        json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DomainError("Invalid run bundle: values must be JSON with finite numbers") from exc
    run = _field(raw, "run", dict)
    name = _field(run, "name", str).strip()
    _require(bool(name), "The bundled run needs a name")
    branches = _parse_branches(_field(raw, "branches", list))
    by_key = {branch.key: branch for branch in branches}
    threads = tuple(_parse_thread(entry, by_key) for entry in _field(raw, "comments", list))
    return RunBundle(name, deepcopy(_field(run, "metadata", dict)), branches, threads)


def _parse_branches(entries: list) -> tuple[BundleBranch, ...]:
    _require(bool(entries), "A run bundle needs its root branch")
    _require(len(entries) <= MAX_BRANCHES, f"at most {MAX_BRANCHES} branches can be imported")
    branches: dict[str, BundleBranch] = {}
    depths: dict[str, int] = {}
    total_events = 0
    for index, entry in enumerate(entries):
        _require(isinstance(entry, dict), "Each bundled branch must be an object")
        key, name = _field(entry, "key", str), _field(entry, "name", str)
        parent_key, fork_position = entry.get("parent_key"), _field(entry, "fork_position", int)
        _require(key not in branches, f"Duplicate branch key {key!r}")
        _require(bool(name.strip()), f"Branch {key!r} needs a name")
        if index == 0:
            _require(parent_key is None and fork_position == 0, "The first bundled branch must be the root")
        else:
            _require(parent_key in branches, f"Branch {key!r} must follow its parent")
            _require(0 <= fork_position <= branches[parent_key].length,
                     f"Branch {key!r} forks outside its parent's history")
        depths[key] = depths[parent_key] + 1 if index else 0
        _require(depths[key] <= MAX_FORK_DEPTH, f"forks can nest at most {MAX_FORK_DEPTH} deep")
        events = _field(entry, "events", list)
        total_events += len(events)
        _require(total_events <= MAX_EVENTS, f"at most {MAX_EVENTS} events can be imported")
        facts = tuple(_parse_fact(event) for event in events)
        branches[key] = BundleBranch(key, name.strip(), parent_key, fork_position, facts)
    return tuple(branches.values())


def _parse_fact(entry: Any) -> Fact:
    _require(isinstance(entry, dict), "Each bundled event must be an object")
    return Fact(_field(entry, "kind", str), deepcopy(_field(entry, "data", dict)),
                _field(entry, "occurred_at", str), deepcopy(_field(entry, "source", dict)))


def _parse_thread(entry: Any, branches: dict[str, BundleBranch]) -> BundleThread:
    _require(isinstance(entry, dict), "Each bundled comment must be an object")
    branch_key, position = _branch_key(entry, branches), _field(entry, "event_position", int)
    _require(1 <= position <= branches[branch_key].length, "A bundled comment anchors outside its branch's history")
    resolved = _field(entry, "resolved", bool)
    replies = tuple(_parse_reply(reply, branches, branch_key, position) for reply in _field(entry, "replies", list))
    return BundleThread(branch_key, position, comment_author(_field(entry, "author", str)),
                        comment_text(_field(entry, "text", str)), _timestamp(_field(entry, "created_at", str)),
                        _optional_timestamp(entry.get("updated_at")), resolved, replies)


def _parse_reply(entry: Any, branches: dict[str, BundleBranch], thread_key: str, position: int) -> BundleReply:
    _require(isinstance(entry, dict), "Each bundled reply must be an object")
    branch_key = _branch_key(entry, branches)
    _require(_sees(branches, branch_key, thread_key, position), "A bundled reply cannot see its thread")
    return BundleReply(branch_key, comment_author(_field(entry, "author", str)),
                       comment_text(_field(entry, "text", str)), _timestamp(_field(entry, "created_at", str)),
                       _optional_timestamp(entry.get("updated_at")))


def _sees(branches: dict[str, BundleBranch], viewer_key: str, owner_key: str, position: int) -> bool:
    """Mirror the store's comment visibility: an ancestor's comment is visible up to each fork point."""
    key, limit = viewer_key, branches[viewer_key].length
    while key is not None:
        if key == owner_key:
            return position <= limit
        limit = min(limit, branches[key].fork_position)
        key = branches[key].parent_key
    return False


def _branch_key(entry: dict, branches: dict[str, BundleBranch]) -> str:
    key = _field(entry, "branch_key", str)
    _require(key in branches, f"Unknown bundled branch {key!r}")
    return key


def _timestamp(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainError(f"Invalid bundled timestamp {value!r}") from exc
    _require(moment.tzinfo is not None, f"Bundled timestamp {value!r} needs a timezone")
    return moment.astimezone(timezone.utc).isoformat()


def _optional_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    _require(isinstance(value, str), "A bundled timestamp must be a string")
    return _timestamp(value)


def _field(entry: dict, name: str, kind: type) -> Any:
    value = entry.get(name)
    valid = isinstance(value, kind) and not (kind is int and isinstance(value, bool))
    _require(valid, f"Bundle field {name!r} must be a {kind.__name__}")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DomainError(f"Invalid run bundle: {message}")
