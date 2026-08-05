# Technical Documentation

This document records the design decisions behind the pipeline, the
measurements that drove them, and the tradeoffs accepted. It is organised
around the problems that actually had to be solved rather than a tour of
the module layout, which is in the README.

---

## 1. Collection

### 1.1 Choosing a scraping method

The assignment forbids paid APIs, which rules out X's own API entirely --
it is now pay-per-use with no free tier. That leaves the tools that read
the same endpoints a browser does.

The obvious candidates are dead. `snscrape`, `Twint`, `Nitter`, and
`ntscraper` all depended on anonymous guest tokens, which X withdrew; they
fail together and for the same reason. Tutorials recommending them are the
most common way to lose several hours on this problem.

What remains works from an authenticated session:

| Approach | Verdict |
|---|---|
| Selenium / Playwright + logged-in session | Works; slow, brittle against DOM changes, needs stealth measures |
| `twscrape` (account pool over GraphQL) | Works; actively maintained, handles rate limits and rotation |
| Managed scraping services | Excludable -- paid |

`twscrape` was chosen as the primary backend. It calls the same GraphQL
endpoints the web client uses, with the user's own session cookies, and
returns structured JSON rather than parsed DOM. That last point matters
more than it first appears: DOM scraping would require re-deriving
engagement counts, entity spans, and exact timestamps from rendered
markup, all of which arrive typed and complete from the JSON.

The tradeoff accepted is fragility against a different surface. X rotates
its GraphQL operation IDs, so the library needs periodic updates; a DOM
scraper would break on layout changes instead. Neither is stable, and
choosing between them is choosing which maintenance burden to carry.

### 1.2 The volume problem

A single search query cannot reach 2,000 tweets. X's Latest tab stalls its
pagination cursor at roughly 300 results regardless of how the request is
paged. This is the central constraint, and it is not documented anywhere
official -- it was found by measurement.

Volume therefore comes from partitioning the search space so that each
query returns a largely disjoint result set. Two axes are used:

**Time.** `since_time` and `until_time` accept unix-second bounds, giving
arbitrary slice granularity. Slice width is set inversely to expected
density: 30 minutes during the 09:15-15:30 IST cash session, 60 minutes in
the extended 07:00-19:00 window, 120 minutes overnight and at weekends.

**Terms.** Eight topical groups, deliberately overlapping. Overlap costs
only deduplication, which is cheap; missed coverage cannot be recovered
without re-scraping.

The plan is emitted in descending order of expected yield, so a run
truncated by rate limits still captures the densest windows first.

### 1.3 Two filters that cost 92% of the data

The initial query included `lang:en` and `-filter:retweets`, both of which
seemed reasonable. Measurement on an identical term group and time window:

| Query | Tweets returned |
|---|---:|
| `(#nifty50 OR #nifty OR $NIFTY) lang:en -filter:retweets` | 25 |
| `(nifty OR nifty50 OR #nifty50)` | 335 |

Both filters were wrong for this corpus:

- **`lang:en`** discards most of it. Indian market tweets are heavily
  Hinglish -- Hindi vocabulary in Latin script -- and X tags them
  inconsistently as `en`, `hi`, or `und`. Running separate `lang:en` and
  `lang:hi` queries does not recover the `und` bucket and doubles the
  rate-limit cost for a partial union.
- **`-filter:retweets`** discards data the specification asks for.
  Retweets carry their own engagement metrics and represent real
  amplification. They are retained and flagged (`is_retweet`) so that
  downstream code can decide, rather than having the decision baked in at
  the network boundary.

The general lesson, and the reason this is recorded here: filters applied
during collection are irreversible. Filters applied during processing can
be revised without re-scraping. Everything that can be deferred, is.

### 1.4 Rate limits and concurrency

X permits roughly 50 search requests per 15-minute window per account, per
endpoint. Each paginated query consumes 10-15 requests, so a single account
sustains 3-5 queries per window.

This makes concurrency almost useless with one account: parallel workers
compete for the same quota and exhaust it faster without increasing
throughput. Concurrency is therefore bounded by the number of authenticated
accounts, not by CPU or connection count. `twscrape` rotates accounts
automatically and blocks until reset when all are limited.

