"""Snapshot rows that store what changed since the nearest earlier snapshot of the same effective history.

A full row holds the whole state. A delta row names its base row, which may belong to an ancestor branch at or
before the fork, and holds the cursor, the environment, every agent and channel (removing an agent also edits
channel membership), and the messages, memories and tool calls named by the events after its base; the reducer
changes those only through events that name them. Deltas extend a chain while the entities they write stay within
the size of the chain's full row; beyond that a new full row starts. Reading a state therefore decodes at most
about twice its entities however long the run grows. Large texts are stored by digest (see `texts`).

Rows written before deltas existed (format 1) hold a whole state as JSON and remain readable; nothing builds on them.

Snapshots are a cache. A chain that cannot be read (a missing base row or a damaged payload) raises a RuntimeWarning
and counts as no snapshot, so the caller replays history. A new delta never builds on a chain with a missing row, and
saving a state at the cursor of a damaged row replaces it.
"""
from dataclasses import asdict
import json
import sqlite3
import warnings
import zlib

from swarm_lens.core.models import DomainError, State
from . import texts

LEGACY_FORMAT, FORMAT = 1, 2
DELTA_COLLECTIONS = {"message": "messages", "memory": "memories", "tool": "tools"}
WHOLE_COLLECTIONS = ("agents", "channels")
COLLECTIONS = (*WHOLE_COLLECTIONS, *DELTA_COLLECTIONS.values())
_CHAIN = """
WITH RECURSIVE chain(branch_id, cursor, depth) AS (
    VALUES (?, ?, 0)
    UNION ALL
    SELECT row.base_branch, row.base_cursor, chain.depth + 1 FROM snapshots AS row
    JOIN chain ON row.branch_id = chain.branch_id AND row.cursor = chain.cursor
    WHERE row.base_cursor IS NOT NULL
)
SELECT {columns} FROM chain
LEFT JOIN snapshots AS row ON row.branch_id = chain.branch_id AND row.cursor = chain.cursor
ORDER BY chain.depth DESC
"""


def save(db: sqlite3.Connection, state: State) -> None:
    """Store `state` as a delta on the nearest earlier snapshot when the chain allows it, otherwise in full."""
    base = _nearest(db, state.branch_id, state.cursor - 1)
    if base is not None and base["format"] == FORMAT and _intact(db, base):
        changed = _changed(db, state.branch_id, base["cursor"], state.cursor)
        chain = base["chain_entities"] + _size(state, changed)
        if chain <= base["full_entities"]:
            _insert(db, state, _payload(db, state, changed), (base["branch_id"], base["cursor"]),
                    chain, base["full_entities"])
            return
    _insert(db, state, _payload(db, state, {}), (None, None), 0, _size(state, {}))


def load(db: sqlite3.Connection, branch_id: str, cursor: int) -> State | None:
    """The state at the nearest snapshot at or before `cursor` in the branch's effective history."""
    base = _nearest(db, branch_id, cursor)
    if base is None:
        return None
    try:
        return _read(db, base)
    except (DomainError, ValueError, zlib.error) as exc:
        warnings.warn(f"Ignoring the unreadable snapshot of branch {base['branch_id']} at {base['cursor']} ({exc}); "
                      "replaying history instead", RuntimeWarning)
        return None


def _read(db, base) -> State:
    rows = db.execute(_CHAIN.format(columns="row.format, row.base_cursor, row.state"),
                      (base["branch_id"], base["cursor"])).fetchall()
    head = rows[0]
    if head["state"] is None or head["base_cursor"] is not None:
        raise DomainError("its chain is missing a base row")
    if head["format"] == LEGACY_FORMAT:
        if len(rows) > 1:
            raise DomainError("a delta snapshot cannot extend a version 1 snapshot")
        return State.from_dict(json.loads(head["state"]))
    return _state(db, base["branch_id"], _merge([json.loads(zlib.decompress(row["state"])) for row in rows]))


def _intact(db, base) -> bool:
    """Whether every row of the chain ending at `base` exists, down to a full row."""
    head = db.execute(_CHAIN.format(columns="row.cursor IS NULL AS missing, row.base_cursor"),
                      (base["branch_id"], base["cursor"])).fetchone()
    return not head["missing"] and head["base_cursor"] is None


