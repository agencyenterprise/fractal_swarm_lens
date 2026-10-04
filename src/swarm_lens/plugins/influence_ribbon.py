"""Influence ribbon: whose messages predict whose next message, as exploratory predictive coupling.

Per-message gains become metric tracks, one annotation per agent pair carries the pair's coupling, and the
report holds the full matrix. The method is in `swarm_lens.observability.influence`.
"""
from collections.abc import Callable, Sequence
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

from pydantic import BaseModel, ConfigDict, Field

from swarm_lens.core.models import DomainError
from swarm_lens.plugins import Annotation, Metric, Report

LABEL = "Exploratory predictive coupling, not an attack detector"
LIMITATIONS = (
    "Predictive coupling inside one recorded trace, not causal influence.",
    "Not an attack detector. On 30 ACIArena medicine debates, the phase-1 prototype ranked the malicious agent "
    "first in 7 of 30 attack runs and the same seat first in 20 of 30 clean runs; this shipped estimator, checked "
    "after the fact, did so in 11 of 30 and 13 of 30 (docs/plugins/influence-ribbon.md).",
    "Agents with fewer than two messages up to the end of the range are left out, also as conditioning agents.",
    "To compare a fork with its parent, run the plugin on both branches; values come from separate fits.",
    "No significance test: a within-trace shuffle is not a valid null for this conditional, autocorrelated "
    "estimate, so only effect sizes are shown.",
    "Per-message gains are in-sample values of the same fitted model, with no per-message uncertainty.",
    "Embeddings are reduced by a per-trace PCA, so values are not comparable across traces in absolute terms.",
)


class CachedEmbedder:
    """Text embeddings cached in SQLite by text hash; the encoder is created only when a text is missing."""

    def __init__(self, path: Path, encoder: Callable[[], object], schema: str):
        self.path, self.encoder, self.schema = Path(path), encoder, schema
        self.lock = threading.Lock()

    def vectors(self, texts: Sequence[str]) -> dict[str, list[float]]:
        keys = {text: hashlib.sha256(f"{self.schema}\n{text}".encode()).hexdigest() for text in set(texts)}
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
                wanted, found = list(keys.values()), {}
                for offset in range(0, len(wanted), 500):
                    chunk = wanted[offset:offset + 500]
                    found.update((key, json.loads(vector)) for key, vector in db.execute(
                        f"SELECT key, vector FROM embeddings WHERE key IN ({','.join('?' * len(chunk))})", chunk))
                missing = sorted(text for text, key in keys.items() if key not in found)
                if missing:
                    encoded = self.encoder().encode(missing)
                    rows = [(keys[text], json.dumps(list(vector))) for text, vector in zip(missing, encoded, strict=True)]
                    db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?)", rows)
                    found.update((key, json.loads(vector)) for key, vector in rows)
        return {text: found[key] for text, key in keys.items()}


def openai_embedder(data: Path, dimensions: int = 256) -> CachedEmbedder:
    """text-embedding-3-small through the shared adapter, cached under the data directory."""
    from swarm_lens.adapters.openai_embeddings import OpenAITextEncoder

    def encoder():
        if not os.environ.get("OPENAI_API_KEY"):
            raise DomainError("Influence ribbon needs OPENAI_API_KEY on the server to embed new messages.")
        from openai import OpenAI
        return OpenAITextEncoder(OpenAI(timeout=30, max_retries=2), dimensions=dimensions)
    schema = f"openai/{OpenAITextEncoder.model}/dimensions={dimensions}"
    return CachedEmbedder(Path(data) / "influence-ribbon" / "embeddings.sqlite", encoder, schema)


class InfluenceRibbon:
    id, version = "influence-ribbon", "1.0.0"
    title = "Influence ribbon"
    description = ("Whose messages predict whose next message (conditional transfer entropy on message "
                   "embeddings). " + LABEL + ".")

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")
        components: int = Field(1, ge=1, le=3, title="Embedding components",
                                description="Principal components of the message embeddings per agent message")
        min_samples: int = Field(12, ge=4, le=10_000, title="Minimum messages",
                                 description="Report insufficient data below this many target messages per pair")

    def __init__(self, embedder: CachedEmbedder):
        self.embedder = embedder

    def analyze(self, view, start, end, params):
        try:
            from swarm_lens.observability.influence import Guard, couplings, project, utterances
        except ImportError as exc:
            raise DomainError("Influence ribbon needs numpy on the server (install the caspian extra).") from exc
        everyone = utterances(view.events(1, end, kind="message.created"))
        series = {agent: items for agent, items in everyone.items() if len(items) >= 2}
        if len(series) < 2:
            raise DomainError("Influence ribbon needs at least two agents with two or more messages up to the end.")
        vectors = self.embedder.vectors([u.text for items in series.values() for u in items])
        guard = Guard(min_samples=params.min_samples)
        features = project(series, vectors.__getitem__, params.components)
        results = couplings(series, features, guard, first=start)
        for item in results:
            for utterance, gain in zip(item.targets, item.local):
                yield Metric(utterance.position, f"gain from {item.source}", gain, item.target)
            if item.targets:
                yield pair_annotation(item)
        width = next(iter(features.values())).shape[1]
        yield Report(self.report(series, sorted(set(everyone) - set(series)), results, params, guard, width))

    def report(self, series, excluded, results, params, guard, width):
        exposures = [u.exposure for items in series.values() for u in items]
        return {
            "label": LABEL,
            "agents": sorted(series),
            "excluded_agents": excluded,
            "messages": {agent: len(items) for agent, items in sorted(series.items())},
            "exposure": {kind: exposures.count(kind) for kind in ("recorded", "assumed")},
            "couplings": [{"source": c.source, "target": c.target, "n": c.n, "te_nats": c.te,
                           "insufficient": c.reason} for c in results],
            "outgoing": {agent: outgoing(agent, results) for agent in sorted(series)},
            "method": {"estimator": "conditional linear-Gaussian transfer entropy, history 1, in nats",
                       "formula": "TE(X->Y|Z) = I(X_read(t); Y_t | Y_(t-1), Z_read(t))",
                       "features": f"{self.embedder.schema}, per-trace PCA to {params.components} component(s)",
                       "effective_components": width,
                       "guard": {"min_samples": guard.min_samples, "samples_per_column": guard.samples_per_column,
                                 "max_explained": guard.max_explained, "min_distinct": guard.min_distinct}},
            "limitations": list(LIMITATIONS),
        }


def outgoing(agent, results) -> dict:
    """Summed outgoing TE, or None when any outgoing pair is insufficient data (unknown is not zero)."""
    pairs = [c for c in results if c.source == agent]
    reported = [c.te for c in pairs if c.te is not None]
    return {"te_nats": sum(reported) if len(reported) == len(pairs) else None,
            "pairs_reported": len(reported), "pairs": len(pairs)}


def pair_annotation(item) -> Annotation:
    """One span over the target messages scored for this pair, in the target's lane."""
    value = (f"{item.te:.2f} nats over {item.n} messages" if item.te is not None
             else f"insufficient data ({item.reason})")
    return Annotation(item.targets[0].position, item.targets[-1].position, f"{item.source} → {item.target}: {value}",
                      item.target, score=item.te,
                      data={"source": item.source, "target": item.target, "n": item.n, "te_nats": item.te,
                            "insufficient": item.reason, "label": LABEL},
                      cited_event_ids=tuple(u.event_id for u in item.targets))


def create(services):
    return InfluenceRibbon(openai_embedder(services.data))
