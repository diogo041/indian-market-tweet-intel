"""Interval estimation and bucketing.

The Wilson interval is tested against its defining properties rather than
hard-coded values: it must stay inside [0, 1] and must not collapse to zero
width under unanimity -- the two failure modes of the normal approximation
that motivated choosing it.
"""
import numpy as np

from mkt_intel.signals.aggregate import bootstrap_ci, wilson_interval


def test_wilson_bounds_within_unit_interval():
    for successes, n in [(0, 10), (10, 10), (1, 3), (0, 1)]:
        lo, hi = wilson_interval(successes, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_handles_unanimity():
    """Normal approximation gives zero width here, which is wrong."""
    lo, hi = wilson_interval(10, 10)
    assert hi - lo > 0.05


def test_wilson_narrows_with_sample_size():
    small = wilson_interval(6, 10)
    large = wilson_interval(600, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_empty_sample_is_uninformative():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_bootstrap_ci_brackets_weighted_mean():
    rng = np.random.default_rng(0)
    values = rng.normal(0.3, 0.1, 200)
    weights = np.ones(200)
    lo, hi = bootstrap_ci(values, weights, n_resamples=500)
    assert lo < values.mean() < hi


def test_bootstrap_ci_narrows_with_sample_size():
    rng = np.random.default_rng(0)
    small = bootstrap_ci(rng.normal(0, 1, 20), np.ones(20), n_resamples=500)
    large = bootstrap_ci(rng.normal(0, 1, 2000), np.ones(2000), n_resamples=500)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_bootstrap_ci_empty_input():
    lo, hi = bootstrap_ci(np.array([]), np.array([]))
    assert np.isnan(lo) and np.isnan(hi)
