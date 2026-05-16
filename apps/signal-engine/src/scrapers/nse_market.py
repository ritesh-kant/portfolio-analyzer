"""NSE market data scraper: Nifty 50, India VIX (yfinance) + FII/DII net flows (NSE API).

All functions gracefully return empty/None on any network failure.
"""

import logging
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

from ._retry import with_retry

logger = logging.getLogger(__name__)

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=5.0, pool=5.0)


def _strip_commas(val: Any) -> float:
    """Parse NSE string values like '1,23,456.78' or '-2,444.44'."""
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def fetch_nifty_vix_sync() -> dict[str, Any]:
    """Fetch Nifty 50 and India VIX via yfinance. Returns {} on failure.

    Downloads 55 days so we can compute: today's change, 5-day return,
    30-day return, and whether Nifty is above its 50-day EMA (regime context).
    """
    try:
        data = yf.download(
            ["^NSEI", "^INDIAVIX"],
            period="55d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if data.empty:
            return {}

        closes = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data

        nifty = closes.get("^NSEI", pd.Series(dtype=float)).dropna()
        vix = closes.get("^INDIAVIX", pd.Series(dtype=float)).dropna()

        if nifty.shape[0] < 2:
            return {}

        nifty_close = float(nifty.iloc[-1])
        nifty_prev = float(nifty.iloc[-2])
        nifty_change_pct = (nifty_close - nifty_prev) / nifty_prev * 100

        nifty_5d_start = float(nifty.iloc[-5]) if len(nifty) >= 5 else float(nifty.iloc[0])
        nifty_5d_return = (nifty_close - nifty_5d_start) / nifty_5d_start * 100

        nifty_30d_start = float(nifty.iloc[-30]) if len(nifty) >= 30 else float(nifty.iloc[0])
        nifty_30d_return = (nifty_close - nifty_30d_start) / nifty_30d_start * 100

        # 50-day EMA to classify bull/bear regime
        nifty_ema50: float | None = None
        nifty_above_ema50: bool | None = None
        if len(nifty) >= 50:
            nifty_ema50 = float(nifty.ewm(span=50, adjust=False).mean().iloc[-1])
            nifty_above_ema50 = nifty_close > nifty_ema50

        return {
            "nifty_close": round(nifty_close, 2),
            "nifty_prev_close": round(nifty_prev, 2),
            "nifty_change_pct": round(nifty_change_pct, 3),
            "nifty_5d_return": round(nifty_5d_return, 3),
            "nifty_30d_return": round(nifty_30d_return, 3),
            "nifty_ema50": round(nifty_ema50, 2) if nifty_ema50 is not None else None,
            "nifty_above_ema50": nifty_above_ema50,
            "vix": round(float(vix.iloc[-1]), 2) if not vix.empty else None,
        }
    except Exception as exc:
        logger.warning("nifty_vix_fetch_failed error=%s", exc)
        return {}


async def fetch_fii_dii() -> dict[str, Any]:
    """Fetch today's FII/DII net equity flows from NSE. Returns {} on failure."""

    async def _do() -> dict[str, Any]:
        async with httpx.AsyncClient(
            headers=_NSE_HEADERS, timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            await client.get("https://www.nseindia.com/")
            resp = await client.get("https://www.nseindia.com/api/fiidiiTradeReact")
            resp.raise_for_status()
            rows = resp.json()

        fii_net = dii_net = None
        for row in rows if isinstance(rows, list) else []:
            category = str(row.get("category", "")).upper()
            net = _strip_commas(row.get("netValue") or row.get("net_value") or 0)
            if "FII" in category or "FPI" in category:
                fii_net = net
            elif "DII" in category:
                dii_net = net

        logger.info("fii_dii_fetched fii=%s dii=%s", fii_net, dii_net)
        return {"fii_net_crore": fii_net, "dii_net_crore": dii_net}

    result = await with_retry(_do, max_attempts=3, base_delay=2.0, label="nse:fii_dii")
    if result is None:
        logger.warning("fii_dii_fetch_failed exhausted retries")
        return {}
    return result
