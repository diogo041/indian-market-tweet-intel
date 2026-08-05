"""Execute a query plan against X, streaming results to JSONL.

Design notes
------------
Checkpointing: completed query keys are appended to a sidecar file and
skipped on resume. Combined with append-only JSONL output this makes the
collector fully restartable -- a rate-limit stall, crash, or closed laptop
costs only the in-flight query.

Concurrency: bounded by the number of authenticated accounts. twscrape
rotates accounts per endpoint and blocks until the reset when all are
limited, so oversubscribing gains no throughput while raising ban risk.

Encoding: `ensure_ascii=False` preserves Devanagari and emoji as UTF-8
rather than escaping them, which keeps the raw files readable and halves
their size.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Iterable

from twscrape import API

from mkt_intel.config import Account
from mkt_intel.scraper.query_planner import Query

log = logging.getLogger(__name__)


async def build_api(accounts: Iterable[Account], db_path: str = "accounts.db") -> API:
    """Register all accounts into a twscrape pool."""
    api = API(db_path)
    for acc in accounts:
        await api.pool.add_account_cookies(acc.username, acc.cookies)
    return api


def _load_checkpoint(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}


async def collect(
    api: API,
    queries: list[Query],
    out_path: Path,
    checkpoint_path: Path,
    per_query_limit: int = 300,
    concurrency: int = 1,
    target: int | None = None,
) -> int:
    """Run `queries`, appending raw tweet JSON to `out_path`.

    Returns the number of distinct tweets written this run. Cross-run
    deduplication is handled downstream in the processing layer; the in-memory
    set here only avoids redundant writes within a single invocation.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    done = _load_checkpoint(checkpoint_path)
    pending = [q for q in queries if q.key not in done]
    log.info("plan: %d queries (%d already complete)", len(pending), len(done))

    sem = asyncio.Semaphore(concurrency)
    seen: set[int] = set()
    write_lock = asyncio.Lock()
    stop = asyncio.Event()

    out = out_path.open("a", encoding="utf-8")
    ckpt = checkpoint_path.open("a", encoding="utf-8")

    async def run_one(q: Query) -> None:
        if stop.is_set():
            return
        async with sem:
            written = 0
            try:
                async for tweet in api.search(q.text, limit=per_query_limit):
                    async with write_lock:
                        if tweet.id in seen:
                            continue
                        seen.add(tweet.id)
                        out.write(
                            json.dumps(tweet.dict(), default=str, ensure_ascii=False)
                            + "\n"
                        )
                        written += 1
                        if target and len(seen) >= target:
                            stop.set()
                out.flush()
                ckpt.write(q.key + "\n")
                ckpt.flush()
                log.info("+%-4d total=%-6d %s", written, len(seen), q.text[:64])
            except Exception:
                # A failed query is not fatal: it stays unchecked and will be
                # retried on the next run.
                log.exception("query failed: %s", q.text[:64])
            await asyncio.sleep(random.uniform(0.5, 1.5))

    try:
        await asyncio.gather(*(run_one(q) for q in pending))
    finally:
        out.close()
        ckpt.close()

    return len(seen)