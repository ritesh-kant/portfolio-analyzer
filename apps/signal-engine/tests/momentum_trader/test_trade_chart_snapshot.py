from __future__ import annotations

import pandas as pd

from src.momentum_trader.ledger import chart_bars_doc


def test_chart_bars_doc_serializes_canonical_one_minute_bars() -> None:
    index = pd.date_range("2026-09-10 09:15", periods=2, freq="min", tz="Asia/Kolkata")
    bars = pd.DataFrame(
        {
            "open": [100, 101],
            "high": [102, 103],
            "low": [99, 100],
            "close": [101, 102],
            "volume": [1200, 1400],
        },
        index=index,
    )

    assert chart_bars_doc(bars) == [
        {
            "time": "2026-09-10T09:15:00+05:30",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 1200.0,
        },
        {
            "time": "2026-09-10T09:16:00+05:30",
            "open": 101.0,
            "high": 103.0,
            "low": 100.0,
            "close": 102.0,
            "volume": 1400.0,
        },
    ]
