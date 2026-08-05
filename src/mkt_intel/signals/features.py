"""Convert tweet text into numerical feature vectors.

Three complementary representations, because no single one captures what
matters in this corpus:

  1. Lexicon polarity -- interpretable, domain-specific, but sparse
     (~34% of discussion tweets carry a scoreable term).
  2. TF-IDF -- dense and unsupervised, catching term co-occurrence the
     hand-built lexicon misses.
  3. Engagement and authority -- non-textual signal about how much weight
     a given view should carry.

TF-IDF combines word and character n-grams. Word n-grams (1-2) capture
"short covering" and "gap up" as units. Character n-grams (3-5) matter more
than usual here: Hinglish is transliterated inconsistently -- "girawat",
"girawt", "girawaat" all appear -- and character n-grams match across those
variants where word-level tokenisation treats them as unrelated. They also
survive the aggressive abbreviation typical of the register ("bnf" for
Bank Nifty, "tgt" for target).

`HashingVectorizer` is offered alongside `TfidfVectorizer` for the scaling
path. TF-IDF must hold a vocabulary in memory that grows with corpus size;
hashing projects into a fixed 2**18 space with no vocabulary at all, giving
flat memory at 10x data. The cost is losing the term-to-index mapping, so
feature importances become uninspectable -- acceptable for production
scoring, not for the analysis in this repository.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import (
    ENGLISH_STOP_WORDS,
    HashingVectorizer,
    TfidfVectorizer,
)

from mkt_intel.signals.lexicon import classify, score_text

_URL_RE = re.compile(r"https?://\S+|t\.co/\S+")

# Domain-specific stopwords. Standard English stopwords are removed for the
# word-level vectoriser, but t.co URL fragments needed explicit handling:
# every link-bearing tweet contributes "https", "co", and the bigram
# "https co", which ranked 2nd, 3rd, and 4th by mean TF-IDF weight before
# removal despite carrying no information about content.
_EXTRA_STOPWORDS = frozenset({"https", "http", "co", "amp", "rt"})


@dataclass(frozen=True)
class TweetFeatures:
    """Per-tweet features, aligned row-wise with the input table."""

    polarity: np.ndarray        # [-1, 1] lexicon score
    evidence: np.ndarray        # count of matched sentiment terms
    is_promo: np.ndarray        # bool: tout / paid-group solicitation
    content_class: list[str]    # discussion | earnings | ...
    engagement: np.ndarray      # log-scaled interaction weight
    authority: np.ndarray       # log-scaled follower weight
    confidence: np.ndarray      # [0, 1] per-tweet signal reliability


def engagement_weight(
    likes: np.ndarray,
    retweets: np.ndarray,
    replies: np.ndarray,
    quotes: np.ndarray,
) -> np.ndarray:
    """Log-scaled composite of interaction counts.

    Replies and quotes are weighted above retweets and likes because they
    require composing a response rather than a single tap, and so indicate
    that a post provoked engagement rather than merely passive approval.

    log1p compresses the heavy right tail. Engagement on X is roughly
    power-law distributed, and on raw counts a single viral tweet would
    contribute more weight than several hundred ordinary ones combined --
    which would make any weighted aggregate a measure of that one tweet.
    """
    raw = likes + 2.0 * retweets + 3.0 * replies + 3.0 * quotes
    return np.log1p(raw)


def authority_weight(followers: np.ndarray) -> np.ndarray:
    """Log-scaled follower count, capped.

    Capped at log1p(1e6) so that a handful of very large accounts cannot
    dominate a time bucket. Follower count is a weak proxy for accuracy in
    any case -- it measures reach, not skill -- so it enters as a mild tilt
    rather than a primary weight.
    """
    return np.minimum(np.log1p(followers), math.log1p(1e6))


def confidence_score(
    evidence: np.ndarray,
    is_promo: np.ndarray,
    content_class: list[str],
    is_retweet: np.ndarray,
) -> np.ndarray:
    """Per-tweet reliability in [0, 1], used to weight the aggregate.

    Four adjustments, each with a distinct rationale:

      * Evidence count. A tweet matching one rocket emoji and one matching
        five explicit directional terms both score near 0.9 in mean
        polarity, but they are not equally informative. Confidence rises
        with matched terms and saturates at three, past which additional
        matches add little.

      * Promotional flag. Tout accounts post directional claims as
        advertising rather than as views, so their sentiment is downweighted
        rather than removed -- whether it carries information is tested in
        validation, not assumed here.

      * Content class. Earnings and corporate-action posts are downweighted
        because any polarity they score is incidental to their purpose.

      * Retweet status. A retweet is an endorsement, but a weaker signal of
        the retweeter's own view than an original post.
    """
    conf = np.minimum(evidence, 3.0) / 3.0
    conf = np.where(is_promo, conf * 0.4, conf)
    cls = np.array(content_class)
    conf = np.where(np.isin(cls, ["earnings", "corporate_action"]),
                    conf * 0.3, conf)
    conf = np.where(is_retweet, conf * 0.7, conf)
    return np.clip(conf, 0.0, 1.0)


def extract(table) -> TweetFeatures:
    """Build per-tweet features from a pyarrow Table of cleaned tweets."""
    texts = table.column("content").to_pylist()

    scored = [score_text(t) for t in texts]
    polarity = np.array([s[0] for s in scored], dtype=np.float32)
    evidence = np.array([s[1] for s in scored], dtype=np.int16)
    is_promo = np.array([s[2] for s in scored], dtype=bool)
    content_class = [classify(t) for t in texts]

    def col(name: str) -> np.ndarray:
        return np.asarray(table.column(name).to_pylist(), dtype=np.float64)

    engagement = engagement_weight(
        col("like_count"), col("retweet_count"),
        col("reply_count"), col("quote_count"),
    ).astype(np.float32)

    authority = authority_weight(col("followers")).astype(np.float32)
    is_retweet = np.asarray(table.column("is_retweet").to_pylist(), dtype=bool)

    confidence = confidence_score(
        evidence, is_promo, content_class, is_retweet
    ).astype(np.float32)

    return TweetFeatures(
        polarity=polarity,
        evidence=evidence,
        is_promo=is_promo,
        content_class=content_class,
        engagement=engagement,
        authority=authority,
        confidence=confidence,
    )


def build_tfidf(
    texts: list[str], max_features: int = 20_000
) -> tuple[sparse.csr_matrix, TfidfVectorizer, TfidfVectorizer]:
    """Fit word- and character-level TF-IDF and return the stacked matrix.

    URLs are stripped before vectorising. Character n-grams are computed on
    the stripped text too, since t.co URL character sequences would
    otherwise produce spurious similarity between unrelated tweets that
    happen to link somewhere.

    `min_df=2` drops terms appearing in a single document, which are almost
    entirely typos and one-off tickers and would otherwise inflate the
    vocabulary without contributing generalisable signal.
    """
    cleaned = [_URL_RE.sub(" ", t) for t in texts]
    stop = list(ENGLISH_STOP_WORDS | _EXTRA_STOPWORDS)

    word_vec = TfidfVectorizer(
        ngram_range=(1, 2), min_df=2, max_features=max_features,
        sublinear_tf=True, lowercase=True, stop_words=stop,
    )
    char_vec = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(3, 5), min_df=3,
        max_features=max_features, sublinear_tf=True, lowercase=True,
    )
    word_x = word_vec.fit_transform(cleaned)
    char_x = char_vec.fit_transform(cleaned)
    return sparse.hstack([word_x, char_x], format="csr"), word_vec, char_vec


def build_hashed(texts: list[str], n_features: int = 2 ** 18) -> sparse.csr_matrix:
    """Fixed-memory alternative to `build_tfidf` for the 10x scaling path.

    Holds no vocabulary, so memory is constant in corpus size and the
    transform is stateless across batches. Term-to-index inspection is
    lost, which is why it is not the default here.
    """
    cleaned = [_URL_RE.sub(" ", t) for t in texts]
    vec = HashingVectorizer(
        ngram_range=(1, 2), n_features=n_features,
        alternate_sign=False, norm="l2", lowercase=True,
    )
    return vec.transform(cleaned)
