"""NSE corporate announcements scraper.

NSE's API requires a browser session (cookies from the homepage).
Gracefully returns [] on any network or parse failure.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
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


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _make_hash(symbol: str, subject: str) -> str:
    return hashlib.sha256(f"{symbol.strip().upper()}|{subject.strip().lower()}".encode()).hexdigest()[:24]


async def fetch_nse_announcements() -> list[dict[str, Any]]:
    """Return today's NSE corporate announcements as normalised article dicts."""

    async def _do() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
            await client.get(_NSE_HOME)
            await asyncio.sleep(0.5)
            resp = await client.get(_NSE_ANN_URL, params=_PARAMS)
            resp.raise_for_status()
            data = resp.json()

        articles: list[dict[str, Any]] = []
        for item in data if isinstance(data, list) else []:
            symbol = item.get("symbol", "")
            subject = item.get("subject") or item.get("desc", "")
            body = item.get("body", "")
            if not subject:
                continue
            headline = f"{symbol}: {subject}" if symbol else subject
            published_at = _parse_dt(item.get("sort_date") or item.get("bcastDate"))
            topic_hash = _make_hash(symbol, subject)
            articles.append(
                {
                    "source": "NSE Announcements",
                    "tier": "tier1",
                    "headline": headline,
                    "url": None,
                    "published_at": published_at,
                    "raw_text": f"{headline}. {body}"[:1000],
                    "topic_hash": topic_hash,
                    # NSE announcements are canonical (single official source),
                    # so the per-source dedup key is also the cross-source one.
                    "story_hash": topic_hash,
                    "nse_symbol": symbol,
                }
            )

        logger.info("nse_fetched count=%d", len(articles))
        return articles

    result = await with_retry(_do, max_attempts=3, base_delay=2.0, label="nse:announcements")
    if result is None:
        logger.warning("nse_fetch_failed exhausted retries")
        return []
    return result
