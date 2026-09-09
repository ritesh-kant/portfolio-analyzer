"""Universe filter (spec §1 criterion 4, 5 and the band rule).

Static per-name facts come from `research/data/mt_universe.csv`, refreshed
quarterly from the Trendlyne StratQ export + the NSE price-band file:

    symbol,free_float_mcap_cr,promoter_pct,band_pct,series,surveillance

`surveillance` is blank or one of ASM/GSM/ESM. If the file is missing the
scanner still runs on NIFTY 500 with the float filters *skipped* and every row
flagged `float_filter_applied=0`, so the forward log can tell the two regimes
apart. Turnover and price come from daily bars at startup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.news_trader.nifty500 import NIFTY_500

logger = logging.getLogger(__name__)

PRICE_MIN, PRICE_MAX = 60.0, 2000.0
FF_MCAP_MIN_CR, FF_MCAP_MAX_CR = 500.0, 5000.0
PROMOTER_MIN_PCT = 50.0
TURNOVER_MIN_CR, TURNOVER_MAX_CR = 3.0, 50.0
ALLOWED_BANDS = (10.0, 20.0, 0.0)   # 0 = no band (F&O names)


@dataclass(frozen=True)
class NameFacts:
    symbol: str
    free_float_mcap_cr: float | None
    promoter_pct: float | None
    band_pct: float | None
    series: str
    surveillance: str


def load_facts(path: Path | None) -> dict[str, NameFacts]:
    if path is None or not path.exists():
        logger.warning("universe file %s missing — float/band filters SKIPPED", path)
        return {}
    df = pd.read_csv(path)
    out: dict[str, NameFacts] = {}

    def _f(row: pd.Series, col: str) -> float | None:
        v = row.get(col)
        return None if v is None or pd.isna(v) else float(v)

    for _, r in df.iterrows():
        out[str(r["symbol"])] = NameFacts(
            symbol=str(r["symbol"]), free_float_mcap_cr=_f(r, "free_float_mcap_cr"),
            promoter_pct=_f(r, "promoter_pct"), band_pct=_f(r, "band_pct"),
            series=str(r.get("series", "EQ") or "EQ"),
            surveillance=str(r.get("surveillance", "") or "").strip(),
        )
    return out


def passes_static(facts: NameFacts | None) -> tuple[bool, str]:
    """(ok, reason). No facts → ok with reason 'no_facts' (filter not applied)."""
    if facts is None:
        return True, "no_facts"
    if facts.series not in ("EQ", "BE") or facts.series == "BE":
        return False, "series"          # BE = trade-to-trade
    if facts.surveillance:
        return False, f"surveillance:{facts.surveillance}"
    if facts.band_pct is not None and facts.band_pct not in ALLOWED_BANDS:
        return False, f"band:{facts.band_pct:g}"
    if facts.free_float_mcap_cr is not None and not (
        FF_MCAP_MIN_CR <= facts.free_float_mcap_cr <= FF_MCAP_MAX_CR
    ):
        return False, "ff_mcap"
    if facts.promoter_pct is not None and facts.promoter_pct < PROMOTER_MIN_PCT:
        return False, "promoter"
    return True, "ok"


def passes_dynamic(price: float, turnover_20d_cr: float | None) -> tuple[bool, str]:
    if not (PRICE_MIN <= price <= PRICE_MAX):
        return False, "price"
    if turnover_20d_cr is not None and not (TURNOVER_MIN_CR <= turnover_20d_cr <= TURNOVER_MAX_CR):
        return False, "turnover"
    return True, "ok"


def base_symbols(extra_lists: list[Path] | None = None) -> list[str]:
    """NIFTY 500 plus any extra one-symbol-per-line files (Smallcap 250 / Microcap 250)."""
    syms = set(NIFTY_500)
    for p in extra_lists or []:
        if p.exists():
            syms.update(s.strip().upper() for s in p.read_text().splitlines() if s.strip())
    return sorted(syms)
