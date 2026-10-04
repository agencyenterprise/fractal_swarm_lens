"""Directional predictive coupling between agents: conditional linear-Gaussian transfer entropy.

For a target agent Y, a source X and the other agents Z, over Y's messages t:

    TE(X -> Y | Z) = I(X_read(t) ; Y_t | Y_{t-1}, Z_read(t))

where X_read(t) is the latest X message Y had read before writing Y_t. For Gaussian variables this is
half the log ratio of the restricted and full regression residual covariances (Barnett, Barrett and
Seth 2009), and it is the mean of the per-message (local) values (Lizier et al. 2008).
It measures predictive coupling in one recorded trace. It is not causal influence and not a detector.
"""
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
import math

import numpy as np

RECORDED, ASSUMED = "recorded", "assumed"
RANK_TOLERANCE = 1e-8


@dataclass(frozen=True)
class Utterance:
    agent: str
    position: int
    event_id: str
    text: str
    read: dict[str, int]  # other agent -> index of that agent's utterance seen before this one
    exposure: str


@dataclass(frozen=True)
class Guard:
    """When a coupling is reported as insufficient data instead of a number."""
    min_samples: int = 12
    samples_per_column: float = 2.0
    max_explained: float = 0.98
    min_distinct: int = 6


@dataclass(frozen=True)
class Coupling:
    source: str
    target: str
    n: int
    te: float | None
    reason: str | None = None
    targets: tuple[Utterance, ...] = field(default=())  # the target messages scored, in order
    local: tuple[float, ...] = field(default=())  # per-message gain, aligned with targets


def utterances(events: Iterable) -> dict[str, list[Utterance]]:
    """Agent messages in order. Exposure comes from `source.delivered_sources` when the trace records it;
    otherwise each message is assumed to have seen every other agent's latest earlier message."""
    series: dict[str, list[Utterance]] = {}
    index_of: dict[str, tuple[str, int]] = {}
    for event in events:
        sender, text = event.data.get("sender_id"), event.data.get("content") or ""
        if event.kind != "message.created" or not sender or not text.strip():
            continue
        own = series.setdefault(sender, [])
        delivered = event.source.get("delivered_sources")
        if delivered is None:
            read = {agent: len(items) - 1 for agent, items in series.items() if agent != sender and items}
            exposure = ASSUMED
        else:
            read = {}
            for agent, index in (index_of[ref] for ref in delivered if ref in index_of):
                if agent != sender:
                    read[agent] = max(index, read.get(agent, -1))
            exposure = RECORDED
        if event.data.get("id"):
            index_of[event.data["id"]] = (sender, len(own))
        own.append(Utterance(sender, event.position, event.id, text, read, exposure))
    return series


def project(series: dict[str, list[Utterance]], vectors: Callable[[str], Sequence[float]],
            components: int) -> dict[str, np.ndarray]:
    """Per-trace PCA of all utterance embeddings, each component standardized. Only components above the
    numerical rank tolerance are kept, so the feature width can be smaller than `components` (at least 1)."""
    agents = sorted(series)
    stacked = np.array([vectors(u.text) for agent in agents for u in series[agent]], dtype=float)
    if not np.isfinite(stacked).all():
        raise ValueError("Embeddings must be finite")
    centered = stacked - stacked.mean(axis=0)
    _, singular, basis = np.linalg.svd(centered, full_matrices=False)
    kept = int(np.sum(singular[:components] > RANK_TOLERANCE * max(singular[0], np.finfo(float).tiny)))
    scores = np.zeros((len(stacked), max(kept, 1)))
    if kept:
        scores[:] = centered @ basis[:kept].T
        scores /= scores.std(axis=0)
    features, offset = {}, 0
    for agent in agents:
        features[agent] = scores[offset:offset + len(series[agent])]
        offset += len(series[agent])
    return features


def samples(series, features, source, target, others, first=1):
    """Rows (x, y_t, y_{t-1}, z) for each target message at position >= `first`, after the target's first
    message, that read the source and every other agent."""
    rows = [(t, u) for t, u in enumerate(series[target])
            if t and u.position >= first and source in u.read and all(o in u.read for o in others)]
    width = {agent: features[agent].shape[1] for agent in features}
    x = np.array([features[source][u.read[source]] for _, u in rows], dtype=float).reshape(len(rows), width[source])
    y = np.array([features[target][t] for t, _ in rows], dtype=float).reshape(len(rows), width[target])
    past = np.array([features[target][t - 1] for t, _ in rows], dtype=float).reshape(len(rows), width[target])
    z = np.array([np.concatenate([features[o][u.read[o]] for o in others]) if others else []
                  for _, u in rows], dtype=float).reshape(len(rows), sum(width[o] for o in others))
    return x, y, past, z, tuple(u for _, u in rows)


def _fit(design, y):
    design = np.hstack([np.ones((len(y), 1)), design])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ beta
    return residual, residual.T @ residual / len(y)


def _log_density(residual, covariance):
    inverse = np.linalg.inv(covariance)
    _, logdet = np.linalg.slogdet(covariance)
    quadratic = np.einsum("ij,jk,ik->i", residual, inverse, residual)
    return -0.5 * (quadratic + logdet + residual.shape[1] * math.log(2 * math.pi))


def insufficiency(x, y, past, z, guard: Guard) -> str | None:
    """Why the Gaussian estimate would be unstable on these rows, or None when it can be reported."""
    n, columns = len(y), 1 + past.shape[1] + z.shape[1] + x.shape[1]
    needed = max(guard.min_samples, math.ceil(guard.samples_per_column * columns))
    if n < needed:
        return f"{n} target messages, needs at least {needed}"
    distinct = len({tuple(np.round(row, 6)) for row in np.hstack([x, y, past, z])})
    if distinct < guard.min_distinct:
        return f"only {distinct} distinct message combinations"
    if np.linalg.eigvalsh(np.atleast_2d(np.cov(x, rowvar=False, bias=True))).min() <= 1e-9:
        return "the source's messages do not vary"
    spread = np.atleast_2d(np.cov(y, rowvar=False, bias=True))
    if np.linalg.eigvalsh(spread).min() <= 1e-9:
        return "the target's messages do not vary"
    _, residual = _fit(np.hstack([past, z, x]), y)
    whitening = np.linalg.inv(np.linalg.cholesky(spread))
    unexplained = np.linalg.eigvalsh(whitening @ residual @ whitening.T).min()
    if unexplained < 1 - guard.max_explained:
        return "the next message is almost exactly predictable (repeated messages)"
    return None


def coupling(series, features, source, target, guard: Guard, first=1) -> Coupling:
    others = [a for a in sorted(series) if a not in (source, target)]
    x, y, past, z, targets = samples(series, features, source, target, others, first)
    if not len(y):
        reason = "no target message read the source" + (" and every other agent" if others else "")
        return Coupling(source, target, 0, None, reason)
    reason = insufficiency(x, y, past, z, guard)
    if reason:
        return Coupling(source, target, len(y), None, reason, targets)
    restricted, full = np.hstack([past, z]), np.hstack([past, z, x])
    local = _log_density(*_fit(full, y)) - _log_density(*_fit(restricted, y))
    return Coupling(source, target, len(y), float(local.mean()), None, targets, tuple(local.tolist()))


def couplings(series, features, guard: Guard, first=1) -> list[Coupling]:
    agents = sorted(series)
    return [coupling(series, features, s, t, guard, first) for s in agents for t in agents if s != t]
