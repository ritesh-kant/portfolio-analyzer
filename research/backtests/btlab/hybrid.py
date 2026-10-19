"""The hybrid trigger: the nine bullish patterns PLUS doji / spinning tops that formed after a down-move.

`base.py` keeps the nine bullish patterns only. The detector (`candles.py`) calls doji and spinning tops
*neutral* and never trades them, but their meaning depends on what came before them. With
`entry_mode="hybrid"` the candidate table also holds those candles, when `prior_trend == "down"` (the five
candles before the formation fell), relabelled as bullish entries. They are entered exactly like a pattern:
a resting buy-stop one tick over the candle's high, dead if price trades under its low first. `candles.py` is
not changed; only the name and direction of the match are rewritten here.

Measured (2022-26, frozen momentum screen, 1-minute candles): gross about 0% per trade, about -86 INR/trade at
real costs, the same as the nine patterns (BT28 / BT29). Runs using it are exploratory.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from src.momentum_trader.candles import completed_pattern_matches  # noqa: E402

INDECISION_PATTERNS = ("indecision_doji", "indecision_spinning_top")
_NEUTRAL_TO_INDECISION = {"doji": "indecision_doji", "bullish_spinning_top": "indecision_spinning_top",
                          "bearish_spinning_top": "indecision_spinning_top"}


def indecision_matches(sig_bars: pd.DataFrame, tf: str) -> list:
    """Doji / spinning tops that completed on the last bar right after a down-move, as long entries.

    Same detector call as the bullish patterns; the neutral match is only relabelled, so its
    `confirmation` (the high) and `invalidation` (the low) are what the buy-stop uses.
    """
    out = []
    for m in completed_pattern_matches(sig_bars, tf):
        name = _NEUTRAL_TO_INDECISION.get(m.name)
        if name is not None and m.direction == "neutral" and m.prior_trend == "down":
            out.append(replace(m, name=name, direction="bullish"))
    return out


def pool(cands: pd.DataFrame, patterns: list[str]) -> pd.DataFrame:
    """The chosen bullish patterns, plus every indecision candidate."""
    return cands[cands["pattern"].isin([*patterns, *INDECISION_PATTERNS])]
