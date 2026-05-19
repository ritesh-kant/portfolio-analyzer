"""Point-in-time loader for the L1 data lake.

To be implemented in Month 1 (data ingest block). This module owns:
    - Reading raw source feeds (NSE Bhavcopy, NSE/BSE filings, FII/DII,
      F&O OI snapshots, MFAPI Nifty NAV)
    - Writing them to parquet partitions keyed by (symbol, business_date)
      with an explicit as_of_timestamp column
    - Exposing a load(symbol, start, end, snapshot_at=None) API that
      returns only rows where as_of_timestamp <= snapshot_at

The single most important rule: nothing downstream is allowed to read
a column unless it was knowable at the inference date. See
~/.claude/plans/based-on-the-full-harmonic-gosling.md §4.3.

Reuses:
    apps/signal-engine/backtest/data_loader.py — existing caching layer;
    will be refactored to write PIT-shaped parquet here.
"""

from __future__ import annotations


def load(symbol: str, start: str, end: str, snapshot_at: str | None = None):
    raise NotImplementedError("pit_loader.load — to be implemented in Month 1 data ingest")
