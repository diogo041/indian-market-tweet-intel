"""Aggregate per-tweet features into a time-bucketed trading signal.

The output is a series of composite scores on [-1, 1] with interval
estimates, one per time bucket. Two interval types are computed because
they answer different questions:

  * A bootstrap percentile interval on the weighted mean polarity, which
    captures uncertainty in the magnitude of sentiment. It is used in
    preference to a normal-theory interval because the weighted mean of a
    bounded, heavily zero-inflated variable is not remotely Gaussian at
    the bucket sizes involved here (often 20-80 tweets).

  * A Wilson score interval on the bullish share, which captures
    uncertainty in direction. Wilson is used rather than the textbook
    normal approximation because it stays inside [0, 1] and remains
    well-behaved when a bucket is small or nearly unanimous -- exactly the
    conditions where the normal approximation produces intervals extending
    past 1.0 and would silently mislead.

Bucket width defaults to 15 minutes: short enough to resolve intraday
moves, long enough that a typical bucket holds enough scoreable tweets for
the intervals to mean anything. Buckets below a minimum tweet count are
emitted with a null signal rather than a noisy one; overnight hours in this
corpus are genuinely sparse, and a confident-looking number derived from
three tweets would be worse than an honest gap.

Weighting combines confidence, engagement, and authority multiplicatively.
Confidence is the dominant term -- a tweet with no scoreable content
contributes nothing regardless of how many likes it received -- while
engagement and authority act as tilts on tweets that already carry signal.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

import numpy as np

from mkt_intel.signals.features import TweetFeatures

IST = timezone(timedelta(hours=5, minutes=30))

MIN_TWEETS_PER_BUCKET = 5
MIN_SCOREABLE_PER_BUCKET = 3


@dataclass
class BucketSignal:
    """Aggregate signal for one time bucket."""

    bucket_start: datetime
    n_tweets: int
    n_scoreable: int
    signal: float | None          # weighted composite, [-1, 1]
    ci_low: float | None          # bootstrap 2.5th percentile
    ci_high: float | None         # bootstrap 97.5th percentile
    bull_share: float | None      # proportion bullish among scoreable
    bull_ci_low: float | None     # Wilson lower bound
    bull_ci_high: float | None    # Wilson upper bound
    mean_engagement: float
    sparse: bool                  # True when below the reliability floor


def wilson_interval(
    successes: int, n: int, z: float = 1.96
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Unlike the normal approximation p +/- z*sqrt(p(1-p)/n), this cannot
    produce bounds outside [0, 1] and does not collapse to a zero-width
    interval when all observations fall on one side -- both of which occur
    routinely in small or unanimous buckets.
    """
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def bootstrap_ci(
    values: np.ndarray,
    weights: np.ndarray,
    n_resamples: int = 1000,
    level: float = 0.95,
    rng: np.random.Generator | None = None,
) -> tuple[float, float]:
    """Percentile bootstrap interval for a weighted mean.

    Resamples tweet indices with replacement, preserving the pairing of
    each polarity score with its weight, so the interval reflects
    uncertainty in both which tweets were observed and how much weight
    each carried.
    """
    if len(values) == 0 or weights.sum() == 0:
        return float("nan"), float("nan")
    rng = rng or np.random.default_rng(42)
    n = len(values)
    idx = rng.integers(0, n, size=(n_resamples, n))
    sampled_v = values[idx]
    sampled_w = weights[idx]
    denom = sampled_w.sum(axis=1)
    denom[denom == 0] = np.nan
    means = (sampled_v * sampled_w).sum(axis=1) / denom
    means = means[~np.isnan(means)]
    if means.size == 0:
        return float("nan"), float("nan")
    alpha = (1.0 - level) / 2.0
    return (float(np.percentile(means, 100 * alpha)),
            float(np.percentile(means, 100 * (1 - alpha))))


