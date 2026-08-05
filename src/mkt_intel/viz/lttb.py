"""Largest-Triangle-Three-Buckets downsampling for time series.

Plotting a full-resolution series wastes memory and renders slower without
improving what the viewer sees: a 1920px-wide figure cannot resolve more
than about 2000 points. The question is which points to keep.

Naive strategies destroy the parts of a series that matter. Every-nth
sampling drops spikes entirely -- a one-bar volume surge sitting on an
unsampled index simply vanishes. Mean-bucketing averages spikes away, which
is worse: the extremes that matter most in market data get smoothed into
the baseline.

LTTB selects, for each output bucket, the point forming the largest
triangle with the previously-selected point and the mean of the next
bucket. Maximising triangle area preferentially retains points that deviate
from the local trend, so peaks and troughs survive while redundant points
along straight segments are discarded. First and last points are always
kept so the series endpoints are exact.

Complexity is O(n) time and O(threshold) space, and the implementation is
vectorised per bucket rather than looping over points.

Reference: Steinarsson, "Downsampling Time Series for Visual
Representation" (2013).
"""
from __future__ import annotations

import numpy as np


def lttb(x: np.ndarray, y: np.ndarray, threshold: int) -> tuple[np.ndarray, np.ndarray]:
    """Downsample (x, y) to approximately `threshold` points.

    Args:
        x: Monotonically increasing coordinates (e.g. epoch seconds).
        y: Values aligned with x.
        threshold: Target point count; must be at least 3.

    Returns:
        (x_sampled, y_sampled), preserving the first and last points.
    """
    n = len(x)
    if threshold >= n or threshold < 3:
        return x, y

    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)

    # Interior buckets; first and last points are reserved.
    bucket_size = (n - 2) / (threshold - 2)

    sampled_idx = np.empty(threshold, dtype=np.int64)
    sampled_idx[0] = 0
    sampled_idx[-1] = n - 1
    a = 0  # index of the previously selected point

    for i in range(threshold - 2):
        # Current bucket bounds.
        lo = int(np.floor(i * bucket_size)) + 1
        hi = min(int(np.floor((i + 1) * bucket_size)) + 1, n - 1)

        # Next bucket, used only for its centroid.
        nxt_lo = hi
        nxt_hi = min(int(np.floor((i + 2) * bucket_size)) + 1, n)
        if nxt_hi <= nxt_lo:
            nxt_lo, nxt_hi = n - 1, n
        avg_x = x[nxt_lo:nxt_hi].mean()
        avg_y = y[nxt_lo:nxt_hi].mean()

        if hi <= lo:
            sampled_idx[i + 1] = a
            continue

        # Twice the triangle area, sign discarded: the largest triangle
        # marks the point that deviates most from the line joining the
        # previous selection to the next bucket's centre of mass.
        areas = np.abs(
            (x[a] - avg_x) * (y[lo:hi] - y[a])
            - (x[a] - x[lo:hi]) * (avg_y - y[a])
        )
        a = lo + int(areas.argmax())
        sampled_idx[i + 1] = a

    return x[sampled_idx], y[sampled_idx]


def streaming_histogram(
    values_iter, bins: int = 50, value_range: tuple[float, float] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Accumulate a histogram over an iterable of chunks.

    Never materialises the full array: counts are summed per chunk against
    fixed bin edges. Memory is O(bins) regardless of input size, which is
    what makes distribution plots viable over a dataset larger than RAM.

    A fixed range must be supplied for a true single pass; when omitted,
    the first chunk determines the edges and later out-of-range values are
    clipped into the end bins rather than silently dropped.
    """
    counts = None
    edges = None
    for chunk in values_iter:
        chunk = np.asarray(chunk, dtype=np.float64)
        if chunk.size == 0:
            continue
        if edges is None:
            rng = value_range or (float(chunk.min()), float(chunk.max()))
            edges = np.linspace(rng[0], rng[1], bins + 1)
            counts = np.zeros(bins, dtype=np.int64)
        clipped = np.clip(chunk, edges[0], edges[-1])
        counts += np.histogram(clipped, bins=edges)[0]
    if counts is None:
        return np.zeros(bins, dtype=np.int64), np.linspace(0, 1, bins + 1)
    return counts, edges


def reservoir_sample(items_iter, k: int, seed: int = 42) -> list:
    """Algorithm R: uniform sample of size k from a stream of unknown length.

    Each element of the stream has equal probability k/n of appearing in
    the sample, without knowing n in advance and holding only k items in
    memory. Used for scatter plots, where a uniform subsample is visually
    indistinguishable from the full cloud at any realistic figure size.
    """
    rng = np.random.default_rng(seed)
    reservoir: list = []
    for i, item in enumerate(items_iter):
        if i < k:
            reservoir.append(item)
        else:
            j = int(rng.integers(0, i + 1))
            if j < k:
                reservoir[j] = item
    return reservoir