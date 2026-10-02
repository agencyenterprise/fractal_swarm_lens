from dataclasses import asdict

from .config import CHANNELS, CaspianConfig
from .detection import CascadeDetector
from .estimator import InfluenceEstimator
from .inputs import Turn
from .topology import make_snapshot, topology


class Caspian:
    """Paper-based reconstruction; not the unreleased author implementation."""

    id, version = "caspian", "0.1.0"

    def __init__(self, agents, edges, config=None, *, feature_schema, observed_channels=CHANNELS):
        if not isinstance(feature_schema, str) or not feature_schema.strip():
            raise ValueError("Provide an application feature schema/encoder version")
        self.agents, self.mask = topology(agents, edges)
        self.config = config or CaspianConfig()
        self.feature_schema = feature_schema
        self.observed_channels = tuple(observed_channels)
        if (not self.observed_channels or len(set(self.observed_channels)) != len(self.observed_channels)
                or any(c not in CHANNELS for c in self.observed_channels)):
            raise ValueError("Declare a nonempty subset of the four observed channels")
        self.estimator = InfluenceEstimator(self.agents, self.mask, self.config)
        self.detector = CascadeDetector(self.agents, self.mask, self.config)

    @property
    def finished(self):
        return self.detector.alert is not None

    def describe(self):
        return {"paper": "https://arxiv.org/abs/2605.19240v1", "implementation": "paper_reconstruction",
                "config": asdict(self.config), "agents": list(self.agents),
                "edges": [[a, b] for i, a in enumerate(self.agents) for j, b in enumerate(self.agents)
                          if self.mask[i, j]], "feature_schema": self.feature_schema,
                "observed_channels": list(self.observed_channels),
                "limitations": ["Trace-conditioned predictive dependence, not proof of attack or intervention causality",
                                "Unreleased estimator/normalization details are explicit reconstruction choices",
                                "Unobserved channels remain zero; entropy denominator remains log(4)"]}

    def update(self, observation: Turn):
        self.detector.validate_turn(observation.index)
        if any(e.channel not in self.observed_channels for e in observation.events):
            raise ValueError("Event uses a channel declared unobserved")
        tensor, evidence = self.estimator.update(observation.events)
        snapshot = make_snapshot(tensor, self.mask, self.config)
        result = self.detector.update(observation.index, snapshot)
        return {**result, "evidence": evidence, "influence_tensor": tensor.tolist(),
                "normalized_matrix": snapshot.normalized.tolist(), "raw_matrix": snapshot.raw.tolist()}
