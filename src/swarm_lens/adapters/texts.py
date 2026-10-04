"""Large text fields stored once per database, keyed by the SHA-256 of their UTF-8 bytes and compressed with zlib.

A packed record keeps its keys in order; each large text field holds the text's digest instead, and the caller
stores the names of those fields next to the record so a digest is never mistaken for literal text.
"""
from collections.abc import Iterable
import hashlib
import sqlite3
import zlib

from swarm_lens.core.models import DomainError

TEXT_FIELDS = ("content",)
INLINE_LIMIT = 256
_LOOKUP_CHUNK = 500


def pack(db: sqlite3.Connection, record: dict) -> tuple[dict, list[str]]:
    """Store each large text field of `record`; return a copy holding digests and the names of the replaced fields."""
    packed, refs = dict(record), []
    for name in TEXT_FIELDS:
        value = record.get(name)
        if isinstance(value, str) and len(value) > INLINE_LIMIT:
            packed[name] = put(db, value)
            refs.append(name)
    return packed, refs


def put(db: sqlite3.Connection, text: str) -> str:
    body = text.encode()
    digest = hashlib.sha256(body).hexdigest()
    if db.execute("SELECT 1 FROM texts WHERE digest=?", (digest,)).fetchone() is None:
        db.execute("INSERT INTO texts VALUES (?,?)", (digest, zlib.compress(body)))
    return digest


def load(db: sqlite3.Connection, digests: Iterable[str]) -> dict[str, str]:
    wanted = list(set(digests))
    found = {}
    for start in range(0, len(wanted), _LOOKUP_CHUNK):
        chunk = wanted[start:start + _LOOKUP_CHUNK]
        rows = db.execute(f"SELECT digest, body FROM texts WHERE digest IN ({','.join('?' * len(chunk))})", chunk)
        found.update((digest, zlib.decompress(body).decode()) for digest, body in rows)
    if len(found) != len(wanted):
        raise DomainError(f"Stored text is missing for {len(wanted) - len(found)} digest(s)")
    return found


def unpack(packed: dict, refs: list[str], texts: dict[str, str]) -> dict:
    return {**packed, **{name: texts[packed[name]] for name in refs}}
