from dataclasses import dataclass
import math

CHANNELS = ("comm", "mem", "tool", "exec")


@dataclass(frozen=True)
class CaspianConfig:
    """Reconstruction choices, NOT recovered author hyperparameters; see method README."""

    history_alpha: float = 0.1
    covariance_alpha: float = 0.05
    shrinkage: float = 0.05
    jitter: float = 1e-8
    epsilon: float = 1e-8
    min_samples: int = 8
    entropy_threshold: float = 0.5
    top_k: int = 3
    normalization: str = "raw_sum"
    persistence: str = "algorithm1"
    missing_evidence: str = "hold"
    path_budget: int = 100_000

    def __post_init__(self):
        for name in ("history_alpha", "covariance_alpha"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        for name in ("shrinkage", "entropy_threshold"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        for name in ("jitter", "epsilon"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name, minimum in (("min_samples", 2), ("top_k", 1), ("path_budget", 1)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.normalization not in {"raw_sum", "positive_zscore"}:
            raise ValueError("Unknown normalization policy")
        if self.persistence not in {"algorithm1", "reset_on_watch_drop"}:
            raise ValueError("Unknown persistence policy")
        if self.missing_evidence not in {"hold", "zero"}:
            raise ValueError("Unknown missing evidence policy")
