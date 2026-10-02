from dataclasses import asdict, dataclass
import math

import numpy as np

from .topology import PathBudgetExceeded, attribute, weak_link


@dataclass(frozen=True)
class Signals:
    leading: float
    secondary: float
    energy: float
    amplification: float
    ratio: float
    gap: float
    gap_contraction: float
    phase_magnitude: float
    channel_entropy: float
    phase_shift: bool
    cross_channel: bool
    watch: bool
    bottleneck: float
    influence_scale: float
    weak_link: bool


def measure(snapshot, previous, mask, config):
    singular = np.linalg.svd(snapshot.normalized, compute_uv=False)
    leading, secondary = float(singular[0]), float(singular[1]) if len(singular) > 1 else 0.0
    energy = leading + secondary
    ratio = secondary / (leading + config.epsilon)
    gap = 1 - ratio
    amplification = energy / (previous.energy + config.epsilon) if previous else 0.0
    contraction = previous.gap - gap if previous else 0.0
    phase = abs(ratio - previous.ratio) / (previous.ratio + config.epsilon) if previous else 0.0
    energies = np.array([np.linalg.svd(snapshot.channels[:, :, c], compute_uv=False)[0]
                         for c in range(snapshot.channels.shape[2])])
    shares = energies / (energies.sum() + config.epsilon)
    positive = shares[shares > 0]
    entropy = float(-np.dot(positive, np.log(positive)) / np.log(len(energies)))
    bottleneck, scale, feasible = weak_link(snapshot.normalized, mask, config.epsilon)
    watch = bool(previous and amplification > 1 and contraction > 0 and leading > previous.leading)
    return Signals(leading, secondary, energy, amplification, ratio, gap, contraction, phase,
                   entropy, phase > contraction, entropy >= config.entropy_threshold, watch,
                   bottleneck, scale, feasible)


class CascadeDetector:
    """First-alert monitor. Algorithm 1 and the conflicting prose policy are explicit."""

    def __init__(self, agents, mask, config):
        self.agents, self.mask, self.config = agents, mask.copy(), config
        self.previous = None
        self.last_turn = 0
        self.onset = self.deadline = None
        self.cache, self.signals = [], []
        self.alert = None

    def validate_turn(self, turn):
        if isinstance(turn, bool) or not isinstance(turn, int) or turn != self.last_turn + 1:
            raise ValueError("Turns must be contiguous positive integers beginning at 1; submit empty turns explicitly")
        if self.alert is not None:
            raise ValueError("CASPIAN stops at its first alert (Algorithm 1); create a new monitor to restart")

    def update(self, turn, snapshot):
        self.validate_turn(turn)
        signals = measure(snapshot, self.previous, self.mask, self.config)
        return self.advance(turn, snapshot, signals)

    def advance(self, turn, snapshot, signals):
        """State transition separated from numerical measurement for rule-level validation."""
        self.validate_turn(turn)
        self.last_turn, self.previous = turn, signals
        if (self.config.persistence == "reset_on_watch_drop" and self.onset is not None
                and not signals.watch):
            self._reset()
        if signals.watch and self.onset is None:
            self.onset = turn
            window = max(1, math.ceil(1 / (signals.gap + self.config.epsilon)))
            self.deadline = turn + window - 1
        classification = None
        if self.onset is not None:
            self.cache.append(snapshot)
            self.signals.append(signals)
            if (turn == self.onset and (signals.phase_shift or signals.cross_channel)
                    and signals.weak_link):
                classification = "single_turn"
            elif turn == self.deadline:
                majority = sum(s.watch for s in self.signals) >= len(self.signals) / 2
                transition = any(s.phase_shift or s.cross_channel for s in self.signals)
                if majority and transition:
                    classification = "multi_turn"
                else:
                    self._reset()
        if classification:
            self.alert = {"classification": classification, "onset_turn": self.onset,
                          "confirmation_turn": turn}
            try:
                self.alert["attribution"] = attribute(self.cache, self.mask, self.agents, self.config)
                self.alert["attribution_status"] = "complete"
            except PathBudgetExceeded as error:
                self.alert["attribution_status"] = "path_budget_exceeded"
                self.alert["attribution_error"] = str(error)
            self.cache.clear()
        return {"turn": turn, "signals": asdict(signals), "candidate_onset": self.onset,
                "candidate_deadline": self.deadline, "alert": self.alert}

    def _reset(self):
        self.onset = self.deadline = None
        self.cache.clear()
        self.signals.clear()
