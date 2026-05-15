"""NSE guard data scrapers: ASM list, GSM list, earnings calendar.

All functions gracefully return empty results on any network failure.
Requires NSE session (cookies from homepage) for authenticated endpoints.
"""

import logging
from datetime import date, timedelta
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
_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=5.0, pool=5.0)

_ASM_URL = "https://www.nseindia.com/api/reportASM"
_GSM_URL = "https://www.nseindia.com/api/reportGSM"
_EVENTS_URL = "https://www.nseindia.com/api/event-calendar"


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


async def _nse_get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> Any:
    """GET with NSE session; returns parsed JSON or None."""
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


async def fetch_asm_gsm_symbols() -> tuple[set[str], set[str]]:
    """Return (asm_symbols, gsm_symbols) as sets of NSE symbol strings."""

    async def _do() -> tuple[set[str], set[str]]:
        asm: set[str] = set()
        gsm: set[str] = set()
        async with httpx.AsyncClient(
            headers=_NSE_HEADERS, timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            await client.get("https://www.nseindia.com/")

            try:
                data = await _nse_get(client, _ASM_URL)
                for section in (data.values() if isinstance(data, dict) else [data]):
                    rows = section.get("data", []) if isinstance(section, dict) else section
                    for row in rows if isinstance(rows, list) else []:
                        if isinstance(row, dict):
                            sym = row.get("symbol") or row.get("Symbol", "")
                            if sym:
                                asm.add(sym.upper())
            except Exception as exc:
                logger.warning("asm_fetch_failed error=%s", exc)

            try:
                data = await _nse_get(client, _GSM_URL)
                for row in (data if isinstance(data, list) else data.get("data", [])):
                    if isinstance(row, dict):
                        sym = row.get("symbol") or row.get("Symbol", "")
                        if sym:
                            gsm.add(sym.upper())
            except Exception as exc:
                logger.warning("gsm_fetch_failed error=%s", exc)

        logger.info("asm_gsm_fetched asm=%d gsm=%d", len(asm), len(gsm))
        return asm, gsm

    result = await with_retry(_do, max_attempts=3, base_delay=2.0, label="nse:asm_gsm")
    if result is None:
        logger.warning("asm_gsm_fetch_failed exhausted retries")
        return set(), set()
    return result


async def fetch_earnings_within_days(
    symbols: list[str], days: int = 5
) -> dict[str, str]:
    """Return {nse_symbol: date_str} for stocks with results within `days` calendar days."""
    set_symbols = {s.replace(".NS", "").upper() for s in symbols}
    today = date.today()
    cutoff = today + timedelta(days=days)

    async def _do() -> dict[str, str]:
        upcoming: dict[str, str] = {}
        async with httpx.AsyncClient(
            headers=_NSE_HEADERS, timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            await client.get("https://www.nseindia.com/")
            data = await _nse_get(client, _EVENTS_URL)

        for row in data if isinstance(data, list) else []:
            if not isinstance(row, dict):
                continue
            sym = (row.get("symbol") or "").upper()
            if sym not in set_symbols:
                continue
            purpose = (row.get("purpose") or "").lower()
            if "result" not in purpose and "earning" not in purpose:
                continue
            ex_date_str = row.get("exDate") or row.get("ex_date") or ""
            ex_date = _parse_date(ex_date_str)
            if ex_date and today <= ex_date <= cutoff:
                upcoming[sym] = ex_date_str

        logger.info("earnings_upcoming count=%d", len(upcoming))
        return upcoming

    result = await with_retry(_do, max_attempts=3, base_delay=2.0, label="nse:earnings")
    if result is None:
        logger.warning("earnings_calendar_fetch_failed exhausted retries")
        return {}
    return result
