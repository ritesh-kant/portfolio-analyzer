"""NSE option chain fetcher — best-effort, fails gracefully.

NSE's API requires browser-like session cookies. Lambda is stateless, so we
refresh cookies on every call (adds ~0.5s). The chain logger tags iv_source as
"nse" on success or "synthetic" on failure, so callers always know what they got.

This should never raise — callers expect None on any failure.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_NSE_BASE = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": _NSE_BASE,
}


def fetch_option_chain(symbol: str, timeout: int = 12) -> dict[str, Any] | None:
    """Fetch NSE option chain for a symbol. Returns parsed JSON or None."""
    try:
        import requests

        s = requests.Session()
        s.headers.update(_HEADERS)
        # Seed cookies with a homepage visit
        s.get(_NSE_BASE, timeout=8)
        url = f"{_NSE_BASE}/api/option-chain-equities?symbol={symbol}"
        resp = s.get(url, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("nse_chain_http_error sym=%s status=%s", symbol, resp.status_code)
            return None
        data = resp.json()
        return data if "records" in data else None
    except Exception as exc:
        logger.debug("nse_chain_fetch_failed sym=%s err=%s", symbol, exc)
        return None


def extract_atm(
    chain_data: dict[str, Any], spot: float, strike_step: float
) -> dict[str, float] | None:
    """Pull ATM row from NSE chain data.

    Returns dict with ce_bid, ce_ask, ce_iv, pe_bid, pe_ask, pe_iv, atm_strike.
    Returns None if the ATM row is missing or malformed.
    """
    try:
        atm = round(spot / strike_step) * strike_step
        rows = chain_data.get("records", {}).get("data", [])
        for row in rows:
            if abs(row.get("strikePrice", 0) - atm) < 0.01:
                ce = row.get("CE", {})
                pe = row.get("PE", {})
                return {
                    "atm_strike": atm,
                    "ce_bid": float(ce.get("bidprice", 0)),
                    "ce_ask": float(ce.get("askPrice", 0)),
                    "ce_iv": float(ce.get("impliedVolatility", 0)) / 100.0,  # NSE gives %
                    "ce_oi": int(ce.get("openInterest", 0)),
                    "pe_bid": float(pe.get("bidprice", 0)),
                    "pe_ask": float(pe.get("askPrice", 0)),
                    "pe_iv": float(pe.get("impliedVolatility", 0)) / 100.0,
                    "pe_oi": int(pe.get("openInterest", 0)),
                }
        return None
    except Exception as exc:
        logger.debug("nse_chain_extract_failed err=%s", exc)
        return None
