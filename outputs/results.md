# Analysis Results

## Corpus

- Tweets (deduplicated, 24h window): **1,527**
- Unique authors: 940
- Mean lexicon polarity: +0.036
- Tweets carrying a scoreable term: 34.4%
- Promotional (tout) tweets: 0.9%

### Content classes

| Class | Tweets |
|---|---:|
| discussion | 1,243 |
| corporate_action | 132 |
| earnings | 101 |
| mechanical | 51 |

## Signal series

16 buckets total, 8 above the reliability floor (>=5 tweets and >=3 carrying a scoreable term).

| Time (IST) | Tweets | Scoreable | Signal | 95% CI | Bullish share |
|---|---:|---:|---:|---|---:|
| 04 Aug 14:00 | 9 | 7 | +0.109 | [-0.133, +0.612] | 57% [25%, 84%] |
| 04 Aug 14:15 | 37 | 25 | +0.192 | [-0.008, +0.380] | 64% [45%, 80%] |
| 04 Aug 14:30 | 135 | 47 | +0.149 | [-0.029, +0.293] | 70% [56%, 82%] |
| 04 Aug 14:45 | 130 | 54 | +0.130 | [+0.009, +0.262] | 67% [53%, 78%] |
| 04 Aug 15:00 | 208 | 80 | +0.114 | [+0.014, +0.216] | 59% [47%, 69%] |
| 04 Aug 15:15 | 221 | 72 | +0.184 | [+0.082, +0.292] | 67% [56%, 77%] |
| 04 Aug 15:30 | 458 | 134 | +0.028 | [-0.068, +0.122] | 53% [44%, 61%] |
| 04 Aug 15:45 | 316 | 96 | -0.010 | [-0.123, +0.088] | 48% [38%, 58%] |

## Validation against NIFTY 50

| Horizon | n | Pearson r | p | Spearman r | p | Hit rate |
|---|---:|---:|---:|---:|---:|---|
| 15m | 6 | -0.000 | 1.000 | -0.232 | 0.658 | 66.7% [30%, 90%] |
| 30m | 5 | -0.450 | 0.447 | +0.051 | 0.935 | 60.0% [23%, 88%] |

**Interpretation.** No correlation reaches significance at the 5% level. Sample sizes here are very small -- a handful of buckets overlap NSE trading hours, because collection covered only part of one session -- so this analysis can detect a strong relationship but cannot rule out a modest one. Point estimates at these sample sizes are not evidence in either direction, and no lag or horizon search was performed, since that would manufacture significance without predictive content. Establishing whether the signal carries information requires collection across multiple sessions.

## Top TF-IDF terms

| Term | Mean weight |
|---|---:|
| `nifty` | 0.0422 |
| `https` | 0.0347 |
| `https co` | 0.0347 |
| `co` | 0.0347 |
| `the` | 0.0253 |
| `to` | 0.0224 |
| `is` | 0.0222 |
| `cas` | 0.0200 |
| `in` | 0.0191 |
| `and` | 0.0189 |
| `sensex` | 0.0171 |
| `nse` | 0.0169 |
| `at` | 0.0167 |
| `of` | 0.0162 |
| `expiry` | 0.0152 |
