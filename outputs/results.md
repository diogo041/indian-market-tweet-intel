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
| discussion | 1,005 |
| mechanical | 289 |
| corporate_action | 132 |
| earnings | 101 |

## Signal series

16 buckets total, 8 above the reliability floor (>=5 tweets and >=3 carrying a scoreable term).

![Signal series](figures/signal_series.png)

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

![Bullish share](figures/bull_share.png)

## Validation against NIFTY 50

| Horizon | n | Pearson r | p | Spearman r | p | Hit rate |
|---|---:|---:|---:|---:|---:|---|
| 15m | 6 | -0.000 | 1.000 | -0.232 | 0.658 | 66.7% [30%, 90%] |
| 30m | 5 | -0.450 | 0.447 | +0.051 | 0.935 | 60.0% [23%, 88%] |

**Interpretation.** No correlation reaches significance at the 5% level. Sample sizes here are very small -- a handful of buckets overlap NSE trading hours, because collection covered only part of one session -- so this analysis can detect a strong relationship but cannot rule out a modest one. Point estimates at these sample sizes are not evidence in either direction, and no lag or horizon search was performed, since that would manufacture significance without predictive content. Establishing whether the signal carries information requires collection across multiple sessions.

## Corpus characteristics

![Engagement distribution](figures/engagement_distribution.png)

Engagement is power-law distributed, which is why the aggregation layer weights by `log1p` of the interaction composite rather than raw counts: on a linear scale a single viral tweet would outweigh several hundred ordinary ones.

## Downsampling

![LTTB demonstration](figures/lttb_demo.png)

Largest-Triangle-Three-Buckets reduces 100,000 points to 500 (99.5% reduction) while retaining an isolated spike that every-200th sampling discards at the same point budget.

## Top TF-IDF terms

| Term | Mean weight |
|---|---:|
| `nifty` | 0.0535 |
| `cas` | 0.0253 |
| `nse` | 0.0206 |
| `sensex` | 0.0201 |
| `expiry` | 0.0188 |
| `market` | 0.0168 |
| `nifty50` | 0.0166 |
| `trading` | 0.0160 |
| `today` | 0.0157 |
| `closing` | 0.0146 |
| `price` | 0.0139 |
| `banknifty` | 0.0138 |
| `sebi` | 0.0130 |
| `15` | 0.0122 |
| `hai` | 0.0121 |
