"""NSE corporate announcements scraper.

NSE's API requires a browser session (cookies from the homepage).
Gracefully returns [] on any network or parse failure.

Timestamps come back as IST wall-clock strings (`sort_date` "2026-09-24 18:22:40",
`an_dt` "24-Sep-2026 18:22:40") and are returned as UTC-aware datetimes. The
filing's own summary is in `attchmntText` and the PDF/zip link in `attchmntFile`.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ._retry import with_retry

logger = logging.getLogger(__name__)

_NSE_HOME = "https://www.nseindia.com/"
_NSE_ANN_URL = "https://www.nseindia.com/api/corporate-announcements"
_PARAMS = {"index": "equities"}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}
_TIMEOUT = 20
_IST = timezone(timedelta(hours=5, minutes=30))
_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y")


def _parse_dt(raw: str | None) -> datetime | None:
    """IST wall-clock string → UTC-aware datetime."""
    if not raw:
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=_IST).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _make_hash(symbol: str, subject: str) -> str:
    return hashlib.sha256(f"{symbol.strip().upper()}|{subject.strip().lower()}".encode()).hexdigest()[:24]


def _normalise(data: Any) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        symbol = item.get("symbol", "")
        subject = item.get("subject") or item.get("desc", "")
        body = item.get("attchmntText") or item.get("body", "")
        if not subject:
            continue
        headline = f"{symbol}: {subject}" if symbol else subject
        published_at = _parse_dt(item.get("sort_date") or item.get("an_dt") or item.get("exchdisstime"))
        topic_hash = _make_hash(symbol, subject)
        articles.append(
            {
                "source": "NSE Announcements",
                "tier": "tier1",
                "headline": headline,
                "url": item.get("attchmntFile") or None,
                "published_at": published_at,
                "raw_text": f"{headline}. {body}"[:1000],
                "topic_hash": topic_hash,
                # NSE announcements are canonical (single official source),
                # so the per-source dedup key is also the cross-source one.
                "story_hash": topic_hash,
                "nse_symbol": symbol,
            }
        )
    return articles


async def _get(params: dict[str, str]) -> Any:
    async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
        await client.get(_NSE_HOME)
        await asyncio.sleep(0.5)
        resp = await client.get(_NSE_ANN_URL, params=params)
        resp.raise_for_status()
        return resp.json()


async def fetch_nse_announcements() -> list[dict[str, Any]]:
    """Return the latest NSE corporate announcements (exchange-wide, ~20 rows)."""

    async def _do() -> list[dict[str, Any]]:
        articles = _normalise(await _get(_PARAMS))
        logger.info("nse_fetched count=%d", len(articles))
        return articles

    result = await with_retry(_do, max_attempts=3, base_delay=2.0, label="nse:announcements")
    if result is None:
        logger.warning("nse_fetch_failed exhausted retries")
        return []
    return result


async def fetch_nse_symbol_announcements(symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Every announcement one symbol filed between the IST dates of `start` and `end`.

    The exchange-wide feed only carries the latest ~20 filings, so a lookup for
    one name has to ask for that name. NSE filters by whole days; callers trim
    to the exact window.
    """
    params = {
        **_PARAMS,
        "symbol": symbol.upper(),
        "from_date": start.astimezone(_IST).strftime("%d-%m-%Y"),
        "to_date": end.astimezone(_IST).strftime("%d-%m-%Y"),
    }

    async def _do() -> list[dict[str, Any]]:
        articles = _normalise(await _get(params))
        logger.info("nse_symbol_fetched symbol=%s count=%d", symbol, len(articles))
        return articles

    result = await with_retry(_do, max_attempts=3, base_delay=2.0, label=f"nse:announcements:{symbol}")
    if result is None:
        logger.warning("nse_symbol_fetch_failed symbol=%s exhausted retries", symbol)
        return []
    return result
