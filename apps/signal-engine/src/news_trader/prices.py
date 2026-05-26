"""Current price fetcher for NSE symbols.

Paper mode: yfinance (free, ~15 min delayed outside market hours).
Live mode (future): replace get_ltp() body with Kite Connect WebSocket tick.
"""

import logging
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)


def get_ltp(symbol: str) -> float | None:
    """Return last traded price for an NSE symbol. Returns None on failure."""
    ticker_str = f"{symbol.upper()}.NS"
    try:
        t = yf.Ticker(ticker_str)
        fi: Any = t.fast_info
        price = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        if price and float(price) > 0:
            return float(price)
        # Fallback: 1-day 1-min bar close
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        return None
    except Exception as exc:
        logger.warning("price_fetch_failed symbol=%s err=%s", symbol, exc)
        return None


def get_ltps(symbols: list[str]) -> dict[str, float]:
    """Batch price fetch. Returns only symbols with valid prices."""
    return {s: p for s in symbols if (p := get_ltp(s)) is not None}
