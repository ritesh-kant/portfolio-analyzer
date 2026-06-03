"""Current price fetcher for NSE symbols.

Paper mode: yfinance (free, ~15 min delayed outside market hours).
Live mode (future): replace get_ltp() body with Kite Connect WebSocket tick.
"""

import logging
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def get_ltp(symbol: str) -> float | None:
    """Return last traded price for an NSE symbol. Returns None on failure."""
    # Index symbols (^NSEI, ^NSEBANK, etc.) must NOT get the .NS suffix
    ticker_str = symbol if symbol.startswith("^") else f"{symbol.upper()}.NS"
    try:
        t = yf.Ticker(ticker_str)
        fi: Any = t.fast_info
        price = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        if price and float(price) > 0:
            return float(price)
        # Fallback: 1-day 1-min bar close
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            v = hist["Close"].iloc[-1]
            return float(v.item()) if hasattr(v, "item") else float(v)
        return None
    except Exception as exc:
        logger.warning("price_fetch_failed symbol=%s err=%s", symbol, exc)
        return None


def get_ltps(symbols: list[str]) -> dict[str, float]:
    """Batch price fetch via yf.download. Falls back to per-symbol get_ltp on error."""
    if not symbols:
        return {}
    tickers = [s if s.startswith("^") else f"{s.upper()}.NS" for s in symbols]
    try:
        data = yf.download(tickers, period="1d", interval="1m", progress=False, threads=True)
        if data.empty:
            raise ValueError("empty response")
        result: dict[str, float] = {}
        if len(tickers) > 1:
            closes = data["Close"]
            for sym, ticker in zip(symbols, tickers):
                if ticker in closes.columns:
                    series = closes[ticker].dropna()
                    if not series.empty:
                        v = series.iloc[-1]
                        result[sym] = float(v.item()) if hasattr(v, "item") else float(v)
        else:
            series = data["Close"].dropna()
            if not series.empty:
                v = series.iloc[-1]
                result[symbols[0]] = float(v.item()) if hasattr(v, "item") else float(v)
        return result
    except Exception as exc:
        logger.warning("get_ltps_batch_failed symbols=%s err=%s — falling back to per-symbol", symbols, exc)
        return {s: p for s in symbols if (p := get_ltp(s)) is not None}


def get_volume_data(symbol: str) -> dict[str, int | float | None]:
    """Return current-day volume, 3-month avg daily volume, and their ratio.

    volume_ratio_at_entry > 1.0 means above-average activity — news is being
    traded. Values well below 1.0 suggest the market is ignoring the signal.
    Returns None values on failure — never raises.
    """
    ticker_str = symbol if symbol.startswith("^") else f"{symbol.upper()}.NS"
    result: dict[str, int | float | None] = {
        "current_day_volume": None,
        "avg_daily_volume": None,
        "volume_ratio": None,
    }
    try:
        t = yf.Ticker(ticker_str)
        fi: Any = t.fast_info
        avg_vol = getattr(fi, "three_month_average_volume", None)
        if avg_vol:
            result["avg_daily_volume"] = int(avg_vol)
        hist = t.history(period="1d", interval="1d")
        if not hist.empty and "Volume" in hist.columns:
            current_vol = int(hist["Volume"].iloc[-1])
            result["current_day_volume"] = current_vol
            if avg_vol and float(avg_vol) > 0:
                result["volume_ratio"] = round(current_vol / float(avg_vol), 3)
    except Exception as exc:
        logger.warning("volume_fetch_failed symbol=%s err=%s", symbol, exc)
    return result


# NSE sector index tickers on Yahoo Finance
_SECTOR_TICKERS: dict[str, str] = {
    "Banking": "^NSEBANK",
    "IT": "^CNXIT",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "Energy": "^CNXENERGY",
    "FMCG": "^CNXFMCG",
    "Metal": "^CNXMETAL",
    "Realty": "^CNXREALTY",
    "Media": "^CNXMEDIA",
    "PSU Bank": "^CNXPSUBANK",
}


def get_market_snapshot(sector: str | None = None) -> dict[str, float | None]:
    """Fetch NIFTY 50 and optionally the sector index at the current moment.

    Stored once at trade entry so market regime can be reconstructed later.
    Returns None values on fetch failure — never raises.
    """
    snapshot: dict[str, float | None] = {"nifty50": None, "sector_index": None}
    try:
        snapshot["nifty50"] = get_ltp("^NSEI")
    except Exception as exc:
        logger.warning("market_snapshot_nifty_failed err=%s", exc)

    if sector and sector in _SECTOR_TICKERS:
        try:
            t = yf.Ticker(_SECTOR_TICKERS[sector])
            fi: Any = t.fast_info
            price = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
            snapshot["sector_index"] = float(price) if price else None
        except Exception as exc:
            logger.warning("market_snapshot_sector_failed sector=%s err=%s", sector, exc)

    return snapshot
