"""CLI entry point for the collection run."""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from mkt_intel.config import load_accounts
from mkt_intel.scraper.collector import build_api, collect
from mkt_intel.scraper.query_planner import plan


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect Indian market tweets from X")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--limit", type=int, default=300, help="max tweets per query")
    ap.add_argument("--target", type=int, default=None, help="stop after N tweets")
    ap.add_argument("--out", type=Path, default=Path("data/raw/tweets.jsonl"))
    ap.add_argument("--checkpoint", type=Path, default=Path("data/raw/.checkpoint"))
    args = ap.parse_args()

    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        handlers=[  
            logging.StreamHandler(),
            logging.FileHandler("logs/collect.log", encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    accounts = load_accounts()
    queries = plan(hours_back=args.hours)
    logging.info("accounts=%d queries=%d", len(accounts), len(queries))

    async def run() -> None:
        api = await build_api(accounts)
        n = await collect(
            api,
            queries,
            out_path=args.out,
            checkpoint_path=args.checkpoint,
            per_query_limit=args.limit,
            concurrency=len(accounts),
            target=args.target,
        )
        logging.info("run complete: %d tweets written to %s", n, args.out)

    asyncio.run(run())


if __name__ == "__main__":
    main()