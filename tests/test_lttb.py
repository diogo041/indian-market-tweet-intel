"""Downsampling correctness.

The central claim -- that LTTB retains extremes naive sampling discards --
is asserted directly, since it is the entire reason for preferring it over
every-nth or mean-bucketing.
"""
import numpy as np

from mkt_intel.viz.lttb import lttb, reservoir_sample, streaming_histogram


def _series(n=10_000, spike_at=None, spike=8.0):
    """Noisy sine with one isolated spike, placed off any sampling grid."""
    rng = np.random.default_rng(0)
    x = np.arange(n, dtype=float)
    y = np.sin(x / 200) + rng.normal(0, 0.05, n)
    if spike_at is None:
        spike_at = min(5_137, n - 1)
    if spike_at < n:
        y[spike_at] = spike
    return x, y


def test_output_length_matches_threshold():
    x, y = _series()
    xs, _ = lttb(x, y, 500)
    assert len(xs) == 500


def test_endpoints_preserved_exactly():
    x, y = _series()
    xs, ys = lttb(x, y, 500)
    assert xs[0] == x[0] and xs[-1] == x[-1]
    assert ys[0] == y[0] and ys[-1] == y[-1]


def test_spike_retained_where_naive_sampling_fails():
    """The defining property: same point budget, different information kept."""
    x, y = _series()
    xs, ys = lttb(x, y, 500)
    step = len(x) // 500
    assert ys.max() > 7.5
    assert y[::step].max() < 7.5


def test_noop_when_threshold_exceeds_length():
    x, y = _series(n=100)
    xs, ys = lttb(x, y, 500)
    assert len(xs) == 100


def test_monotonic_x_preserved():
    x, y = _series()
    xs, _ = lttb(x, y, 500)
    assert np.all(np.diff(xs) > 0)


def test_streaming_histogram_counts_all_values():
    data = np.random.default_rng(0).normal(0, 1, 10_000)
    counts, _ = streaming_histogram(
        (data[i:i + 500] for i in range(0, len(data), 500)),
        bins=20, value_range=(-5, 5),
    )
    assert counts.sum() == len(data)


def test_streaming_histogram_handles_empty_input():
    counts, edges = streaming_histogram(iter([]), bins=20)
    assert counts.sum() == 0 and len(edges) == 21


def test_reservoir_sample_size_and_uniqueness():
    sample = reservoir_sample(range(100_000), 1000)
    assert len(sample) == 1000
    assert len(set(sample)) == 1000


def test_reservoir_sample_shorter_than_k():
    assert len(reservoir_sample(range(10), 1000)) == 10