Two consequences shaped the collector:

- **Checkpointing is mandatory, not a nicety.** A full run takes hours of
  wall time, most of it waiting. Completed query keys are appended to a
  sidecar file and skipped on resume; output is append-only JSONL. An
  interruption costs only the in-flight query.
- **Bursting is counterproductive.** Random 0.5-1.5s delays between
  queries, and concurrency equal to the account count, keep the request
  pattern closer to human and reduce suspension risk.

Measured yield decay over a run is itself informative. Once a term group's
time slices are exhausted, duplicate rates climb past 50% -- the signal
that queries, not the time window, have become the binding constraint. The
term list was expanded from four groups to eight on that basis, which
raised the final count from 1,527 to 2,838.

---

## 2. Processing

### 2.1 Unicode

Four distinct problems, each needing different treatment:

- **NFKC normalisation** folds fullwidth and mathematical-alphanumeric
  variants that spam accounts use to evade keyword filters. Without it,
  a post written as mathematical bold capitals escapes both term matching
  and deduplication.
- **Zero-width characters** (U+200B-U+200F, U+FEFF) are stripped. ZWJ is
  structurally meaningful in Devanagari, but in tweet text it appears
  almost exclusively as an evasion or copy-paste artefact.
- **Devanagari codepoints are preserved untouched.** Only invisible
  control characters are removed.
- **Emoji are retained, not stripped.** In this corpus they carry
  directional signal -- rocket and chart glyphs are among the strongest
  bullish markers present -- and removing them would discard information
  the sentiment layer depends on.

Raw JSONL is written with `ensure_ascii=False`, which keeps files readable
and roughly halves their size relative to escaped output.

### 2.2 The 24-hour window

Cleaned records included tweets months outside the requested window, one
dating to 2015. The cause is structural: X returns nested `retweetedTweet`
and `quotedTweet` objects alongside timeline results. The *retweet*
occurred inside the window; the tweet being retweeted did not.

Measured contamination was 3.7% of cleaned records, roughly half of them
retweets or quotes. The filter is applied at the processing boundary rather
than at collection, for the same reason as the language filter: raw data
stays unfiltered so the window can be widened later without re-scraping.

### 2.3 Deduplication, and an estimator-variance trap

Two layers:

**Exact.** Tweet IDs are globally unique, so an in-memory `set` gives O(1)
detection. Measured exact-duplicate rate on the final run was 41%,
dominated by term-group overlap -- a tweet mentioning both nifty and sensex
is returned by two queries. This is the intended cost of overlapping
groups.

**Near-duplicate.** MinHash signatures over word 3-gram shingles, indexed
by LSH. URLs are stripped before hashing, since templated spam varies only
its tracking link and would otherwise appear distinct.

The instructive part was the permutation count. With the common default of
`num_perm=64`, a threshold sweep looked almost flat:

| Jaccard threshold | Near-duplicates (64 perm) | Near-duplicates (128 perm) |
|---|---:|---:|
| 0.90 | 15 | 41 |
| 0.85 | 17 | 46 |
| 0.70 | 23 | 67 |
| 0.50 | 42 | 121 |

The flatness was not a property of the corpus. MinHash estimates Jaccard
similarity with standard error near `1/sqrt(num_perm)`, so 64 permutations
carry ~12% error -- wide enough that pairs with true similarity close to
the threshold are classified nearly at random. Doubling to 128 halved that
and raised the measured duplicate count by 2.7x at negligible cost.

This surfaced through a unit test that kept failing on a case that should
obviously have matched. It is worth stating plainly: an LSH result is only
as trustworthy as the variance of its estimator, and the default is not
always adequate.

The threshold remains 0.85, which is conservative. The asymmetry is
deliberate -- a false positive silently deletes a real observation, while a
false negative merely leaves a duplicate for the aggregate to average over.

### 2.4 Storage

Parquet, partitioned by UTC date and hour, ZSTD compressed. Measured
compression against raw JSONL: **14 MB to 688 KB, 20.8x**.

