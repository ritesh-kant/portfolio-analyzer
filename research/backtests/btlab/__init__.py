"""Backtest lab: one frozen base trade, plug-and-play indicators, every result recorded.

The base trade is `candlestick pattern + support/resistance` on momentum stocks
(see base.py). Indicators are checkboxes: entry indicators filter which base
candidates are taken, exit indicators change how a taken trade is managed.
Every Apply is saved with the exact indicators and parameters that produced it.

Manual: research/backtests/README-lab.md
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_ENGINE = str(REPO / "apps" / "signal-engine")
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

# Bump when anything that changes a candidate or a simulated trade changes
# (base rule, fill model, exit semantics, feature definitions). It is part of
# every cache key and every saved run, so an old result is never mistaken for a
# new one.
LAB_VERSION = "lab-1"