def _ancestry(db, branch_id, cursor):
    """Yield (branch id, last visible position) for the branch and each ancestor whose prefix it shares."""
    while branch_id is not None:
        yield branch_id, cursor
        row = db.execute("SELECT parent_id, fork_position FROM branches WHERE id=?", (branch_id,)).fetchone()
        if row is None:
            raise DomainError("Unknown branch")
        branch_id, cursor = row["parent_id"], min(cursor, row["fork_position"])


def _nearest(db, branch_id, cursor):
    for owner, limit in _ancestry(db, branch_id, cursor):
        row = db.execute("SELECT branch_id, cursor, format, chain_entities, full_entities FROM snapshots "
                         "WHERE branch_id=? AND cursor<=? ORDER BY cursor DESC LIMIT 1", (owner, limit)).fetchone()
        if row is not None:
            return row
    return None


def _changed(db, branch_id, after, cursor) -> dict[str, list[str]]:
    """Ids of messages, memories and tool calls named by effective-history events in (after, cursor], first seen first."""
    segments = []
    for owner, limit in _ancestry(db, branch_id, cursor):
        if limit <= after:
            break
        segments.append((owner, limit))
    changed = {name: {} for name in DELTA_COLLECTIONS.values()}
    for owner, limit in reversed(segments):
        for kind, entity_id in db.execute(
                "SELECT kind, json_extract(data, '$.id') FROM events WHERE branch_id=? AND position>? AND position<=? "
                "ORDER BY position", (owner, after, limit)):
            collection = DELTA_COLLECTIONS.get(kind.split(".")[0])
            if collection is not None:
                changed[collection][entity_id] = None
    return {name: list(ids) for name, ids in changed.items()}


def _size(state: State, changed: dict[str, list[str]]) -> int:
    """Entities a row writes; `changed` is empty for a full row."""
    written = (len(changed[name]) if changed else len(getattr(state, name)) for name in DELTA_COLLECTIONS.values())
    return 1 + sum(len(getattr(state, name)) for name in WHOLE_COLLECTIONS) + sum(written)


def _payload(db, state: State, changed: dict[str, list[str]]) -> dict:
    def entities(name):
        values = getattr(state, name)
        return {key: _pack(db, values[key]) for key in changed.get(name, values)}
    return {"cursor": state.cursor, "occurred_at": state.occurred_at, "environment": _pack(db, state.environment),
            **{name: entities(name) for name in COLLECTIONS}}


def _pack(db, entity):
    record, refs = texts.pack(db, asdict(entity))
    return [record, refs] if refs else record


def _insert(db, state, payload, base, chain, full):
    db.execute("INSERT OR REPLACE INTO snapshots (branch_id, cursor, state, base_branch, base_cursor, chain_entities, "
               "full_entities, format) VALUES (?,?,?,?,?,?,?,?)",
               (state.branch_id, state.cursor, zlib.compress(json.dumps(payload, separators=(",", ":")).encode()),
                *base, chain, full, FORMAT))


def _merge(payloads: list[dict]) -> dict:
    merged = payloads[0]
    for delta in payloads[1:]:
        for name in DELTA_COLLECTIONS.values():
            merged[name].update(delta[name])
        merged.update({key: delta[key] for key in ("cursor", "occurred_at", "environment", *WHOLE_COLLECTIONS)})
    return merged


def _parts(entity) -> tuple[dict, list[str]]:
    return (entity[0], entity[1]) if isinstance(entity, list) else (entity, [])


def _state(db, branch_id: str, payload: dict) -> State:
    entities = [payload["environment"], *(entity for name in COLLECTIONS for entity in payload[name].values())]
    found = texts.load(db, (record[name] for record, refs in map(_parts, entities) for name in refs))

    def unpack(entity):
        return texts.unpack(*_parts(entity), found)
    return State.from_dict({"branch_id": branch_id, "cursor": payload["cursor"], "occurred_at": payload["occurred_at"],
                            "environment": unpack(payload["environment"]),
                            **{name: {key: unpack(value) for key, value in payload[name].items()}
                               for name in COLLECTIONS}})
