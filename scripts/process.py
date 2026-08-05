"""Stream raw JSONL through cleaning and deduplication into Parquet.

Memory is bounded by the batch size rather than the input size: records are
parsed, cleaned, deduplicated, and flushed in fixed-size chunks, so peak
usage is flat regardless of how large the raw file grows. The only
unbounded structures are the deduplicator's ID set and LSH index, both of
which have documented scaling paths.

The 24-hour window is enforced here rather than at collection time. Raw
JSONL is kept unfiltered so the window can be widened later without
re-scraping -- a filter applied at the network boundary would be
irreversible. The filter is needed because X's search API returns nested
`retweetedTweet` and `quotedTweet` objects alongside timeline results: the
retweet occurred inside the requested window, but the tweet being retweeted
may be arbitrarily old. Measured contamination on this corpus was ~3.7% of
cleaned records, of which roughly half were retweets or quotes.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mkt_intel.processing.clean import clean
from mkt_intel.processing.dedup import Deduplicator
from mkt_intel.processing.storage import write


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean and deduplicate raw tweets")
    ap.add_argument("--raw", type=Path, default=Path("data/raw/tweets.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("data/processed"))
    ap.add_argument("--batch-size", type=int, default=5000)
    ap.add_argument("--near-dup-threshold", type=float, default=0.85)
    ap.add_argument("--window-hours", type=int, default=24,
                    help="drop tweets older than this many hours; 0 disables")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s | %(message)s"
    )
    log = logging.getLogger("process")

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=args.window_hours)
        if args.window_hours
        else None
    )
    if cutoff:
        log.info("window: keeping tweets at or after %s", cutoff.isoformat())

    dedup = Deduplicator(threshold=args.near_dup_threshold)
    batch: list[dict] = []
    batch_id = 0
    written = 0
    malformed = 0
    unusable = 0
    out_of_window = 0

    with args.raw.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # Expected when the collector is mid-write on the final line.
                malformed += 1
                continue

            rec = clean(raw)
            if rec is None:
                unusable += 1
                continue
            if cutoff and rec["created_at"] < cutoff:
                out_of_window += 1
                continue
            if not dedup.is_new(rec["tweet_id"], rec["content"]):
                continue

            batch.append(rec)
            if len(batch) >= args.batch_size:
                written += write(batch, args.out, batch_id)
                log.info("flushed batch %d (%d rows total)", batch_id, written)
                batch.clear()
                batch_id += 1

    if batch:
        written += write(batch, args.out, batch_id)

    s = dedup.stats
    log.info(
        "read=%d  exact_dupes=%d  near_dupes=%d  kept=%d",
        s.total, s.exact_dupes, s.near_dupes, s.kept,
    )
    log.info(
        "malformed=%d  unusable=%d  out_of_window=%d  written=%d -> %s",
        malformed, unusable, out_of_window, written, args.out,
    )


if __name__ == "__main__":
    main()