def composite_weights(features: TweetFeatures) -> np.ndarray:
    """Multiplicative weight per tweet: confidence x engagement x authority.

    Engagement and authority are normalised to a [1, 2] multiplier so they
    can at most double a tweet's influence. Confidence is left unscaled and
    can drive the weight to zero, which is the intended asymmetry: absence
    of scoreable content is disqualifying, whereas low engagement is only
    mildly informative.
    """
    def tilt(x: np.ndarray) -> np.ndarray:
        top = x.max()
        return 1.0 + (x / top if top > 0 else np.zeros_like(x))

    return (features.confidence.astype(np.float64)
            * tilt(features.engagement.astype(np.float64))
            * tilt(features.authority.astype(np.float64)))


def aggregate(
    timestamps: np.ndarray,
    features: TweetFeatures,
    bucket_minutes: int = 15,
    n_resamples: int = 1000,
) -> list[BucketSignal]:
    """Bucket tweets by time and compute a composite signal per bucket.

    Args:
        timestamps: datetime64 array aligned with `features`.
        features: per-tweet features from `features.extract`.
        bucket_minutes: bucket width.
        n_resamples: bootstrap resamples per bucket.

    Returns:
        Buckets in ascending time order. Sparse buckets carry None signals
        and `sparse=True` rather than being omitted, so that gaps in
        coverage remain visible downstream instead of being interpolated
        over silently.
    """
    # datetime64 has no timezone concept; the inputs are already UTC-aware,
    # so strip tzinfo explicitly rather than letting numpy warn and discard it.
    ts = np.array(
        [t.replace(tzinfo=None) for t in timestamps], dtype="datetime64[s]"
    ).astype("int64")
    if ts.size == 0:
        return []

    width = bucket_minutes * 60
    bucket_idx = ts // width
    weights = composite_weights(features)
    rng = np.random.default_rng(42)

    out: list[BucketSignal] = []
    for b in np.unique(bucket_idx):
        mask = bucket_idx == b
        start = datetime.fromtimestamp(int(b) * width, tz=timezone.utc)

        pol = features.polarity[mask].astype(np.float64)
        wts = weights[mask]
        evidence = features.evidence[mask]
        scoreable = evidence > 0

        n_tweets = int(mask.sum())
        n_scoreable = int(scoreable.sum())
        mean_eng = float(features.engagement[mask].mean())

        if (n_tweets < MIN_TWEETS_PER_BUCKET
                or n_scoreable < MIN_SCOREABLE_PER_BUCKET):
            out.append(BucketSignal(
                bucket_start=start, n_tweets=n_tweets,
                n_scoreable=n_scoreable, signal=None,
                ci_low=None, ci_high=None, bull_share=None,
                bull_ci_low=None, bull_ci_high=None,
                mean_engagement=mean_eng, sparse=True,
            ))
            continue

        sig_pol, sig_w = pol[scoreable], wts[scoreable]
        denom = sig_w.sum()
        signal = float((sig_pol * sig_w).sum() / denom) if denom > 0 else 0.0
        lo, hi = bootstrap_ci(sig_pol, sig_w, n_resamples=n_resamples, rng=rng)

        n_bull = int((sig_pol > 0).sum())
        n_dir = int((sig_pol != 0).sum())
        share = n_bull / n_dir if n_dir else None
        b_lo, b_hi = wilson_interval(n_bull, n_dir)

        out.append(BucketSignal(
            bucket_start=start, n_tweets=n_tweets, n_scoreable=n_scoreable,
            signal=signal, ci_low=lo, ci_high=hi,
            bull_share=share, bull_ci_low=b_lo, bull_ci_high=b_hi,
            mean_engagement=mean_eng, sparse=False,
        ))

    return sorted(out, key=lambda s: s.bucket_start)


def to_records(signals: list[BucketSignal]) -> list[dict]:
    """Flatten to plain dicts for serialisation or DataFrame construction."""
    return [asdict(s) for s in signals]