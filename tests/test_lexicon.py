"""Sentiment scoring and content classification.

These tests encode the domain claims the lexicon rests on: that CE and PE
are directional, that negation inverts, that hashtags are engagement bait
rather than sentiment. If any of these regress, every downstream aggregate
is wrong in a way that no crash would reveal.
"""
from mkt_intel.signals.lexicon import classify, score_text


def test_call_option_bullish():
    score, n, _ = score_text("nifty 24500 CE loaded for tomorrow")
    assert score > 0 and n > 0


def test_put_option_bearish():
    """A general sentiment model reads this as neutral; it is a short."""
    score, n, _ = score_text("banknifty 52000 PE loaded for tomorrow")
    assert score < 0 and n > 0


def test_negation_inverts_polarity():
    pos, _, _ = score_text("market looks bullish here")
    neg, _, _ = score_text("market is not bullish here")
    assert pos > 0 > neg


def test_short_covering_is_bullish():
    """Longest-match ordering must beat the bare 'short' term."""
    score, _, _ = score_text("strong short covering seen in banknifty")
    assert score > 0


def test_hashtags_excluded_from_scoring():
    """A bullish call tagged #CRASH for reach must not score bearish."""
    with_tag, _, _ = score_text("23400 close and sustain, 23900 will come #CRASH")
    without, _, _ = score_text("23400 close and sustain, 23900 will come")
    assert with_tag == without


def test_promotional_flagged():
    _, _, promo = score_text("150% target done, join my telegram, link in bio")
    assert promo


def test_unscoreable_returns_zero_evidence():
    score, n, _ = score_text("Nifty spot 24530, expiry tomorrow")
    assert n == 0 and score == 0.0


def test_classify_separates_non_directional():
    assert classify("Q1FY26 revenue up 12% YoY, EBITDA margin 18%") == "earnings"
    # "margin" is an earnings term and earnings is checked first, so the
    # corporate-action example must not straddle both categories.
    assert classify("SEBI approves buyback for the company") == "corporate_action"
    assert classify("CAS closing auction likely to print higher") == "mechanical"
    assert classify("nifty looking strong above 24500") == "discussion"


def test_emoji_repetition_capped():
    """Five rockets are emphasis, not five independent signals."""
    one, n1, _ = score_text("going up 🚀")
    many, n5, _ = score_text("going up 🚀🚀🚀🚀🚀")
    assert n5 <= n1 + 2
