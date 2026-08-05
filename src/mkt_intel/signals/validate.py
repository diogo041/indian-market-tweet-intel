"""Test the sentiment signal against realised NIFTY 50 returns.

A trading signal that has not been checked against prices is an assertion,
not a result. This module makes the falsifiable claim explicit: if
aggregate tweet sentiment carries information about near-term index
direction, then bucket-level signal values should correlate with forward
returns over the following minutes.

Method notes:

  * Forward returns, not contemporaneous ones. Correlating sentiment at
    time t with the return over [t-15m, t] would mostly measure traders
    describing what just happened, which is real but useless -- the
    interesting question is whether sentiment precedes the move.

  * Spearman alongside Pearson. Both series have heavy tails and the
    relationship, if any, need not be linear; rank correlation is robust
    to both.

  * Sparse buckets excluded. Buckets that failed the reliability floor in
    aggregation carry no signal and would inject noise as zeros.

  * Market hours only. NSE trades 09:15-15:30 IST; tweets outside those
    hours have no contemporaneous price to compare against.

Price data comes from Yahoo Finance via yfinance (ticker ^NSEI), which is
free and requires no key -- consistent with the no-paid-API constraint.
Intraday history there is limited to roughly the last 60 days at 15-minute
resolution, which is sufficient for a 24-hour study.

A null or weak result is reported as such. With a single trading session
and a few dozen usable buckets, this analysis is underpowered by
construction: it can detect a strong relationship but cannot rule out a
modest one. That limitation is stated rather than hidden, because the
alternative -- searching over lags and horizons until something reaches
significance -- would produce a number with no predictive content.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats

from mkt_intel.signals.aggregate import IST, BucketSignal, wilson_interval

log = logging.getLogger(__name__)

NIFTY_TICKER = "^NSEI"
EXCHANGE_TZ = "Asia/Kolkata"
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)


@dataclass
class ValidationResult:
    horizon_minutes: int
    n_buckets: int
    pearson_r: float
    pearson_p: float
    spearman_r: float
    spearman_p: float
    hit_rate: float          # share of buckets where sign(signal)==sign(return)
    hit_rate_ci: tuple[float, float]

    def summary(self) -> str:
        sig = "significant" if self.pearson_p < 0.05 else "not significant"
        return (
            f"h={self.horizon_minutes:>3}m  n={self.n_buckets:>3}  "
            f"pearson={self.pearson_r:+.3f} (p={self.pearson_p:.3f}, {sig})  "
            f"spearman={self.spearman_r:+.3f} (p={self.spearman_p:.3f})  "
            f"hit={self.hit_rate:.1%} "
            f"[{self.hit_rate_ci[0]:.1%}, {self.hit_rate_ci[1]:.1%}]"
        )


def fetch_prices(start, end, interval: str = "15m") -> pd.DataFrame | None:
    """Fetch NIFTY 50 index prices, normalised to UTC. None if unavailable."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed; skipping price validation")
        return None

    try:
        df = yf.download(
            NIFTY_TICKER,
            start=(start - timedelta(days=1)).date(),
            end=(end + timedelta(days=1)).date(),
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        log.exception("price fetch failed")
        return None

    if df is None or df.empty:
        log.warning("no price data returned for %s", NIFTY_TICKER)
        return None

    # yfinance may return a MultiIndex when several tickers are requested;
    # flatten so column access is uniform for the single-ticker case.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Bars arrive in exchange-local time (Asia/Kolkata for ^NSEI). Everything
    # downstream works in UTC, so normalise here rather than carrying two
    # timezone conventions into the join.
    if df.index.tz is None:
        df.index = df.index.tz_localize(EXCHANGE_TZ)
    df.index = df.index.tz_convert("UTC").as_unit("ns")
    return df


def forward_returns(
    prices: pd.DataFrame, horizon_minutes: int, bar_minutes: int = 15
) -> pd.Series:
    """Log return from each bar's close to the close `horizon` ahead.

    Log returns are used so that returns over successive intervals are
    additive and the distribution is closer to symmetric -- both convenient
    when correlating against a bounded, roughly symmetric signal.
    """
    steps = max(1, horizon_minutes // bar_minutes)
    close = prices["Close"]
    return np.log(close.shift(-steps) / close)


def _in_market_hours(ts) -> bool:
    ist = ts.astimezone(IST)
    if ist.weekday() >= 5:
        return False
    minute = ist.hour * 60 + ist.minute
    return (MARKET_OPEN[0] * 60 + MARKET_OPEN[1]) <= minute <= (
        MARKET_CLOSE[0] * 60 + MARKET_CLOSE[1]
    )


def validate(
    signals: list[BucketSignal],
    horizons: tuple[int, ...] = (15, 30, 60),
) -> list[ValidationResult]:
    """Correlate bucket signals against forward NIFTY returns.

    Returns one result per horizon, or an empty list if prices could not be
    fetched or too few buckets overlap trading hours.
    """
    live = [
        s for s in signals
        if not s.sparse and _in_market_hours(s.bucket_start)
    ]
    if len(live) < 5:
        log.warning("only %d usable in-hours buckets; skipping", len(live))
        return []

    prices = fetch_prices(
        min(s.bucket_start for s in live), max(s.bucket_start for s in live)
    )
    if prices is None:
        return []

    # Match the price index exactly in both timezone and resolution;
    # merge_asof rejects mismatched datetime dtypes.
    sig = pd.Series(
        [s.signal for s in live],
        index=pd.DatetimeIndex(
            [s.bucket_start for s in live]
        ).tz_convert("UTC").as_unit("ns"),
    ).sort_index()

    results: list[ValidationResult] = []
    for h in horizons:
        fwd = forward_returns(prices, h)
        fwd.index = fwd.index.as_unit("ns")

        # Align each sentiment bucket to the price bar it falls within.
        joined = pd.merge_asof(
            sig.to_frame("signal"),
            fwd.to_frame("fwd_ret").dropna(),
            left_index=True,
            right_index=True,
            direction="backward",
            tolerance=pd.Timedelta("15min"),
        ).dropna()

        if len(joined) < 5:
            log.warning("horizon %dm: only %d aligned buckets", h, len(joined))
            continue

        x = joined["signal"].to_numpy()
        y = joined["fwd_ret"].to_numpy()
        pr, pp = stats.pearsonr(x, y)
        sr, sp = stats.spearmanr(x, y)

        hits = int(np.sum(np.sign(x) == np.sign(y)))
        n = len(joined)
        results.append(ValidationResult(
            horizon_minutes=h,
            n_buckets=n,
            pearson_r=float(pr),
            pearson_p=float(pp),
            spearman_r=float(sr),
            spearman_p=float(sp),
            hit_rate=hits / n,
            hit_rate_ci=wilson_interval(hits, n),
        ))

    return results
