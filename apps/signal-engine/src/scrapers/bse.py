"""BSE corporate announcements scraper.

BSE's API is public and doesn't require session management.
Gracefully returns [] on any network or parse failure.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ._retry import with_retry

logger = logging.getLogger(__name__)

_BSE_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
_PARAMS = {
    "pageno": "1",
    "strCat": "-1",
    "strPrevDate": "",
    "strScrip": "",
    "strSearch": "P",
    "strToDate": "",
    "strType": "C",
}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}
_TIMEOUT = 20


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _make_hash(news_id: str, scrip_code: str) -> str:
    return hashlib.sha256(f"bse|{news_id}|{scrip_code}".encode()).hexdigest()[:24]


async def fetch_bse_announcements() -> list[dict[str, Any]]:
    """Return today's BSE corporate announcements as normalised article dicts."""

    async def _do() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_BSE_URL, params=_PARAMS)
            resp.raise_for_status()
            payload = resp.json()

        rows = payload.get("Table", []) if isinstance(payload, dict) else []
        articles: list[dict[str, Any]] = []

        for item in rows:
            news_id = str(item.get("NEWSID", ""))
            headline = (item.get("HEADLINE") or item.get("NEWSSUB", "")).strip()
            scrip_code = str(item.get("SCRIP_CD", ""))
            scrip_name = (item.get("SLONGNAME") or item.get("SNAME", "")).strip()
            category = item.get("CATEGORYNAME", "")
            if not headline:
                continue
            full_headline = f"{scrip_name}: {headline}" if scrip_name else headline
            published_at = _parse_dt(item.get("DissemDT") or item.get("ANNOUNCEMENTDATE"))
            articles.append(
                {
                    "source": "BSE Announcements",
                    "tier": "tier1",
                    "headline": full_headline,
                    "url": None,
                    "published_at": published_at,
                    "raw_text": f"{full_headline}. Category: {category}"[:1000],
                    "topic_hash": _make_hash(news_id, scrip_code),
                    "bse_scrip_code": scrip_code,
                    "bse_category": category,
                }
            )

        logger.info("bse_fetched count=%d", len(articles))
        return articles

    result = await with_retry(_do, max_attempts=3, base_delay=1.5, label="bse:announcements")
    if result is None:
        logger.warning("bse_fetch_failed exhausted retries")
        return []
    return result
