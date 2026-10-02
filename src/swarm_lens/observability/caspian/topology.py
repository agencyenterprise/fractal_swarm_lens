from collections import deque
from dataclasses import dataclass
from bisect import insort

import numpy as np

from .config import CHANNELS


class PathBudgetExceeded(RuntimeError):
    """Exact spine enumeration exceeded the declared resource budget; no approximation returned."""


def topology(agents, edges):
    agents = tuple(agents)
    if not agents or any(not isinstance(a, str) or not a for a in agents) or len(set(agents)) != len(agents):
        raise ValueError("Provide nonempty, unique agent IDs")
    lookup = {agent: i for i, agent in enumerate(agents)}
    mask = np.zeros((len(agents), len(agents)), dtype=bool)
    for source, target in edges:
        if source not in lookup or target not in lookup or source == target:
            raise ValueError("Topology edges must join distinct known agents")
        mask[lookup[source], lookup[target]] = True
    return agents, mask


def finite_diameter(mask):
    """Maximum finite directed shortest-path distance; convention for disconnected graphs."""
    diameter = 0
    for start in range(len(mask)):
        distance, queue = {start: 0}, deque([start])
        while queue:
            node = queue.popleft()
            for target in np.flatnonzero(mask[node]):
                if target not in distance:
                    distance[target] = distance[node] + 1
                    diameter = max(diameter, distance[target])
                    queue.append(target)
    return diameter


def degree_normalize(matrix, epsilon):
    outgoing, incoming = matrix.sum(axis=1), matrix.sum(axis=0)
    return matrix / (np.sqrt(outgoing[:, None] * incoming[None, :]) + epsilon)


@dataclass
class Snapshot:
    tensor: np.ndarray
    raw: np.ndarray
    normalized: np.ndarray
    channels: np.ndarray


def make_snapshot(tensor, mask, config):
    values = np.array(tensor, dtype=float, copy=True)
    if values.shape != (*mask.shape, len(CHANNELS)) or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Influence tensor must be finite, nonnegative, and N x N x 4")
    values *= mask[:, :, None]
    raw = values.sum(axis=2)
    scaled = values.copy()
    if config.normalization == "positive_zscore" and mask.any():
        for c in range(len(CHANNELS)):
            feasible = values[:, :, c][mask]
            scaled[:, :, c][mask] = np.maximum(0, (feasible - feasible.mean()) / (feasible.std() + config.epsilon))
    normalized = degree_normalize(scaled.sum(axis=2), config.epsilon)
    channels = np.stack([degree_normalize(scaled[:, :, c], config.epsilon)
                         for c in range(len(CHANNELS))], axis=2)
    return Snapshot(values, raw, normalized, channels)


def weak_link(matrix, mask, epsilon):
    # Eq. (8) permits one-edge simple paths: the global maximum bottleneck is max edge.
    weights = matrix[mask]
    bottleneck = float(weights.max()) if weights.size else 0.0
    scale = float(np.dot(weights, weights) / (weights.sum() + epsilon))
    return bottleneck, scale, bool(weights.size and bottleneck >= scale)


def top_spines(matrix, channels, mask, agents, top_k, budget):
    """Exact Eq. (14), simple paths of 1..diameter edges, deterministic ties."""
    limit, count, best = finite_diameter(mask), 0, []
    adjacency = [np.flatnonzero(row).tolist() for row in mask]
    for start in range(len(mask)):
        stack = [((start,), float("inf"))]
        while stack:
            path, strength = stack.pop()
            if len(path) - 1 >= limit:
                continue
            for target in adjacency[path[-1]]:
                if target in path:
                    continue
                candidate = (*path, target)
                score = min(strength, float(matrix[path[-1], target]))
                count += 1
                if count > budget:
                    raise PathBudgetExceeded(f"More than {budget} simple paths; increase path_budget explicitly")
                insort(best, (-score, candidate))
                if len(best) > top_k:
                    best.pop()
                stack.append((candidate, score))
    result = []
    for negative_score, path in best:
        score = -negative_score
        edges = list(zip(path[:-1], path[1:]))
        energy = np.sum([channels[i, j, :] for i, j in edges], axis=0)
        result.append({"agents": [agents[i] for i in path], "bottleneck": score,
                       "channel": CHANNELS[int(np.argmax(energy))],
                       "channel_scores": dict(zip(CHANNELS, energy.tolist()))})
    return result


def attribute(snapshots, mask, agents, config):
    origin = snapshots[0].normalized.sum(axis=1)
    amplifier = sum(s.normalized.sum(axis=1) / (s.normalized.sum(axis=0) + config.epsilon)
                    for s in snapshots)
    bridge = sum(s.raw.sum(axis=1) * s.raw.sum(axis=0) for s in snapshots)
    ranks = {}
    for name, scores in (("origin", origin), ("amplifier", amplifier), ("bridge", bridge)):
        ranks[name] = [{"agent": agents[i], "score": float(scores[i])}
                       for i in sorted(range(len(agents)), key=lambda i: (-scores[i], i))]
    maximum = np.maximum.reduce([s.normalized for s in snapshots])
    channel_maximum = np.maximum.reduce([s.channels for s in snapshots])
    return {**{name: ranking[0]["agent"] for name, ranking in ranks.items()}, "rankings": ranks,
            "spines": top_spines(maximum, channel_maximum, mask, agents, config.top_k, config.path_budget)}
