"""Figures for the signal series and corpus, built for bounded memory.

Every plot here reads through pyarrow's batch iterator or an LTTB-reduced
series rather than materialising the full dataset. At current corpus size
that is unnecessary -- a few thousand tweets fit comfortably in memory --
but the same code runs unchanged at 10x, which is the point: the memory
discipline is structural rather than a later optimisation.

The Agg backend is selected explicitly so figures render without a display,
which matters when the pipeline runs headless or under CI.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pyarrow.dataset as ds

from mkt_intel.signals.aggregate import IST, BucketSignal
from mkt_intel.viz.lttb import lttb, streaming_histogram

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.25,
    "font.size": 9,
})

_BULL = "#1a7f37"
_BEAR = "#cf222e"
_NEUTRAL = "#57606a"


def plot_signal_series(signals: list[BucketSignal], out: Path) -> Path:
    """Signal with bootstrap confidence band and tweet volume.

    The confidence band is the point of this figure. A signal line alone
    invites reading every wiggle as meaningful; showing the interval makes
    visible which buckets are actually distinguishable from neutral.
    """
    live = [s for s in signals if not s.sparse]
    if not live:
        raise ValueError("no buckets above the reliability floor")

    times = [s.bucket_start.astimezone(IST) for s in live]
    sig = np.array([s.signal for s in live])
    lo = np.array([s.ci_low for s in live])
    hi = np.array([s.ci_high for s in live])
    vol = np.array([s.n_tweets for s in live])

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(10, 6), sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    ax.fill_between(times, lo, hi, alpha=0.18, color=_NEUTRAL,
                    label="95% bootstrap CI")
    ax.plot(times, sig, marker="o", ms=4, lw=1.6, color="#0969da",
            label="Composite sentiment")
    ax.axhline(0, color=_NEUTRAL, lw=0.8, ls="--")

    # Mark buckets whose interval excludes zero -- the only ones that are
    # statistically distinguishable from neutral.
    signif = [(t, s) for t, s, l, h in zip(times, sig, lo, hi)
              if l > 0 or h < 0]
    if signif:
        ax.scatter(*zip(*signif), s=70, facecolors="none",
                   edgecolors=_BULL, lw=1.6, zorder=5,
                   label="CI excludes zero")

    ax.set_ylabel("Signal  [-1, 1]")
    ax.set_title("Indian market sentiment signal, 15-minute buckets")
    ax.legend(loc="upper right", frameon=False, fontsize=8)

    ax2.bar(times, vol, width=0.008, color=_NEUTRAL, alpha=0.6)
    ax2.set_ylabel("Tweets")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=IST))
    ax2.set_xlabel("Time (IST)")

    fig.savefig(out)
    plt.close(fig)
    return out


def plot_bull_share(signals: list[BucketSignal], out: Path) -> Path:
    """Bullish share with Wilson intervals.

    Wilson rather than the normal approximation: at these bucket sizes the
    textbook interval can extend past 1.0, which would be visibly wrong on
    a proportion axis.
    """
    live = [s for s in signals if not s.sparse]
    if not live:
        raise ValueError("no buckets above the reliability floor")

    times = [s.bucket_start.astimezone(IST) for s in live]
    share = np.array([s.bull_share for s in live])
    lo = np.array([s.bull_ci_low for s in live])
    hi = np.array([s.bull_ci_high for s in live])

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.errorbar(times, share, yerr=[share - lo, hi - share],
                fmt="o", ms=5, capsize=3, lw=1.2, color=_BULL)
    ax.axhline(0.5, color=_NEUTRAL, lw=0.9, ls="--",
               label="No directional bias")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Bullish share of directional tweets")
    ax.set_xlabel("Time (IST)")
    ax.set_title("Directional balance with Wilson 95% intervals")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=IST))
    ax.legend(frameon=False, fontsize=8)
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_engagement_distribution(data_dir: Path, out: Path,
                                 batch_size: int = 5000) -> Path:
    """Engagement distribution, accumulated in a single streaming pass.

    Reads through `Dataset.to_batches` and sums histogram counts per batch,
    so peak memory is O(bins) rather than O(rows). Plotted on a log1p axis
    because engagement is power-law distributed and a linear axis would
    compress everything below the top percentile into one bar.
    """
    dataset = ds.dataset(data_dir, partitioning="hive")

    def chunks():
        for batch in dataset.to_batches(
            columns=["like_count", "retweet_count", "reply_count"],
            batch_size=batch_size,
        ):
            likes = np.asarray(batch.column("like_count"))
            rts = np.asarray(batch.column("retweet_count"))
            reps = np.asarray(batch.column("reply_count"))
            yield np.log1p(likes + 2.0 * rts + 3.0 * reps)

    counts, edges = streaming_histogram(chunks(), bins=40, value_range=(0, 10))
    centres = (edges[:-1] + edges[1:]) / 2

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(centres, counts, width=np.diff(edges), color="#0969da", alpha=0.75)
    ax.set_xlabel("log1p(likes + 2*retweets + 3*replies)")
    ax.set_ylabel("Tweets")
    ax.set_title(
        f"Engagement distribution  (n={counts.sum():,}, streamed in "
        f"{batch_size:,}-row batches)"
    )
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_lttb_demo(out: Path, n: int = 100_000, threshold: int = 500) -> Path:
    """Demonstrate that LTTB preserves extremes that naive sampling drops.

    Synthetic rather than real data, because the property being shown needs
    a known spike at a known index to be legible. The spike is placed off
    any sampling grid so the comparison is not accidentally favourable.
    """
    rng = np.random.default_rng(0)
    x = np.arange(n, dtype=float)
    y = np.sin(x / 500) + rng.normal(0, 0.05, n)
    y[50_137] = 8.0

    xs, ys = lttb(x, y, threshold)
    step = n // threshold
    xn, yn = x[::step], y[::step]

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(xs, ys, lw=0.8, color="#0969da")
    a1.set_title(f"LTTB: {n:,} to {len(xs):,} points (spike retained)")
    a2.plot(xn, yn, lw=0.8, color=_BEAR)
    a2.set_title(f"Every-{step}th: {len(xn):,} points (spike lost)")
    for a in (a1, a2):
        a.set_ylim(-1.5, 9)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


def generate_all(signals: list[BucketSignal], data_dir: Path,
                 out_dir: Path) -> list[Path]:
    """Write the full figure set, skipping any that lack sufficient data."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    jobs = [
        (plot_signal_series, (signals, out_dir / "signal_series.png")),
        (plot_bull_share, (signals, out_dir / "bull_share.png")),
        (plot_engagement_distribution,
         (data_dir, out_dir / "engagement_distribution.png")),
        (plot_lttb_demo, (out_dir / "lttb_demo.png",)),
    ]
    for fn, fnargs in jobs:
        try:
            written.append(fn(*fnargs))
        except Exception as exc:  # a missing figure should not fail the run
            print(f"skipped {fn.__name__}: {exc}")
    return written
