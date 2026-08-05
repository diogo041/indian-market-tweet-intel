"""Two-layer deduplication.

Layer 1 (exact): tweet IDs are globally unique, so an in-memory set gives
O(1) exact detection. At 10x scale a Bloom filter is the right swap -- 10M
int64 IDs cost ~600MB as a Python set versus ~12MB as a Bloom filter at 1%
false-positive rate. The tradeoff is that false positives silently drop
real tweets, which is acceptable when the alternative is not fitting in
memory at all.

Layer 2 (near-duplicate): Indian fintwit carries heavy copy-paste spam --
identical tip-seller blasts posted from dozens of accounts within minutes.
These have distinct IDs and survive layer 1, but they are not distinct
observations and would bias any sentiment aggregate toward whatever the
spam says. MinHash over character-normalised 3-grams with LSH banding
detects them in sublinear time.

The 0.85 Jaccard threshold was chosen to catch templated spam while
preserving genuinely independent tweets that happen to share market
vocabulary ("nifty support 24800") -- those share terms but differ in
overall shingle composition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from datasketch import MinHash, MinHashLSH

_WS = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+")


@dataclass
class DedupStats:
    total: int = 0
    exact_dupes: int = 0
    near_dupes: int = 0
    kept: int = 0


class Deduplicator:
    """Streaming deduplicator. Call `is_new` once per incoming tweet."""

    def __init__(self, threshold: float = 0.85, num_perm: int = 128,
                 shingle_size: int = 3) -> None:
        self._seen_ids: set[int] = set()
        self._lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        self._num_perm = num_perm
        self._k = shingle_size
        self.stats = DedupStats()

    def _signature(self, text: str) -> MinHash:
        # URLs are stripped: spam varies only its tracking links, which would
        # otherwise make each copy look distinct.
        norm = _WS.sub(" ", _URL.sub("", text.lower())).strip()
        tokens = norm.split()
        mh = MinHash(num_perm=self._num_perm)
        if len(tokens) < self._k:
            mh.update(norm.encode("utf-8"))
            return mh
        for i in range(len(tokens) - self._k + 1):
            mh.update(" ".join(tokens[i:i + self._k]).encode("utf-8"))
        return mh

    def is_new(self, tweet_id: int, text: str) -> bool:
        self.stats.total += 1
        if tweet_id in self._seen_ids:
            self.stats.exact_dupes += 1
            return False
        self._seen_ids.add(tweet_id)

        sig = self._signature(text)
        if self._lsh.query(sig):
            self.stats.near_dupes += 1
            return False
        self._lsh.insert(str(tweet_id), sig)
        self.stats.kept += 1
        return True