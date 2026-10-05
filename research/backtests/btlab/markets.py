"""Everything that differs between the NSE and the US tape, in one place.

The lab's engine (patterns, levels, indicators, the exit replay) is market-neutral.
What is not: the clock, the tick, the price band the screen applies, the cost model
and the currency. Minutes below are minutes of the exchange's own clock (IST for NSE,
New York for US), because every bar index is read in its local time.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import REPO  # noqa: F401  (puts apps/signal-engine on sys.path)


@dataclass(frozen=True)
class Market:
    id: str
    label: str
    currency: str               # CSV `currency` column; "USD" makes bt32 draw the US report
    glyph: str                  # ₹ / $
    tick: float
    open_min: int
    eod_min: int                # the bar STARTING here is the last one; exit at its close
    cutoff_min: int             # default: no new entry from here
    day_chg: tuple[float, float]
    risk: float                 # default risk per trade, in the currency
    max_notional: float
    liq_unit: float             # divisor that turns 20-day average traded value into liq_label
    liq_label: str
    universe_text: str

    def passes(self, price: float, liq: float | None) -> tuple[bool, str]:
        if self.id == "NSE":
            from src.momentum_trader import universe
            return universe.passes_dynamic(price, liq)
        if not (US_PRICE[0] <= price <= US_PRICE[1]):
            return False, "price"
        if liq is not None and liq < US_LIQ_MIN_M:
            return False, "liquidity"
        return True, "ok"

    def costs(self, entry: float, exit_px: float, qty: int) -> float:
        """Round-trip charges in the market's currency, from the engine's own models."""
        if self.id == "US":
            from src.momentum_trader import us_costs
            return float(us_costs.calc_costs(entry, qty).total)     # one price, as bt32 and the live ledger
        from src.news_trader.trailing_sl import calc_costs
        return float(calc_costs(entry, exit_px, qty, direction="long")["total"])


# The Warrior guide's $1-20 band (us_universe.py). No float / news data exists for history,
# so those two screens are not applied; the floor on 20-day average dollar volume only keeps
# names whose 1-minute bars are not mostly empty.
US_PRICE = (1.0, 20.0)
US_LIQ_MIN_M = 0.3              # $ millions of average daily traded value

NSE = Market("NSE", "NSE (India)", "INR", "₹", 0.05, 9 * 60 + 15, 15 * 60 + 14, 14 * 60 + 30,
             (4.0, 8.0), 500.0, 50_000.0, 1e7, "cr",
             "price ₹60–2,000, 20-day turnover ₹3–50 cr")
US = Market("US", "US (NYSE/Nasdaq)", "USD", "$", 0.01, 9 * 60 + 30, 15 * 60 + 59, 15 * 60,
            (10.0, 50.0), 50.0, 5_000.0, 1e6, "M",
            f"price ${US_PRICE[0]:g}–{US_PRICE[1]:g}, 20-day average traded value ≥ ${US_LIQ_MIN_M:g}M")

MARKETS = {m.id: m for m in (NSE, US)}


def get(market: str) -> Market:
    try:
        return MARKETS[market]
    except KeyError:
        raise ValueError(f"unknown market {market!r}; expected one of {sorted(MARKETS)}") from None
