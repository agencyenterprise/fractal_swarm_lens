from dataclasses import replace

import numpy as np
import pytest

from swarm_lens.observability.caspian import CaspianConfig
from swarm_lens.observability.caspian.detection import CascadeDetector, Signals, measure
from swarm_lens.observability.caspian.topology import Snapshot, make_snapshot, topology


def setup(**kwargs):
    agents, mask = topology(('a', 'b', 'c'), [('a', 'b'), ('b', 'c')])
    config = CaspianConfig(**kwargs)
    tensor = np.repeat(mask[:, :, None], 4, axis=2).astype(float)
    return CascadeDetector(agents, mask, config), make_snapshot(tensor, mask, config)


def signals(**kwargs):
    value = Signals(1, .5, 1.5, 1.2, .5, .5, .1, .2, 0,
                    False, False, True, .5, .3, False)
    return replace(value, **kwargs)


def test_signals_hand_computed_singular_values_entropy_and_watch():
    detector, snapshot = setup()
    # Directed nilpotent matrix: eigenvalues zero; singular values 3 and 2.
    matrix = np.array([[0, 3, 0], [0, 0, 2], [0, 0, 0.]])
    channels = np.repeat(matrix[:, :, None], 4, axis=2)
    snapshot = Snapshot(channels, matrix, matrix, channels)
    previous = signals(leading=2, secondary=.5, energy=2.5, ratio=.25, gap=.75)
    result = measure(snapshot, previous, detector.mask, detector.config)
    assert result.leading == 3 and result.secondary == 2
    assert result.amplification == pytest.approx(2)
    assert result.ratio == pytest.approx(2/3)
    assert result.gap_contraction == pytest.approx(5/12)
    assert result.phase_magnitude == pytest.approx(5/3)
    assert result.channel_entropy == pytest.approx(1)
    assert result.watch and result.cross_channel and result.phase_shift
    first = measure(snapshot, None, detector.mask, detector.config)
    assert not first.watch and first.amplification == 0


def test_zero_tensor_and_single_agent_no_nan_or_alert():
    agents, mask = topology(('alone',), [])
    config = CaspianConfig()
    detector = CascadeDetector(agents, mask, config)
    snapshot = make_snapshot(np.zeros((1, 1, 4)), mask, config)
    for turn in range(1, 5):
        result = detector.update(turn, snapshot)
        assert result['alert'] is None
        assert result['signals']['channel_entropy'] == 0
        assert result['signals']['gap'] == 1


def test_one_channel_entropy_is_not_renormalized_to_observed_channels():
    detector, snapshot = setup()
    snapshot.channels[:, :, 1:] = 0
    assert measure(snapshot, None, detector.mask, detector.config).channel_entropy < 1e-7


def test_instant_first_alert_and_stop():
    detector, snapshot = setup()
    result = detector.advance(1, snapshot, signals(phase_shift=True, weak_link=True))
    assert result['alert']['classification'] == 'single_turn'
    assert result['alert']['confirmation_turn'] == result['alert']['onset_turn'] == 1
    assert result['alert']['attribution_status'] == 'complete'
    with pytest.raises(ValueError):
        detector.update(2, snapshot)


def test_algorithm1_majority_inclusive_window_and_interval_attribution():
    detector, snapshot = setup()
    # gap=.3 -> ceil(1/.3)=4, inclusive turns [1,4].
    assert detector.advance(1, snapshot, signals(gap=.3))['candidate_deadline'] == 4
    detector.advance(2, snapshot, signals(watch=False, cross_channel=True))
    detector.advance(3, snapshot, signals(watch=False))
    result = detector.advance(4, snapshot, signals())
    assert result['alert']['classification'] == 'multi_turn'
    assert result['alert']['onset_turn'] == 1
    assert result['alert']['confirmation_turn'] == 4


def test_prose_reset_policy_discards_on_watch_drop():
    detector, snapshot = setup(persistence='reset_on_watch_drop')
    detector.advance(1, snapshot, signals(gap=.3))
    result = detector.advance(2, snapshot, signals(watch=False, cross_channel=True))
    assert result['candidate_onset'] is None and not detector.cache
    result = detector.advance(3, snapshot, signals(gap=.3))
    assert result['candidate_onset'] == 3 and result['candidate_deadline'] == 6


@pytest.mark.parametrize('transition,watch_count', [(False, 2), (True, 1)])
def test_unconfirmed_candidate_resets(transition, watch_count):
    detector, snapshot = setup()
    detector.advance(1, snapshot, signals(gap=.3, cross_channel=transition))
    for turn in range(2, 5):
        result = detector.advance(turn, snapshot, signals(watch=turn <= watch_count))
    assert result['alert'] is None and result['candidate_onset'] is None and not detector.cache


def test_phase_rule_is_almost_implied_by_positive_contraction():
    # For increasing R and Rprev+epsilon<1, Phi=deltaR/(Rprev+eps)>deltaR=delta gap.
    for before, after in ((.1, .2), (.5, .6), (.9, .95)):
        assert abs(after-before)/(before+1e-8) > after-before


def test_path_budget_preserves_alert_but_explicitly_marks_missing_attribution():
    detector, snapshot = setup(path_budget=1)
    result = detector.advance(1, snapshot, signals(phase_shift=True, weak_link=True))
    assert result['alert']['attribution_status'] == 'path_budget_exceeded'
    assert 'attribution' not in result['alert']


def test_huge_adaptive_window_is_not_silently_capped():
    detector, snapshot = setup()
    result = detector.advance(1, snapshot, signals(gap=0))
    assert result['candidate_deadline'] == 100_000_000
    assert len(detector.cache) == 1
