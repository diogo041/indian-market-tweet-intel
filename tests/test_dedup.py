"""Deduplication behaviour.

The near-duplicate layer is the part worth testing: exact ID matching is
trivially correct, but MinHash-LSH has a threshold whose behaviour on real
text is not obvious, and a wrongly-tuned threshold silently discards
legitimate tweets rather than failing loudly.

The threshold is deliberately conservative. Writing these tests surfaced
that at 0.85, adding a single token to a ten-word tweet is enough to fall
below the similarity bar -- shorter texts have fewer shingles, so each one
carries more weight. Real templated spam is longer and varies less
proportionally, which is the case exercised below. Erring towards keeping
borderline pairs is the right asymmetry here: a false positive silently
deletes a real observation, while a false negative merely leaves a
duplicate for the aggregate to average over.
"""
from mkt_intel.processing.dedup import Deduplicator


def test_exact_duplicate_rejected():
    d = Deduplicator()
    assert d.is_new(1, "nifty breaking out above 24500")
    assert not d.is_new(1, "nifty breaking out above 24500")
    assert d.stats.exact_dupes == 1


def test_near_duplicate_rejected():
    """Templated spam differing only by a trailing token is one observation."""
    d = Deduplicator(threshold=0.85)
    base = ("BANKNIFTY 52000 CE BUY NOW target 200 stoploss 150 "
            "sure shot call join telegram for daily profit")
    assert d.is_new(1, f"{base} https://t.co/aaaaaa")
    assert not d.is_new(2, f"{base} https://t.co/bbbbbb")
    assert d.stats.near_dupes == 1


def test_distinct_tweets_kept():
    """Shared market vocabulary must not collapse independent tweets."""
    d = Deduplicator(threshold=0.85)
    assert d.is_new(1, "nifty support at 24400 looks strong today")
    assert d.is_new(2, "banknifty resistance 52800 watch for breakdown")
    assert d.stats.near_dupes == 0


def test_urls_stripped_before_hashing():
    """Spam varies only its tracking link; URLs must not create distinctness."""
    d = Deduplicator(threshold=0.85)
    base = ("sure shot intraday tip buy now huge profit guaranteed "
            "daily calls accuracy ninety percent limited seats")
    assert d.is_new(1, f"{base} https://t.co/aaaaaa")
    assert not d.is_new(2, f"{base} https://t.co/bbbbbb")


def test_stats_accounting():
    d = Deduplicator()
    d.is_new(1, "nifty is bullish today after the breakout")
    d.is_new(1, "nifty is bullish today after the breakout")
    d.is_new(2, "sensex closed lower on fii selling pressure")
    assert d.stats.total == 3
    assert d.stats.kept == 2
