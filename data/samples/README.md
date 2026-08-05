# Sample Data

`data/raw/` and `data/processed/` are gitignored, since collected data is
regenerable and would bloat the repository. This directory holds a small
committed sample so the outputs can be inspected without running the
collector.

| File | Contents |
|---|---|
| `tweets_sample.parquet` | 500 cleaned tweets, evenly spaced across the collection window |
| `signals.csv` | Full bucket-level signal series with confidence intervals |
| `summary.json` | Machine-readable corpus and validation summary |
| `results.md` | Rendered analysis report |

To regenerate the full dataset, see the Quick start section of the root
README.
