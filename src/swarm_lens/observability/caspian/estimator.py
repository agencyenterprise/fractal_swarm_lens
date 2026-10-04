"""Appendix C: online Gaussian-copula conditional dependence reconstruction."""
from bisect import bisect_left, bisect_right, insort
from collections import defaultdict
from statistics import NormalDist

import numpy as np

from .config import CHANNELS, CaspianConfig


class OnlineRanks:
    """Exact prefix midranks, including the current sample; never future samples."""

    def __init__(self, dimension):
        self.columns = [[] for _ in range(dimension)]

    def update(self, values):
        result = []
        for column, value in zip(self.columns, values, strict=True):
            lower, upper = bisect_left(column, value), bisect_right(column, value)
            probability = (lower + (upper - lower + 1) / 2) / (len(column) + 1)
            result.append(NormalDist().inv_cdf(probability))
            insort(column, float(value))
        return np.array(result)


class EWCovariance:
    """Population covariance with an exponentially weighted mean, initialized at x1."""

    def __init__(self, dimension, alpha):
        self.alpha, self.count = alpha, 0
        self.mean = np.zeros(dimension)
        self.covariance = np.zeros((dimension, dimension))

    def update(self, x):
        self.count += 1
        if self.count == 1:
            self.mean = x.copy()
        else:
            delta = x - self.mean
            self.mean += self.alpha * delta
            self.covariance = (1 - self.alpha) * (
                self.covariance + self.alpha * np.outer(delta, delta))


def gaussian_cmi(covariance, source_dim, target_dim, shrinkage=0.05, jitter=1e-8):
    """I(U;V|H) in nats via a Schur complement and log determinants.

    Both U and V are residualized on H; residualizing V alone generally is not CMI.
    """
    cov = np.asarray(covariance, dtype=float)
    if (cov.ndim != 2 or cov.shape[0] != cov.shape[1]
            or not np.isfinite(cov).all() or not np.allclose(cov, cov.T)):
        raise ValueError("Covariance must be finite, square and symmetric")
    d = source_dim + target_dim
    if source_dim < 1 or target_dim < 1 or d >= len(cov):
        raise ValueError("Source, target and history blocks must be nonempty")
    if not 0 <= shrinkage <= 1 or not np.isfinite(jitter) or jitter <= 0:
        raise ValueError("Invalid covariance regularization")
    cov = (1 - shrinkage) * cov + shrinkage * np.diag(np.diag(cov))
    cov = cov + jitter * np.eye(len(cov))
    np.linalg.cholesky(cov)
    conditional = cov[:d, :d] - cov[:d, d:] @ np.linalg.solve(cov[d:, d:], cov[d:, :d])
    blocks = (conditional[:source_dim, :source_dim],
              conditional[source_dim:, source_dim:], conditional)
    determinants = [np.linalg.slogdet(block) for block in blocks]
    if any(sign <= 0 for sign, _ in determinants):
        raise ValueError("Conditional covariance must be positive definite")
    return max(0.0, float((determinants[0][1] + determinants[1][1] - determinants[2][1]) / 2))


class EdgeEstimate:
    def __init__(self, source_dim, target_dim, config):
        self.source_dim, self.target_dim, self.config = source_dim, target_dim, config
        dimension = source_dim + 2 * target_dim
        self.ranks = OnlineRanks(dimension)
        self.moments = EWCovariance(dimension, config.covariance_alpha)

    def update(self, source, target, history):
        self.moments.update(self.ranks.update(np.concatenate((source, target, history))))
        if self.moments.count < self.config.min_samples:
            return 0.0
        return gaussian_cmi(self.moments.covariance, self.source_dim, self.target_dim,
                            self.config.shrinkage, self.config.jitter)


class InfluenceEstimator:
    def __init__(self, agents, mask, config: CaspianConfig):
        self.agents, self.mask, self.config = tuple(agents), mask.copy(), config
        self.indices = {agent: i for i, agent in enumerate(agents)}
        self.histories, self.estimates, self.dimensions = {}, {}, {}
        self.tensor = np.zeros((*mask.shape, len(CHANNELS)))

    def update(self, events):
        grouped, targets, dimensions = defaultdict(list), defaultdict(list), dict(self.dimensions)
        masked = 0
        # Validate the entire turn before advancing any estimator or history.
        for event in events:
            if event.source not in self.indices or event.target not in self.indices:
                raise ValueError("Unknown agent in channel event")
            if event.channel not in CHANNELS:
                raise ValueError("Unknown interaction channel")
            i, j, c = self.indices[event.source], self.indices[event.target], CHANNELS.index(event.channel)
            u, v = np.asarray(event.source_vector, dtype=float), np.asarray(event.target_vector, dtype=float)
            if any(x.ndim != 1 or x.size == 0 or not np.isfinite(x).all() for x in (u, v)):
                raise ValueError("Features must be nonempty finite vectors")
            if not self.mask[i, j]:
                masked += 1
                continue
            shape = (len(u), len(v))
            if c in dimensions and dimensions[c] != shape:
                raise ValueError("Feature dimensions must be stable within each channel")
            dimensions[c] = shape
            grouped[i, j, c].append((u, v))
            targets[j, c].append(v)
        self.dimensions = dimensions
        if self.config.missing_evidence == "zero":
            self.tensor.fill(0)
        for (i, j, c), pairs in sorted(grouped.items()):
            u, v = np.mean([p[0] for p in pairs], axis=0), np.mean([p[1] for p in pairs], axis=0)
            history = self.histories.get((j, c), np.zeros_like(v))
            key = (i, j, c)
            if key not in self.estimates:
                self.estimates[key] = EdgeEstimate(len(u), len(v), self.config)
            self.tensor[key] = self.estimates[key].update(u, v, history)
        for key, vectors in targets.items():
            current = np.mean(vectors, axis=0)
            old = self.histories.get(key, np.zeros_like(current))
            self.histories[key] = (1 - self.config.history_alpha) * old + self.config.history_alpha * current
        counts = np.zeros_like(self.tensor, dtype=int)
        for key, estimate in self.estimates.items():
            counts[key] = estimate.moments.count
        return self.tensor.copy(), {
            "observed_triplets": len(grouped), "masked_events": masked,
            "sample_counts": counts.tolist(),
            "ready_triplets": int(np.count_nonzero(counts >= self.config.min_samples)),
        }
