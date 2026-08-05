"""Partition the search space into disjoint, high-yield queries.

X's Latest-tab search returns a bounded result set per query before the
pagination cursor stalls -- empirically ~300 tweets. A single hashtag query
therefore cannot reach the 2000-tweet target no matter how it is paged. We
partition along two axes:

  1. Time  -- `since_time` / `until_time` unix-second bounds
  2. Terms -- topical groups, deliberately overlapping (dedup is cheap;
              missed coverage is not)

Slice width is inversely proportional to expected tweet density. Indian
market chatter concentrates sharply in NSE/BSE cash-session hours
(09:15-15:30 IST), so those windows get 30-minute slices while overnight
windows get two hours. Weekends are uniformly sparse and get the widest
slices.

Two filters were deliberately omitted after calibration:

  * `lang:en`         -- most Indian fintwit is Hinglish and is tagged
                         inconsistently as en/hi/und
  * `-filter:retweets` -- retweets carry their own engagement metrics and
                         are required by the spec; they are flagged during
                         processing instead

Applying both cost ~92% of retrievable volume in measurement (25 vs 335
tweets for an identical term group and time window).

The term list was extended after the initial four groups began returning
over 50% duplicates on later runs. That ratio is the signal that the
*queries* rather than the *time window* have become the binding
constraint: once a group's slices are exhausted, adding more slices returns
tweets already collected, and only new vocabulary opens new coverage.

Queries are emitted in descending order of expected yield, so a run
truncated by rate limits still captures the densest windows first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

TERM_GROUPS: tuple[str, ...] = (
    # Core indices: highest yield, saturate their slices quickly.
    "(nifty OR nifty50 OR #nifty50)",
    '(banknifty OR "bank nifty" OR #banknifty)',
    "(sensex OR #sensex OR NSE OR BSE)",
    # #intraday is named explicitly in the specification and is given its
    # own group rather than being folded into a broader query, so that its
    # yield can be measured independently.
    "(#intraday OR intraday OR scalping)",
    '("option chain" OR "share market" OR "stock market india")',
    # Flow and expiry vocabulary: FII/DII positioning is closely tracked by
    # Indian retail accounts and generates its own distinct discussion.
    "(FII OR DII OR expiry OR #expiryday)",
    # Options-trading community tags, constrained to index context so the
    # group does not drift into unrelated global markets.
    "(#optionstrading OR #optionselling OR #trading) (nifty OR sensex)",
    # Index heavyweights: single-stock discussion that moves the index but
    # frequently never names it.
    "(reliance OR infosys OR tcs OR hdfcbank OR icicibank)",
    # Broad retail tags, India-constrained.
    "(#stockmarket OR #sharemarket OR #investing) india",
)

_MARKET_OPEN_MIN = 9 * 60 + 15    # 09:15 IST
_MARKET_CLOSE_MIN = 15 * 60 + 30  # 15:30 IST
_EXTENDED_OPEN_MIN = 7 * 60
_EXTENDED_CLOSE_MIN = 19 * 60


@dataclass(frozen=True)
class Query:
    """One planned search: a term group bounded to a time slice."""

    text: str
    start: datetime
    end: datetime
    group: str
    priority: int  # lower is fetched first

    @property
    def key(self) -> str:
        """Stable identity for checkpointing across runs."""
        return f"{self.group}|{int(self.start.timestamp())}"


def _slice_for(ts: datetime) -> tuple[int, int]:
    """Return (slice_width_minutes, priority) for a slice starting at `ts`."""
    ist = ts.astimezone(IST)
    if ist.weekday() >= 5:          # markets closed
        return 120, 3
    minute_of_day = ist.hour * 60 + ist.minute
    if _MARKET_OPEN_MIN <= minute_of_day < _MARKET_CLOSE_MIN:
        return 30, 0                # cash session: densest
    if _EXTENDED_OPEN_MIN <= minute_of_day < _EXTENDED_CLOSE_MIN:
        return 60, 1                # pre-open and post-close commentary
    return 120, 2                   # overnight


def plan(hours_back: int = 24, now: datetime | None = None) -> list[Query]:
    """Build the full query plan for the trailing `hours_back` window."""
    now = now or datetime.now(timezone.utc)
    cursor = now - timedelta(hours=hours_back)

    queries: list[Query] = []
    while cursor < now:
        width, priority = _slice_for(cursor)
        end = min(cursor + timedelta(minutes=width), now)
        for group in TERM_GROUPS:
            queries.append(
                Query(
                    text=(
                        f"{group} "
                        f"since_time:{int(cursor.timestamp())} "
                        f"until_time:{int(end.timestamp())}"
                    ),
                    start=cursor,
                    end=end,
                    group=group,
                    priority=priority,
                )
            )
        cursor = end

    # Densest windows first, most recent first within a priority band.
    queries.sort(key=lambda q: (q.priority, -q.start.timestamp()))
    return queries
