"""Normalise raw twscrape records into the analysis schema.

Unicode handling is the substantive concern here. Indian market tweets mix
Latin script, Devanagari, transliterated Hinglish, and heavy emoji use, and
each needs different treatment:

  * NFKC normalisation folds the fullwidth and mathematical-alphanumeric
    variants that spam accounts use to evade keyword filters
    ("𝗡𝗜𝗙𝗧𝗬" -> "NIFTY"), which matters because those posts would
    otherwise escape both deduplication and term matching.
  * Zero-width characters (U+200B-U+200D, U+FEFF) are stripped. ZWJ is
    structurally meaningful in Devanagari but appears in tweet text almost
    exclusively as an evasion or copy-paste artefact; Devanagari codepoints
    themselves are preserved untouched.
  * Emoji are retained rather than stripped. In this corpus they carry
    directional signal -- rocket, chart-up, and green-circle glyphs are
    among the strongest bullish markers available, and discarding them
    would throw away information the sentiment layer depends on.

Engagement counts are coerced to zero when absent. `viewCount` in
particular is null for older tweets and for accounts that have disabled
it, and a null would propagate through every downstream aggregate.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_WHITESPACE = re.compile(r"[ \t]+")
_NEWLINES = re.compile(r"\n{3,}")


def normalise_text(text: str) -> str:
    """Apply NFKC folding, strip invisible control characters, tidy spacing."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    text = _NEWLINES.sub("\n\n", text)
    return text.strip()


def _as_int(value: Any, default: int = 0) -> int:
    """Coerce possibly-null engagement counts to a usable integer."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value: Any) -> datetime | None:
    """Parse the ISO timestamp emitted by the collector's `default=str`."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _usernames(mentioned: Any) -> list[str]:
    """`mentionedUsers` holds user objects, not bare strings."""
    if not mentioned:
        return []
    out: list[str] = []
    for m in mentioned:
        if isinstance(m, dict) and m.get("username"):
            out.append(str(m["username"]))
        elif isinstance(m, str):
            out.append(m)
    return out


def _strings(values: Any) -> list[str]:
    if not values:
        return []
    return [str(v) for v in values if v]


def clean(record: dict) -> dict | None:
    """Map one raw record to the storage schema. Returns None if unusable.

    A record is unusable when it lacks an ID, a timestamp, or any text --
    these appear occasionally as tombstones for deleted or withheld tweets
    and cannot contribute to any signal.
    """
    tweet_id = record.get("id")
    created_at = _parse_ts(record.get("date"))
    content = normalise_text(record.get("rawContent") or "")

    if not tweet_id or created_at is None or not content:
        return None

    user = record.get("user") or {}

    return {
        "tweet_id": int(tweet_id),
        "created_at": created_at,
        "username": str(user.get("username") or ""),
        "user_id": _as_int(user.get("id")),
        "followers": _as_int(user.get("followersCount")),
        "content": content,
        "lang": str(record.get("lang") or "und"),
        "reply_count": _as_int(record.get("replyCount")),
        "retweet_count": _as_int(record.get("retweetCount")),
        "like_count": _as_int(record.get("likeCount")),
        "quote_count": _as_int(record.get("quoteCount")),
        "view_count": _as_int(record.get("viewCount")),
        "hashtags": [h.lower() for h in _strings(record.get("hashtags"))],
        "cashtags": [c.upper() for c in _strings(record.get("cashtags"))],
        "mentions": _usernames(record.get("mentionedUsers")),
        "is_retweet": record.get("retweetedTweet") is not None,
        "is_reply": record.get("inReplyToTweetId") is not None,
        "is_quote": bool(record.get("isQuoteStatus")),
        "verified": bool(user.get("verified") or user.get("blue")),
        "conversation_id": _as_int(record.get("conversationId")),
    }