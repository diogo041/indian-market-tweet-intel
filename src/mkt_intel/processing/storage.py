"""Parquet persistence with a stable, columnar-friendly schema.

Partitioned by UTC date and hour so downstream analysis can push time
predicates into the scan and read only the relevant files. This matters for
the signal layer, which repeatedly reads narrow time windows (single market
sessions, 15-minute buckets) out of a corpus spanning a full day.

ZSTD is used over Snappy because tweet text compresses well -- roughly 4-5x
on this corpus -- and the extra CPU cost is irrelevant next to the network
time spent collecting the data in the first place.

Low-cardinality string columns (`lang`) are dictionary-encoded, which
collapses thousands of repeated "en"/"hi"/"und" values to small integer
codes plus a single dictionary per row group.

Writes are additive. Each call supplies a distinct `batch_id` so streamed
batches land in separate files within the same partition rather than
overwriting one another -- the processing script flushes many batches into
the same date/hour partitions.
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

SCHEMA = pa.schema([
    ("tweet_id", pa.int64()),
    ("created_at", pa.timestamp("us", tz="UTC")),
    ("username", pa.string()),
    ("user_id", pa.int64()),
    ("followers", pa.int32()),
    ("content", pa.string()),
    ("lang", pa.dictionary(pa.int8(), pa.string())),
    ("reply_count", pa.int32()),
    ("retweet_count", pa.int32()),
    ("like_count", pa.int32()),
    ("quote_count", pa.int32()),
    ("view_count", pa.int64()),
    ("hashtags", pa.list_(pa.string())),
    ("cashtags", pa.list_(pa.string())),
    ("mentions", pa.list_(pa.string())),
    ("is_retweet", pa.bool_()),
    ("is_reply", pa.bool_()),
    ("is_quote", pa.bool_()),
    ("verified", pa.bool_()),
    ("conversation_id", pa.int64()),
])


def write(records: list[dict], out_dir: Path, batch_id: int = 0) -> int:
    """Append cleaned records to a date/hour-partitioned Parquet dataset.

    Args:
        records: Cleaned dicts matching SCHEMA, as produced by `clean.clean`.
        out_dir: Dataset root; partition directories are created beneath it.
        batch_id: Distinguishes files written by successive batches so that
            later flushes do not clobber earlier ones in shared partitions.

    Returns:
        Number of rows written.
    """
    if not records:
        return 0

    table = pa.Table.from_pylist(records, schema=SCHEMA)
    table = table.append_column(
        "date", pc.strftime(table["created_at"], "%Y-%m-%d")
    ).append_column(
        "hour", pc.strftime(table["created_at"], "%H")
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    pq.write_to_dataset(
        table,
        root_path=str(out_dir),
        partition_cols=["date", "hour"],
        compression="zstd",
        existing_data_behavior="overwrite_or_ignore",
        basename_template=f"part-{batch_id}-{{i}}.parquet",
    )
    return table.num_rows