Partitioning by date and hour lets the signal layer push time predicates
into the scan, which matters because it repeatedly reads narrow windows
(single sessions, 15-minute buckets) from a full day's corpus. ZSTD over
Snappy because tweet text compresses well and the CPU cost is irrelevant
beside the network time already spent collecting. `lang` is
dictionary-encoded, collapsing thousands of repeated three-character values
to integer codes.

---

## 3. Signals

### 3.1 Why a domain lexicon was necessary

General-purpose sentiment models fail on this corpus in ways that are not
subtle:

- **Options shorthand reads as neutral.** "CE" (call) and "PE" (put) are
  the dominant directional vocabulary. `banknifty 52000 PE loaded` is an
  explicitly bearish position; no general lexicon encodes this.
- **Bare strike notation is the entire claim.** Inspecting unscored tweets
  showed the dominant idiom is a strike-plus-leg token with no verb:
  `#nifty 24450 ce at 45-50 SL 34` is a complete bullish call. Handled by
  regex rather than vocabulary, since the strike varies continuously.
- **Context inverts meaning.** "Short covering" is bullish (forced buying);
  "short buildup" is bearish. Longest-match ordering ensures the compound
  term wins.
- **Hinglish carries clean signal.** `tezi`, `mandi`, `girawat`, `upar`,
  `neeche` are directionally unambiguous and invisible to English-only
  models.
- **Hashtags are engagement bait, not sentiment.** A bullish call tagged
  `#CRASH` for reach is common. Scoring the tag inverts the author's actual
  claim, so hashtags, URLs, and mentions are stripped before matching. This
  was found by reading the most-bearish-scored tweets and discovering the
  top result was bullish.

### 3.2 Separating content types

A frequency analysis of unscored tweets showed that expanding the lexicon
would not help. The most common terms were `yoy`, `qoq`, `ebitda`, `pat`,
`pbt` (automated earnings summaries), `sebi` (regulatory feeds), and
`straddle`, `spot`, `cas`, `expiry` (market mechanics).

These are not weakly-worded opinion. They are non-directional information,
and no lexicon can score what carries no view. A classifier separates them:

| Class | Tweets | Share |
|---|---:|---:|
| discussion | 2,072 | 73.0% |
| mechanical | 387 | 13.6% |
| earnings | 192 | 6.8% |
| corporate_action | 187 | 6.6% |

Sentiment coverage is reported against discussion tweets, the subset that
plausibly expresses a view, rather than diluted across feeds that never do.

**A classifier bug worth recording.** `cas` was initially placed in the
corporate-action pattern, where it produced 205 of 329 matches and pulled
hundreds of scoreable tweets out of the discussion bucket. Reading those
tweets showed `CAS` is the **Closing Auction Session** -- NSE's call-auction
mechanism for setting the closing price -- and belongs with market
mechanics. It ranks second only to `nifty` by TF-IDF weight in this corpus,
because collection covered the afternoon and CAS speculation dominates the
15:30-15:45 IST window. Over-broad classification is worse than none: it
removes real data silently.

### 3.3 Feature representations

Three complementary views, since none suffices alone:

1. **Lexicon polarity** -- interpretable and domain-specific, but sparse
   (39.2% of tweets carry a scoreable term).
2. **TF-IDF** -- word 1-2 grams plus character 3-5 grams. Character
   n-grams matter more than usual here because Hinglish transliteration is
   inconsistent (`girawat`, `girawt`, `girawaat`) and word tokenisation
   treats those as unrelated. Final matrix: 2,836 x 31,990 at 1.03%
   density.
3. **Engagement and authority** -- non-textual evidence about how much
   weight a view should carry.

Engagement uses `log1p(likes + 2*RT + 3*replies + 3*quotes)`. Replies and
quotes are weighted highest because they require composing a response
rather than a single tap. The log transform is not cosmetic: engagement is
power-law distributed, and on raw counts a single viral tweet would
outweigh several hundred ordinary ones, making any weighted aggregate a
measure of that one tweet.

**URL fragments in TF-IDF.** Before URL stripping, the terms ranked 2nd,
3rd, and 4th by mean TF-IDF weight were `https`, `https co`, and `co`.
Every link-bearing tweet contributes them, and they carry no information.
After stripping, the ranking is `nifty`, `cas`, `nse`, `sensex`, `expiry`,
`market` -- recognisable market vocabulary.

