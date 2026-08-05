"""Domain sentiment lexicon and content classifier for Indian equity
market discussion on X.

General-purpose sentiment models fail badly on this corpus, for reasons
specific to how Indian retail traders write:

  * Options shorthand carries directional meaning that reads as neutral.
    "CE" (call option) and "PE" (put option) are the dominant vocabulary --
    "banknifty 52000 PE loaded" is an explicitly bearish position, and
    "24800 CE" is bullish, but no general lexicon encodes this.

  * Strike notation often stands alone as the entire claim. Inspection of
    unscored tweets showed the dominant idiom is a bare strike-plus-leg
    token with no surrounding verb: "#nifty 24450 ce at 45-50 SL 34" is a
    complete bullish call. This is handled by pattern match rather than by
    vocabulary, since the strike varies continuously.

  * The same word inverts by context. "Short covering" is bullish (forced
    buying); "short buildup" is bearish. "SL hit" is bearish regardless of
    direction because it marks a losing exit.

  * Hinglish appears untranslated and inconsistently transliterated:
    "upar" (up), "neeche" (down), "tezi" (bullishness), "mandi"
    (bearishness), "girawat" (decline), "ke liye ready ho jao" (get ready
    for). These carry clean directional signal and are invisible to
    English-only models.

  * Emoji function as primary sentiment markers rather than decoration.
    Rocket and chart-up glyphs frequently constitute the entire directional
    claim of a tweet.

A frequency analysis of unscored tweets drove one structural decision.
Among the most common terms in tweets the lexicon could not score were
`yoy`, `qoq`, `ebitda`, `pat`, and `pbt` -- automated earnings summaries --
alongside `sebi` and `auction` (regulatory feeds) and `straddle`, `spot`,
and `expiry` (options mechanics). These are not weakly-worded opinion; they
are non-directional information. Expanding the sentiment lexicon cannot
score them and would only add noise, so `classify` separates them instead.
Sentiment coverage is then reported against discussion tweets, the subset
that plausibly carries a view, while the volume of each content type
remains available as an independent feature.

Weights are on [-1, 1] and reflect confidence in directional meaning, not
intensity of feeling. "PE buy" is a stated position (high confidence);
"weak" is a mood (low). This distinction matters because the aggregation
layer treats these scores as evidence about positioning, not affect.

Weights are hand-assigned from domain knowledge and are the most
subjective component of the pipeline. `validate.py` tests them against
realised NIFTY returns rather than assuming they are correct.
"""
from __future__ import annotations

import re

BULLISH: dict[str, float] = {
    # Explicit positioning
    "ce buy": 0.9, "buy ce": 0.9, "ce loaded": 0.85, "call buy": 0.85,
    "buy call": 0.8, "buy": 0.65, "long": 0.7, "went long": 0.85,
    "longs": 0.6, "buying": 0.6, "accumulate": 0.7, "hold": 0.3,
    "add on dips": 0.7, "buy on dips": 0.7, "value buy": 0.7,
    "btst": 0.6, "took position": 0.5, "averaged": 0.4,
    # Structure and momentum
    "breakout": 0.8, "breaking out": 0.8, "higher high": 0.75,
    "short covering": 0.8, "reversal up": 0.75, "bounce": 0.6,
    "support holding": 0.7, "gap up": 0.7, "upper circuit": 0.85,
    "all time high": 0.8, "ath": 0.7, "52 week high": 0.75,
    "rally": 0.7, "surge": 0.7, "momentum": 0.4, "recovery": 0.6,
    "up move": 0.7, "upside": 0.65, "uptrend": 0.75, "higher": 0.5,
    "bulls in control": 0.85, "highest ever": 0.6, "milestone": 0.4,
    # Outcome
    "target achieved": 0.8, "tgt achieved": 0.8, "target hit": 0.75,
    "booked profit": 0.6, "profit booked": 0.6, "2x": 0.5,
    "outperform": 0.7, "beat": 0.5,
    # Sentiment
    "bullish": 0.9, "bull run": 0.85, "positive": 0.5, "strong": 0.55,
    "tezi": 0.8, "upar": 0.6, "badhega": 0.7, "green": 0.45,
    "cheap": 0.4, "ke liye ready": 0.5,
}

