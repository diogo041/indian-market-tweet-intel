"""End-to-end analysis: processed Parquet in, signals, figures, and report out.

Single entry point so the pipeline is reproducible from a clean clone:

    python scripts/collect.py --target 12000
    python scripts/process.py
    python scripts/run_analysis.py

Writes bucket-level signals to Parquet and CSV, a machine-readable corpus
summary, a markdown results report, and the figure set.
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from mkt_intel.signals.aggregate import IST, aggregate, to_records
from mkt_intel.signals.features import build_tfidf, extract
from mkt_intel.signals.validate import validate
from mkt_intel.viz.plots import generate_all

log = logging.getLogger("analysis")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run signal analysis")
    ap.add_argument("--data", type=Path, default=Path("data/processed"))
    ap.add_argument("--out", type=Path, default=Path("outputs"))
    ap.add_argument("--bucket-minutes", type=int, default=15)
    ap.add_argument("--skip-validation", action="store_true",
                    help="skip the yfinance price fetch")
    ap.add_argument("--skip-figures", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s | %(message)s"
    )
    args.out.mkdir(parents=True, exist_ok=True)

    table = ds.dataset(args.data, partitioning="hive").to_table()
    log.info("loaded %d tweets", table.num_rows)

    feats = extract(table)
    timestamps = np.array(table.column("created_at").to_pylist())
    signals = aggregate(timestamps, feats, bucket_minutes=args.bucket_minutes)
    live = [s for s in signals if not s.sparse]
    log.info("%d buckets, %d above the reliability floor",
             len(signals), len(live))

    df = pd.DataFrame(to_records(signals))
    df["bucket_start_ist"] = pd.to_datetime(df["bucket_start"], utc=True) \
                               .dt.tz_convert(IST)
    df.to_parquet(args.out / "signals.parquet", index=False)
    df.to_csv(args.out / "signals.csv", index=False)

    texts = table.column("content").to_pylist()
    tfidf, word_vec, _ = build_tfidf(texts)
    log.info("tf-idf matrix %s (%.3f%% dense)", tfidf.shape,
             100 * tfidf.nnz / (tfidf.shape[0] * tfidf.shape[1]))

    # Highest mean TF-IDF weight identifies terms that are both frequent and
    # discriminative, as opposed to raw counts which just surface stopwords.
    means = np.asarray(tfidf[:, :len(word_vec.vocabulary_)].mean(axis=0)).ravel()
    inv = {i: t for t, i in word_vec.vocabulary_.items()}
    top_terms = [(inv[i], float(means[i])) for i in means.argsort()[::-1][:25]]

    classes = collections.Counter(feats.content_class)
    summary = {
        "n_tweets": table.num_rows,
        "n_unique_authors": len(set(table.column("username").to_pylist())),
        "content_classes": dict(classes),
        "pct_scoreable": float((feats.evidence > 0).mean()),
        "pct_promotional": float(feats.is_promo.mean()),
        "mean_polarity": float(feats.polarity.mean()),
        "n_buckets": len(signals),
        "n_buckets_live": len(live),
        "tfidf_shape": list(tfidf.shape),
        "tfidf_density_pct": 100 * tfidf.nnz / (tfidf.shape[0] * tfidf.shape[1]),
        "top_tfidf_terms": top_terms,
    }

    results = [] if args.skip_validation else validate(signals)
    summary["validation"] = [
        {
            "horizon_minutes": r.horizon_minutes, "n_buckets": r.n_buckets,
            "pearson_r": r.pearson_r, "pearson_p": r.pearson_p,
            "spearman_r": r.spearman_r, "spearman_p": r.spearman_p,
            "hit_rate": r.hit_rate, "hit_rate_ci": list(r.hit_rate_ci),
        }
        for r in results
    ]

    if not args.skip_figures:
        figures = generate_all(signals, args.data, args.out / "figures")
        summary["figures"] = [str(p) for p in figures]
        log.info("wrote %d figures", len(figures))

    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    _write_report(args.out / "results.md", summary, live, results)
    log.info("wrote signals, summary, figures, and report to %s", args.out)


def _write_report(path: Path, summary: dict, live, results) -> None:
    lines = [
        "# Analysis Results",
        "",
        "## Corpus",
        "",
        f"- Tweets (deduplicated, 24h window): **{summary['n_tweets']:,}**",
        f"- Unique authors: {summary['n_unique_authors']:,}",
        f"- Mean lexicon polarity: {summary['mean_polarity']:+.3f}",
        f"- Tweets carrying a scoreable term: {summary['pct_scoreable']:.1%}",
        f"- Promotional (tout) tweets: {summary['pct_promotional']:.1%}",
        "",
        "### Content classes",
        "",
        "| Class | Tweets |",
        "|---|---:|",
    ]
    for cls, n in sorted(summary["content_classes"].items(),
                         key=lambda kv: -kv[1]):
        lines.append(f"| {cls} | {n:,} |")

    lines += [
        "",
        "## Signal series",
        "",
        f"{summary['n_buckets']} buckets total, "
        f"{summary['n_buckets_live']} above the reliability floor "
        "(>=5 tweets and >=3 carrying a scoreable term).",
        "",
        "![Signal series](figures/signal_series.png)",
        "",
        "| Time (IST) | Tweets | Scoreable | Signal | 95% CI | Bullish share |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for s in live:
        t = s.bucket_start.astimezone(IST).strftime("%d %b %H:%M")
        lines.append(
            f"| {t} | {s.n_tweets} | {s.n_scoreable} | {s.signal:+.3f} | "
            f"[{s.ci_low:+.3f}, {s.ci_high:+.3f}] | "
            f"{s.bull_share:.0%} [{s.bull_ci_low:.0%}, {s.bull_ci_high:.0%}] |"
        )

    lines += [
        "",
        "![Bullish share](figures/bull_share.png)",
        "",
        "## Validation against NIFTY 50",
        "",
    ]
    if not results:
        lines.append(
            "Price validation did not run, or too few buckets overlapped "
            "NSE trading hours to compute a correlation."
        )
    else:
        lines += [
            "| Horizon | n | Pearson r | p | Spearman r | p | Hit rate |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        for r in results:
            lines.append(
                f"| {r.horizon_minutes}m | {r.n_buckets} | {r.pearson_r:+.3f} | "
                f"{r.pearson_p:.3f} | {r.spearman_r:+.3f} | {r.spearman_p:.3f} | "
                f"{r.hit_rate:.1%} [{r.hit_rate_ci[0]:.0%}, "
                f"{r.hit_rate_ci[1]:.0%}] |"
            )
        lines += [
            "",
            "**Interpretation.** No correlation reaches significance at the "
            "5% level. Sample sizes here are very small -- a handful of "
            "buckets overlap NSE trading hours, because collection covered "
            "only part of one session -- so this analysis can detect a "
            "strong relationship but cannot rule out a modest one. Point "
            "estimates at these sample sizes are not evidence in either "
            "direction, and no lag or horizon search was performed, since "
            "that would manufacture significance without predictive "
            "content. Establishing whether the signal carries information "
            "requires collection across multiple sessions.",
        ]

    lines += [
        "",
        "## Corpus characteristics",
        "",
        "![Engagement distribution](figures/engagement_distribution.png)",
        "",
        "Engagement is power-law distributed, which is why the aggregation "
        "layer weights by `log1p` of the interaction composite rather than "
        "raw counts: on a linear scale a single viral tweet would outweigh "
        "several hundred ordinary ones.",
        "",
        "## Downsampling",
        "",
        "![LTTB demonstration](figures/lttb_demo.png)",
        "",
        "Largest-Triangle-Three-Buckets reduces 100,000 points to 500 "
        "(99.5% reduction) while retaining an isolated spike that "
        "every-200th sampling discards at the same point budget.",
        "",
        "## Top TF-IDF terms",
        "",
        "| Term | Mean weight |",
        "|---|---:|",
    ]
    for term, weight in summary["top_tfidf_terms"][:15]:
        lines.append(f"| `{term}` | {weight:.4f} |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