### 3.4 Confidence weighting

Mean polarity alone cannot distinguish a tweet matching one rocket emoji
from one making an explicit five-term directional call -- both score near
0.9. The match count is carried alongside the score for exactly this
reason, and per-tweet confidence combines four adjustments:

| Adjustment | Rationale |
|---|---|
| Evidence count, saturating at 3 | More matched terms means more reliable inference |
| Promotional x0.4 | Tout accounts state direction as advertising, not as a view |
| Earnings / corporate action x0.3 | Any polarity is incidental to the post's purpose |
| Retweet x0.7 | Endorsement is weaker evidence than an original post |

Weights combine multiplicatively with engagement and authority tilts, each
normalised to at most 2x. The asymmetry is intentional: confidence can
drive the weight to zero, while engagement can only modulate it. Absence of
scoreable content is disqualifying; low engagement is merely mild evidence.

### 3.5 Confidence intervals

Two kinds, answering different questions:

**Bootstrap percentile interval** on the weighted mean polarity, for
uncertainty in magnitude. Chosen over a normal-theory interval because the
weighted mean of a bounded, heavily zero-inflated variable is not close to
Gaussian at these bucket sizes (20-450 tweets, of which 20-40% are
scoreable). Resampling preserves the pairing of each score with its weight,
so the interval reflects uncertainty in both.

**Wilson score interval** on the bullish share, for uncertainty in
direction. Chosen over the textbook normal approximation because Wilson
cannot produce bounds outside [0, 1] and does not collapse to zero width
under unanimity -- both of which occur routinely in small buckets and would
silently mislead.

Buckets below 5 tweets or 3 scoreable terms are emitted with a null signal
and a `sparse` flag rather than a noisy number. Overnight hours are
genuinely sparse, and a confident-looking value from three tweets is worse
than an honest gap.

### 3.6 Validation

A signal that has not been checked against prices is an assertion. Bucket
signals were correlated against forward NIFTY 50 log returns using free
Yahoo Finance data (`^NSEI`), consistent with the no-paid-API constraint.

Forward rather than contemporaneous returns: correlating sentiment at time
t with the return over [t-15m, t] would mostly measure traders describing
what just happened.

| Horizon | n | Pearson r | p | Hit rate |
|---|---:|---:|---:|---:|
| 15m | 16 | -0.226 | 0.400 | 68.8% |
| 30m | 16 | -0.153 | 0.572 | 56.2% |
| 60m | 15 | -0.039 | 0.891 | 80.0% |

**No correlation reaches significance.** With 15-16 usable in-hours buckets
from a partial session, this analysis can detect a strong relationship but
cannot rule out a modest one. Hit rates above 50% have Wilson intervals
that comfortably contain 50%.

No lag or horizon search was performed. Searching over specifications until
one reaches significance would produce a number with no predictive content,
and at n=15 something would eventually cross the line by chance. The
limitation is a sample-size problem, and the fix is collection across
multiple sessions, not different statistics.

---

## 4. Memory efficiency

### 4.1 LTTB downsampling

A 1920px-wide figure cannot resolve more than ~2,000 points, so plotting a
full series wastes memory without improving what the viewer sees. The
question is which points to keep.

Naive strategies destroy exactly what matters in market data. Every-nth
sampling drops isolated spikes; mean-bucketing averages them into the
baseline, which is worse.

Largest-Triangle-Three-Buckets selects, per output bucket, the point
forming the largest triangle with the previous selection and the next
bucket's centroid. Maximising triangle area preferentially retains points
deviating from the local trend.

Measured on 100,000 points with a single spike at an index off any sampling
grid, reduced to 500 points by both methods:

| Method | Spike retained |
|---|---|
| LTTB | Yes |
| Every-200th | No |

Same point budget, 99.5% reduction, different information preserved. O(n)
time, O(threshold) space.

### 4.2 Streaming aggregation

Histograms accumulate counts per batch against fixed bin edges, reading
through `pyarrow.dataset.to_batches`. Memory is O(bins) regardless of input
size, which is what makes distribution plots viable over a dataset larger
than RAM.