BEARISH: dict[str, float] = {
    # Explicit positioning
    "pe buy": -0.9, "buy pe": -0.9, "pe loaded": -0.85, "put buy": -0.85,
    "short": -0.7, "went short": -0.85, "shorts": -0.6, "shorting": -0.8,
    "sell": -0.6, "selling": -0.65, "exit": -0.4, "book loss": -0.7,
    "avoid": -0.6,
    # Structure and momentum
    "breakdown": -0.8, "breaking down": -0.8, "lower low": -0.75,
    "short buildup": -0.8, "long unwinding": -0.75, "resistance": -0.35,
    "gap down": -0.7, "lower circuit": -0.85, "52 week low": -0.75,
    "correction": -0.6, "crash": -0.9, "selloff": -0.8, "sell off": -0.8,
    "fall": -0.6, "falls": -0.65, "falling": -0.65, "dump": -0.75,
    "profit booking": -0.5, "down move": -0.7, "downside": -0.65,
    "downtrend": -0.75, "lower": -0.5, "down": -0.5,
    "bears in control": -0.85, "bears completely in control": -0.9,
    "points down": -0.7, "decline": -0.65, "market decline": -0.75,
    # Outcome
    "sl hit": -0.8, "stoploss hit": -0.8, "stop loss hit": -0.8,
    "sl triggered": -0.8, "trapped": -0.7, "underperform": -0.7,
    "miss": -0.5,
    # Sentiment
    "bearish": -0.9, "bear market": -0.85, "negative": -0.5, "weak": -0.55,
    "mandi": -0.8, "neeche": -0.6, "girawat": -0.75, "girega": -0.7,
    "red": -0.45, "panic": -0.8, "fear": -0.6, "overvalued": -0.55,
    "caution": -0.5, "risky": -0.45,
}

# Institutional flow: strong directional signal in Indian markets, where
# FII activity is closely tracked and widely reported by retail accounts.
FLOW: dict[str, float] = {
    "fii buying": 0.8, "fii buy": 0.8, "dii buying": 0.7,
    "fii selling": -0.8, "fii sell": -0.8, "dii selling": -0.6,
    "fii short": -0.75, "fii long": 0.75,
}

EMOJI: dict[str, float] = {
    "🚀": 0.9, "📈": 0.8, "🟢": 0.6, "💚": 0.5, "🐂": 0.85, "⬆": 0.6,
    "🔥": 0.4, "💪": 0.5, "🎯": 0.4,
    "📉": -0.8, "🔴": -0.6, "❤": -0.3, "🐻": -0.85, "⬇": -0.6,
    "💀": -0.7, "😭": -0.6, "🩸": -0.75, "⚠": -0.4,
}

# Bare option-leg notation: "24450 ce", "24500CE", "52000 pe". Confidence
# is moderate rather than high because the same notation also appears in
# neutral commentary about where premium is currently trading.
_OPTION_LEG = re.compile(r"\b(\d{4,6})\s*(ce|pe)\b", re.IGNORECASE)
_OPTION_LEG_WEIGHT = {"ce": 0.6, "pe": -0.6}

# Negation inverts polarity within a short window. "not bullish" and
# "no breakout" are common and would otherwise score with the wrong sign.
NEGATIONS = frozenset({"not", "no", "never", "isn't", "wasn't", "won't",
                       "don't", "doesn't", "didn't", "nahi", "nai"})
NEGATION_WINDOW = 3  # tokens

LEXICON: dict[str, float] = {**BULLISH, **BEARISH, **FLOW}

