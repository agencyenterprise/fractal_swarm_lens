import math
from statistics import NormalDist

import numpy as np
import pytest

from swarm_lens.observability.caspian import CaspianConfig, ChannelEvent
from swarm_lens.observability.caspian.estimator import EWCovariance, InfluenceEstimator, OnlineRanks, gaussian_cmi


def event(source='a', target='c', u=(1.,), v=(2.,), channel='comm'):
    return ChannelEvent(source, target, channel, u, v)


def estimator(**config):
    mask = np.ones((3, 3), dtype=bool)
    np.fill_diagonal(mask, False)
    return InfluenceEstimator(('a', 'b', 'c'), mask, CaspianConfig(**config))


def test_gaussian_cmi_closed_form_and_history_confounder():
    rho = 0.6
    cov = np.array([[1, rho, 0], [rho, 1, 0], [0, 0, 1.]])
    jitter = 1e-12
    assert gaussian_cmi(cov, 1, 1, 0, jitter) == pytest.approx(-0.5 * math.log(1 - rho**2), abs=1e-11)
    # U=H+eu and V=2H+ev: marginal correlation, zero conditional information.
    cov = np.array([[2, 2, 1], [2, 5, 2], [1, 2, 1.]])
    assert gaussian_cmi(cov, 1, 1, 0, jitter) == pytest.approx(0, abs=1e-10)
    assert -0.5 * math.log(1 - 4 / 10) > 0.25


def test_multivariate_cmi_independent_reference_logdet_identity():
    rng = np.random.default_rng(18)
    basis = rng.normal(size=(7, 7))
    cov = basis @ basis.T + np.eye(7)
    regularized = cov + 1e-8 * np.eye(7)
    h, uh, vh = [4, 5, 6], [0, 1, 4, 5, 6], [2, 3, 4, 5, 6]
    logdet = lambda indices: np.linalg.slogdet(regularized[np.ix_(indices, indices)])[1]
    reference = (logdet(uh) + logdet(vh) - logdet(h) - np.linalg.slogdet(regularized)[1]) / 2
    assert gaussian_cmi(cov, 2, 2, 0) == pytest.approx(reference)


def test_singular_features_stay_finite_and_nonnegative():
    assert gaussian_cmi(np.zeros((3, 3)), 1, 1) == pytest.approx(0)
    assert math.isfinite(gaussian_cmi(np.ones((3, 3)), 1, 1))
    with pytest.raises((ValueError, np.linalg.LinAlgError)):
        gaussian_cmi(np.diag([-1, 1, 1]), 1, 1)


def test_online_ranks_midties_prefix_only_and_monotone_invariance():
    rank = OnlineRanks(1)
    assert rank.update([3])[0] == 0
    assert rank.update([3])[0] == 0
    assert rank.update([1])[0] == pytest.approx(NormalDist().inv_cdf(1 / 6))
    a, b = OnlineRanks(1), OnlineRanks(1)
    values = [3, 1, 4, 1, 5, 9, 2]
    for value in values:
        np.testing.assert_equal(a.update([value]), b.update([value**3 + 10]))


def test_streaming_covariance_equals_batch_exponential_weights():
    alpha = 0.2
    points = np.random.default_rng(5).normal(size=(30, 4))
    model = EWCovariance(4, alpha)
    for point in points:
        model.update(point)
    weights = np.array([(1-alpha)**29] + [alpha * (1-alpha)**i for i in range(28, -1, -1)])
    mean = np.average(points, axis=0, weights=weights)
    reference = (points - mean).T @ (weights[:, None] * (points - mean))
    np.testing.assert_allclose(model.mean, mean, atol=1e-14)
    np.testing.assert_allclose(model.covariance, reference, atol=1e-14)


def test_triplet_averaging_target_history_event_weighting_and_no_leakage():
    model = estimator(history_alpha=0.5)
    model.update([event(u=(2,), v=(4,)), event(u=(4,), v=(8,)), event(source='b', u=(7,), v=(12,))])
    # One sample per edge; h_c = .5 * mean(4,8,12) = 4, not mean(mean(4,8),12).
    assert model.histories[2, 0][0] == 4
    assert model.estimates[0, 2, 0].ranks.columns == [[3.0], [6.0], [0.0]]
    assert model.estimates[1, 2, 0].ranks.columns == [[7.0], [12.0], [0.0]]
    model.update([event(v=(100,)), event(source='b', v=(200,))])
    assert model.estimates[0, 2, 0].ranks.columns[2] == [0, 4]
    assert model.estimates[1, 2, 0].ranks.columns[2] == [0, 4]


def test_event_order_independence_masking_and_return_isolation():
    a, b = estimator(), estimator()
    events = [event(), event(source='b'), event(source='c', target='c')]
    x, evidence = a.update(events)
    y, _ = b.update(reversed(events))
    np.testing.assert_equal(x, y)
    assert evidence['masked_events'] == 1
    x[:] = 99
    assert not a.tensor.any()


@pytest.mark.parametrize('policy', ['hold', 'zero'])
def test_missing_events_do_not_add_samples_or_update_history(policy):
    model = estimator(missing_evidence=policy, min_samples=2)
    for t in range(10):
        model.update([event(u=(t % 3,), v=(t % 3,))])
    before = model.tensor.copy()
    history = model.histories[2, 0].copy()
    tensor, evidence = model.update([])
    np.testing.assert_equal(model.histories[2, 0], history)
    assert evidence['sample_counts'][0][2][0] == 10
    np.testing.assert_equal(tensor, before if policy == 'hold' else np.zeros_like(before))


def test_invalid_turn_is_rejected_before_partial_estimator_mutation():
    model = estimator()
    with pytest.raises(ValueError):
        model.update([event(), event(u=(math.nan,))])
    assert not model.estimates and not model.histories and not model.dimensions
    model.update([event()])
    with pytest.raises(ValueError):
        model.update([event(u=(1, 2))])
    assert model.estimates[0, 2, 0].moments.count == 1


def test_streaming_dependence_exceeds_independent_control():
    coupled, independent = estimator(), estimator()
    rng = np.random.default_rng(123)
    for _ in range(1500):
        u, noise, v = rng.normal(size=3)
        coupled.update([event(u=(u,), v=(u + 0.15 * noise,))])
        independent.update([event(u=(u,), v=(v,))])
    assert coupled.tensor[0, 2, 0] > 0.7
    assert independent.tensor[0, 2, 0] < 0.15
