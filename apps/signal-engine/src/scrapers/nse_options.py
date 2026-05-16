"""NSE options chain scraper — fetch Put-Call Ratio (PCR) for a given symbol.

PCR < 0.7  → market positioned for rise (bullish signal, +6 pts)
PCR > 1.3  → market positioned for fall (bearish signal, −6 pts)
PCR 0.7–1.3 → neutral positioning

Integrated into technical_agent._execute() after indicator computation.
Falls back gracefully on any network failure — PCR simply won't appear in technical_data.
"""

import logging
from typing import Any

import httpx

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
_TIMEOUT = httpx.Timeout(connect=8.0, read=15.0, write=5.0, pool=5.0)


async def fetch_pcr(symbol: str) -> dict[str, Any]:
    """Return {pcr, put_oi, call_oi} for the given NSE symbol, or {} on failure.

    symbol: NSE symbol with or without .NS suffix (e.g. "SUNPHARMA" or "SUNPHARMA.NS")
    """
    nse_sym = symbol.replace(".NS", "").replace(".BO", "").upper()

    async def _do() -> dict[str, Any]:
        async with httpx.AsyncClient(
            headers=_NSE_HEADERS,
            timeout=_TIMEOUT,
            follow_redirects=True,
        ) as client:
            # NSE requires a session cookie from the homepage before API calls
            await client.get("https://www.nseindia.com/")
            resp = await client.get(
                "https://www.nseindia.com/api/option-chain-equities",
                params={"symbol": nse_sym},
            )
            resp.raise_for_status()
            data = resp.json()

        records = data.get("records", {}).get("data", [])
        total_put_oi  = sum(r.get("PE", {}).get("openInterest", 0) for r in records if "PE" in r)
        total_call_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in records if "CE" in r)

        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else None
        logger.info("pcr_fetched symbol=%s pcr=%s put_oi=%d call_oi=%d",
                    nse_sym, pcr, total_put_oi, total_call_oi)
        return {"pcr": pcr, "put_oi": total_put_oi, "call_oi": total_call_oi}

    result = await with_retry(_do, max_attempts=2, base_delay=1.5, label=f"nse:pcr:{nse_sym}")
    if result is None:
        logger.warning("pcr_fetch_failed symbol=%s — omitting PCR signal", nse_sym)
        return {}
    return result


async def fetch_pcr_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch PCR for multiple symbols concurrently. Returns {symbol: pcr_data}."""
    import asyncio
    tasks = {sym: fetch_pcr(sym) for sym in symbols}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {
        sym: (res if isinstance(res, dict) else {})
        for sym, res in zip(tasks.keys(), results)
    }
