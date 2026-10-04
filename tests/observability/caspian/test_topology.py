import itertools

import numpy as np
import pytest

from swarm_lens.observability.caspian import CaspianConfig
from swarm_lens.observability.caspian.topology import (
    PathBudgetExceeded, Snapshot, attribute, degree_normalize, finite_diameter,
    make_snapshot, top_spines, topology, weak_link,
)


def chain():
    return topology(('a', 'b', 'c', 'd'), [('a', 'b'), ('b', 'c'), ('c', 'd')])


def test_degree_formula_mask_raw_and_channel_slices():
    agents, mask = chain()
    values = np.ones((4, 4, 4))
    result = make_snapshot(values, mask, CaspianConfig())
    np.testing.assert_equal(result.raw, mask * 4)
    np.testing.assert_equal(result.tensor[~mask], 0)
    np.testing.assert_allclose(result.normalized[mask], 4 / (4 + 1e-8))
    np.testing.assert_allclose(result.channels[:, :, 0][mask], 1 / (1 + 1e-8))
    matrix = np.array([[0, 2, 1], [4, 0, 0], [0, 3, 0.]])
    expected = np.zeros((3, 3))
    for i, j in itertools.product(range(3), repeat=2):
        expected[i, j] = matrix[i, j] / (np.sqrt(sum(matrix[i]) * sum(matrix[:, j])) + 1e-8)
    np.testing.assert_allclose(degree_normalize(matrix, 1e-8), expected)


def test_positive_zscore_policy_is_explicit_and_nonnegative():
    _, mask = chain()
    values = np.zeros((4, 4, 4))
    values[0, 1, 0], values[1, 2, 0], values[2, 3, 0] = 1, 2, 3
    result = make_snapshot(values, mask, CaspianConfig(normalization='positive_zscore'))
    assert result.normalized[0, 1] == result.normalized[1, 2] == 0
    assert result.normalized[2, 3] > 0
    assert result.raw[0, 1] == 1


def test_degree_normalization_dominant_singular_value_degeneracy():
    matrix = np.array([[0, 1, 2], [4, 0, 3], [2, 1, 0.]])
    for scale in (1, 20):
        normalized = degree_normalize(scale * matrix, 1e-14)
        assert np.linalg.svd(normalized, compute_uv=False)[0] == pytest.approx(1, abs=1e-13)
    np.testing.assert_allclose(degree_normalize(matrix, 1e-14), degree_normalize(20 * matrix, 1e-14))


def test_weak_link_literal_rule_equals_maximum_edge_and_is_vacuous():
    _, mask = chain()
    matrix = np.zeros((4, 4))
    matrix[0, 1], matrix[1, 2], matrix[2, 3] = 1, .01, .02
    bottleneck, scale, valid = weak_link(matrix, mask, 1e-8)
    assert bottleneck == 1 and valid and scale < 1
    assert weak_link(np.zeros((4, 4)), mask, 1e-8)[2]
    assert not weak_link(matrix, np.zeros_like(mask), 1e-8)[2]


def test_directed_finite_diameter_and_simple_paths_against_bruteforce():
    agents, mask = chain()
    assert finite_diameter(mask) == 3
    assert finite_diameter(np.zeros((1, 1), bool)) == 0
    matrix = mask.astype(float)
    channels = np.repeat(matrix[:, :, None], 4, axis=2)
    actual = top_spines(matrix, channels, mask, agents, 20, 100)
    reference = []
    for length in range(2, 5):
        for path in itertools.permutations(range(4), length):
            if all(mask[i, j] for i, j in zip(path, path[1:])):
                reference.append(path)
    assert [x['agents'] for x in actual] == [[agents[i] for i in p] for p in sorted(reference)]
    assert len(top_spines(matrix, channels, mask, agents, 3, 100)) == 3
    with pytest.raises(PathBudgetExceeded):
        top_spines(matrix, channels, mask, agents, 3, 1)


def test_roles_interval_maxima_and_dominant_channel_reference():
    agents, mask = chain()
    a = np.zeros((4, 4)); b = a.copy()
    a[0, 1], a[1, 2], a[2, 3] = .8, .3, .2
    b[0, 1], b[1, 2], b[2, 3] = .1, .9, .7
    channels_a, channels_b = np.zeros((4, 4, 4)), np.zeros((4, 4, 4))
    channels_a[:, :, 0] = a
    channels_b[:, :, 2] = b
    snapshots = [Snapshot(channels_a, 10*a, a, channels_a), Snapshot(channels_b, 20*b, b, channels_b)]
    config = CaspianConfig(top_k=6)
    result = attribute(snapshots, mask, agents, config)
    assert result['origin'] == result['amplifier'] == 'a'
    # Raw bridge scores: b=100*.8*.3+400*.1*.9=60; c=6+252=258.
    assert result['bridge'] == 'c'
    assert result['rankings']['bridge'][0]['score'] == pytest.approx(258)
    spine = next(s for s in result['spines'] if s['agents'] == ['a', 'b', 'c', 'd'])
    assert spine['bottleneck'] == .7
    assert spine['channel'] == 'tool'


@pytest.mark.parametrize('values', [np.zeros((2, 2, 3)), np.full((2, 2, 4), -1), np.full((2, 2, 4), np.inf)])
def test_bad_tensors(values):
    with pytest.raises(ValueError):
        make_snapshot(values, np.ones((2, 2), bool), CaspianConfig())
