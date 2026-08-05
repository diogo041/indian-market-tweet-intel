"""Stream raw JSONL through cleaning and deduplication into Parquet.

Concurrency model. Cleaning is CPU-bound -- Unicode normalisation, regex
entity extraction, and timestamp parsing per record -- and embarrassingly
parallel, since each tweet is independent. It runs across a process pool,
sidestepping the GIL, which a thread pool would not.

Deduplication is deliberately *not* parallelised. It is inherently
sequential: whether a tweet is a near-duplicate depends on every tweet
already admitted, so the MinHash-LSH index is shared mutable state. Running
it across processes would require either a distributed index or a merge
step that reintroduces the comparisons parallelism was meant to avoid, and
would make results depend on scheduling order. The pipeline therefore
parallelises the parallel part and keeps the sequential part sequential.

Memory is bounded by the submission window rather than the input size:
at most `2 * workers` chunks are in flight, so peak usage is flat
regardless of how large the raw file grows. Results are consumed in
submission order, keeping output deterministic across runs and worker
counts.

The 24-hour window is enforced here rather than at collection time. Raw
JSONL is kept unfiltered so the window can be widened later without
re-scraping -- a filter applied at the network boundary would be
irreversible. The filter is needed because X's search API returns nested
`retweetedTweet` and `quotedTweet` objects alongside timeline results: the
retweet occurred inside the requested window, but the tweet being retweeted
may be arbitrarily old.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from mkt_intel.processing.clean import clean
from mkt_intel.processing.dedup import Deduplicator
from mkt_intel.processing.storage import write


def _clean_chunk(lines: list[str]) -> tuple[list[dict], int, int]:
    """Parse and clean one chunk of raw JSONL lines.

    Module-level rather than a closure so it is picklable for the process
    pool. Returns (records, malformed, unusable) so the parent can
    accumulate statistics without shared state.
    """
    out: list[dict] = []
    malformed = 0
    unusable = 0
    for line in lines:
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
        out.append(rec)
    return out, malformed, unusable


def _chunks(path: Path, size: int) -> Iterator[list[str]]:
    """Yield fixed-size line groups without loading the file."""
    with path.open(encoding="utf-8") as fh:
        buf: list[str] = []
        for line in fh:
            buf.append(line)
            if len(buf) >= size:
                yield buf
                buf = []
        if buf:
            yield buf


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean and deduplicate raw tweets")
    ap.add_argument("--raw", type=Path, default=Path("data/raw/tweets.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("data/processed"))
    ap.add_argument("--batch-size", type=int, default=5000)
    ap.add_argument("--chunk-size", type=int, default=1000,
                    help="lines per parallel cleaning task")
    ap.add_argument("--workers", type=int, default=0,
                    help="cleaning processes; 0 = cpu_count, 1 = sequential")
    ap.add_argument("--near-dup-threshold", type=float, default=0.85)
    ap.add_argument("--window-hours", type=int, default=24,
                    help="drop tweets older than this many hours; 0 disables")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s | %(message)s"
    )
    log = logging.getLogger("process")

    workers = args.workers or (os.cpu_count() or 4)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=args.window_hours)
        if args.window_hours
        else None
    )
    if cutoff:
        log.info("window: keeping tweets at or after %s", cutoff.isoformat())
    log.info("cleaning with %d worker%s", workers, "" if workers == 1 else "s")

    dedup = Deduplicator(threshold=args.near_dup_threshold)
    batch: list[dict] = []
    batch_id = written = malformed = unusable = out_of_window = 0
    started = time.perf_counter()

    def consume(records: list[dict]) -> None:
        """Window-filter, deduplicate, and buffer for the Parquet writer."""
        nonlocal batch, batch_id, written, out_of_window
        for rec in records:
            if cutoff and rec["created_at"] < cutoff:
                out_of_window += 1
                continue
            if not dedup.is_new(rec["tweet_id"], rec["content"]):
                continue
            batch.append(rec)
            if len(batch) >= args.batch_size:
                written += write(batch, args.out, batch_id)
                log.info("flushed batch %d (%d rows total)", batch_id, written)
                batch = []
                batch_id += 1

    if workers == 1:
        # Sequential path: avoids pool overhead, and provides the baseline
        # for measuring whether parallelism actually pays at a given size.
        for chunk in _chunks(args.raw, args.chunk_size):
            recs, m, u = _clean_chunk(chunk)
            malformed += m
            unusable += u
            consume(recs)
    else:
        # Bounded submission window keeps memory flat: at most 2*workers
        # chunks are in flight, and futures are drained in submission order
        # so output is deterministic.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending: deque = deque()
            max_inflight = workers * 2
            for chunk in _chunks(args.raw, args.chunk_size):
                pending.append(pool.submit(_clean_chunk, chunk))
                if len(pending) >= max_inflight:
                    recs, m, u = pending.popleft().result()
                    malformed += m
                    unusable += u
                    consume(recs)
            while pending:
                recs, m, u = pending.popleft().result()
                malformed += m
                unusable += u
                consume(recs)

    if batch:
        written += write(batch, args.out, batch_id)

    elapsed = time.perf_counter() - started
    s = dedup.stats
    log.info(
        "read=%d  exact_dupes=%d  near_dupes=%d  kept=%d",
        s.total, s.exact_dupes, s.near_dupes, s.kept,
    )
    log.info(
        "malformed=%d  unusable=%d  out_of_window=%d  written=%d -> %s",
        malformed, unusable, out_of_window, written, args.out,
    )
    log.info(
        "completed in %.2fs with %d worker%s (%.0f records/s)",
        elapsed, workers, "" if workers == 1 else "s",
        (s.total + out_of_window) / elapsed if elapsed else 0,
    )


if __name__ == "__main__":
    # Required on macOS and Windows, which spawn rather than fork: without
    # the guard, each worker re-imports and re-executes this module.
    main()