Reservoir sampling (Algorithm R) provides fixed-memory uniform subsamples
for scatter plots, where a uniform sample is visually indistinguishable
from the full cloud at any realistic figure size.

---

## 4A. Data structures

Selections and their complexity, for the operations that dominate runtime:

| Structure | Where | Operation | Complexity | Why this one |
|---|---|---|---|---|
| `set[int]` | Exact dedup | membership | O(1) avg | Tweet IDs are unique int64; hashing beats any ordered structure, and no ordering is needed |
| MinHash + LSH | Near-dup | similarity query | O(bands) | Avoids the O(n^2) pairwise comparison a naive scan would need; sublinear in corpus size |
| `deque` | Process pool | popleft | O(1) | Bounded FIFO of in-flight futures; a list would be O(n) per pop from the front |
| `dict[str, float]` | Lexicon | term lookup | O(1) | Compiled into a single alternation regex, so scoring is one pass over the text rather than one pass per term |
| Compiled alternation regex | Lexicon | scan | O(len(text)) | Longest-first ordering makes "short covering" win over "short" without a second pass |
| `numpy.datetime64[s]` | Bucketing | integer division | O(n) vectorised | Bucket assignment is `ts // width` on an int64 array; no per-element datetime arithmetic |
| CSR sparse matrix | TF-IDF | storage | O(nnz) | At 1.03% density, dense storage would be ~100x larger for the same information |
| Fixed-width bin array | Streaming histogram | accumulate | O(bins) | Memory independent of input size, which is what makes the plot viable over a dataset larger than RAM |
| Reservoir (Algorithm R) | Scatter sampling | sample | O(k) space | Uniform sample from a stream of unknown length in one pass |

The recurring theme is that every structure holding per-tweet state has a
documented replacement at 10x, and every structure holding per-*bucket* or
per-*bin* state is already constant in corpus size.

---

## 4B. Concurrency

Three distinct workloads, each with a different correct answer:

**Collection is I/O-bound and rate-limited.** `asyncio` with a semaphore
bounded by the number of authenticated accounts. Adding workers beyond
that does not help: parallel requests compete for the same per-account
quota and exhaust it faster without raising throughput. This is the
unusual case where the correct concurrency level is set by an external
policy rather than by hardware.

**Cleaning is CPU-bound and embarrassingly parallel.** `ProcessPoolExecutor`
over chunks of raw JSONL. Unicode normalisation, regex entity extraction,
and timestamp parsing are pure per-record work with no shared state, and a
process pool sidesteps the GIL where a thread pool would not. Submission is
windowed at `2 * workers` chunks so memory stays flat, and futures are
drained in submission order so output is deterministic regardless of worker
count.

**Deduplication is inherently sequential and is left alone.** Whether a
tweet is a near-duplicate depends on every tweet already admitted, so the
LSH index is shared mutable state. Parallelising it would require a
distributed index or a merge step that reintroduces the comparisons
parallelism was supposed to avoid -- and would make the output depend on
scheduling order.

**Measured, and the result argued against the default.** On 4,892 raw
records:

| Configuration | Time | Throughput |
|---|---:|---:|
| `--workers 1` (sequential) | 1.48s | 3,470 rec/s |
| `--workers 8` (process pool) | 2.57s | 1,994 rec/s |

The pool is **1.7x slower** at this size. Process startup and the pickling
of chunks across the process boundary cost more than the cleaning they
parallelise, and per-record work here is a few microseconds of regex and
`unicodedata` calls -- far too little to amortise a ~200ms pool spin-up
across eight processes.

The default was therefore changed to select automatically: sequential below
50,000 records, pooled above. Shipping a parallel default that is
measurably slower would be worse than shipping none, and the honest version
of "implement concurrent processing where applicable" includes establishing
where it is not applicable. The pool is retained and tested because the
crossover is real -- it is simply above the current corpus size.

---

## 4C. Anti-bot handling

X's defences are layered, and each layer needs a different response:

| Defence | Response |
|---|---|
| Per-account rate limits | Account pool with automatic rotation; block until reset rather than retrying into a wall |
| TLS fingerprinting | `curl-cffi` backend, which mimics a real browser's TLS handshake rather than Python's default |
| Request-pattern analysis | Randomised 0.5-1.5s inter-query delays; concurrency capped at the account count |
| GraphQL operation ID rotation | Pluggable backend interface, so a DOM-based collector can take over without touching the collector |
| Headless browser detection | Selenium backend uses `undetected_chromedriver` and runs non-headless by default |
| Session-age heuristics | Persistent Chrome profile directory, so the session looks continuous across runs |

The general posture is to look like a slow user rather than to defeat
detection outright. Rate-limit waits are respected rather than circumvented
-- collection is designed to take hours, and every checkpointing decision
follows from accepting that rather than fighting it.

---

## 4D. Why not Selenium as the primary backend

The specification suggests Selenium. Both approaches were implemented, and
`twscrape` was chosen as the default for reasons worth stating, since the
choice is not obvious:

| | GraphQL (`twscrape`) | Selenium (DOM) |
|---|---|---|
| Throughput | ~300 tweets in seconds | ~300 tweets in minutes |
| Engagement counts | Exact, typed | Abbreviated in DOM ("1.2K"), parsed approximately |
| View counts | Available | Not exposed in DOM at all |
| Entity extraction | Pre-parsed spans | Re-derived by regex from rendered text |
| Timestamps | Exact ISO | Exact (from `<time datetime>`) |
| Resource cost | One HTTP client | A Chrome process per worker |
| Breaks when | Operation IDs rotate | DOM is restructured |

The decisive factor is fidelity, not speed. The specification requires
engagement metrics, and the DOM renders them abbreviated, so a
DOM-primary pipeline would report approximate counts for every tweet above
1,000 interactions and no view counts whatsoever. That degradation would
propagate into the engagement weighting and therefore into every aggregate
signal.

`SeleniumBackend` is implemented and functional
(`src/mkt_intel/scraper/selenium_backend.py`), behind the same
`ScraperBackend` ABC. Because the two backends break for unrelated reasons,
keeping both means no single upstream change stops collection -- which is
the actual argument for writing the interface rather than picking one.

---

## 5. Scaling to 10x

The current corpus is 2,836 tweets. At roughly 30,000 the following bind:

| Component | Current | At 10x | Change needed |
|---|---|---|---|
| Exact dedup | Python `set`, ~2 MB | ~20 MB | None |
| Exact dedup at 10M | -- | ~600 MB | Bloom filter, ~12 MB at 1% FPR |
| MinHash LSH | In-memory index | Grows linearly | Redis-backed LSH, or shard by time |
| TF-IDF | Vocabulary in memory | ~300k terms | `HashingVectorizer`, fixed 2^18, already implemented |
| Parquet reads | Full table | Exceeds RAM | Already batched via `to_batches` |
| Collection | 1 account, ~200 req/hr | Same | Account pool; rotation already implemented |

**The Bloom filter tradeoff** is worth stating explicitly. At 10M IDs a
Python `set` costs ~600 MB against ~12 MB for a Bloom filter at 1% false
positive rate. The cost is that false positives silently drop real tweets
-- roughly 1 in 100 at that rate. This is acceptable when the alternative
is not fitting in memory, and unacceptable if exact recall matters. The
right choice depends on whether the pipeline is feeding a live signal
(where 1% loss is noise) or an audit (where it is not).

**Collection, not computation, is the real bottleneck.** Every processing
stage is linear and streamable. Rate limits are not: 10x the data needs
10x the accounts or 10x the wall time, and no amount of engineering on this
side of the network changes that.

---

## 6. What I would do differently

- **Collect across multiple sessions before analysing.** Every statistical
  limitation in this report traces back to having part of one trading day.
- **Test the estimator, not just the code.** The MinHash permutation issue
  produced plausible-looking numbers that were wrong by a factor of 2.7.
  Nothing crashed.
- **Read the data earlier.** The hashtag-polarity bug, the `CAS`
  misclassification, and the URL-fragment problem were all found by
  printing actual tweets, not by inspecting aggregates. Each had been
  silently corrupting results before that.
- **Add a Selenium backend.** `twscrape` works, but a second implementation
  behind the same interface would make the system resilient to X changing
  its GraphQL surface. The interface exists; the second backend does not.