# Longest-first so "short covering" matches before "short" inverts it.
_TERMS = sorted(LEXICON, key=len, reverse=True)
_PATTERN = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(t) for t in _TERMS) + r")(?!\w)",
    re.IGNORECASE,
)

_HASHTAG = re.compile(r"[#＃]\w+")
_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"@\w+")
_PROMO = re.compile(
    r"join my|telegram|link in (the )?(profile|bio)|dm for|"
    r"paid group|subscribe now|whatsapp group",
    re.IGNORECASE,
)

# `cas` was removed from the corporate-action pattern after it produced 205
# of 329 matches, nearly all false positives from hashtag fragments. The
# same applied to `listing`, `split`, and `penalty`, which occur freely in
# ordinary market commentary. Over-broad classification is worse than none:
# it silently removes scoreable tweets from the discussion bucket.
_EARNINGS = re.compile(
    r"\b(yoy|qoq|ebitda|pat|pbt|revenue|margin|net profit|"
    r"results|q[1-4]fy\d{2}|topline|bottomline)\b", re.IGNORECASE)
_CORP_ACTION = re.compile(
    r"\b(sebi|dividend|bonus issue|buyback|rights issue|"
    r"stock split|delisting)\b", re.IGNORECASE)
_MECHANICAL = re.compile(
    r"\b(straddle|strangle|spot price|open interest|oi data|"
    r"vix|max pain|pcr)\b", re.IGNORECASE)


def classify(text: str) -> str:
    """Bucket a tweet by information type.

    Returns one of: earnings, corporate_action, mechanical, discussion.
    Only discussion tweets are expected to carry a directional view; the
    others are automated or mechanical feeds whose volume is informative
    even though their sentiment is not.
    """
    if _EARNINGS.search(text):
        return "earnings"
    if _CORP_ACTION.search(text):
        return "corporate_action"
    if _MECHANICAL.search(text):
        return "mechanical"
    return "discussion"


def score_text(text: str) -> tuple[float, int, bool]:
    """Return (mean polarity, match count, is_promotional) for one tweet.

    Hashtags, URLs, and mentions are stripped before matching. Indian
    fintwit uses hashtags for reach rather than meaning -- a bullish call
    tagged #CRASH is common, and scoring the tag inverts the polarity of
    the author's actual claim. Hashtags remain available as a separate
    column for anyone who wants to model them explicitly.

    Polarity is a mean rather than a sum so that tweet length does not
    inflate the score: a long tweet listing ten bullish terms is not ten
    times more bullish than a short one, and sum-based scoring would let
    verbose accounts dominate every aggregate they appear in.

    The match count is returned alongside so the aggregation layer can
    weight by evidence. A lone rocket emoji and an explicit multi-term
    directional call both yield a mean near 0.9, but they are not equally
    informative, and the count is what distinguishes them.

    Promotional tweets are flagged rather than dropped. Tout accounts are a
    large and structurally distinct share of this corpus, and whether their
    sentiment carries information is an empirical question for validation,
    not an assumption to bake in here.
    """
    is_promo = bool(_PROMO.search(text))
    body = _MENTION.sub(" ", _URL.sub(" ", _HASHTAG.sub(" ", text)))
    lowered = body.lower()
    scores: list[float] = []

    for match in _PATTERN.finditer(lowered):
        weight = LEXICON[match.group(1).lower()]
        window = lowered[: match.start()].split()[-NEGATION_WINDOW:]
        if any(tok.strip(".,!?") in NEGATIONS for tok in window):
            weight = -weight
        scores.append(weight)

    for char, weight in EMOJI.items():
        count = body.count(char)
        if count:
            # Cap repetition: "🚀🚀🚀🚀🚀" is emphasis, not five signals.
            scores.extend([weight] * min(count, 3))

    for _, leg in _OPTION_LEG.findall(body):
        scores.append(_OPTION_LEG_WEIGHT[leg.lower()])

    if not scores:
        return 0.0, 0, is_promo
    return sum(scores) / len(scores), len(scores), is_promo