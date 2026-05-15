"""RSS feed scraper — fetches and normalises Indian financial news feeds.

Uses httpx for async HTTP (cancellable, timeout-safe), then feedparser
to parse the XML response body in-process (no blocking I/O).
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from ._retry import with_retry

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    {
        "url": "https://economictimes.indiatimes.com/markets/rss.cms",
        "source": "Economic Times",
        "tier": "tier1",
    },
    {
        "url": "https://www.moneycontrol.com/rss/latestnews.xml",
        "source": "Moneycontrol",
        "tier": "tier1",
    },
    {
        "url": "https://www.livemint.com/rss/markets",
        "source": "LiveMint",
        "tier": "tier1",
    },
    {
        "url": "https://www.business-standard.com/rss/markets-106.rss",
        "source": "Business Standard",
        "tier": "tier1",
    },
    {
        "url": "https://www.thehindubusinessline.com/markets/?service=rss",
        "source": "BusinessLine",
        "tier": "tier2",
    },
]

_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=5.0, pool=5.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def _parse_dt(entry: Any) -> datetime | None:
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass
    return None


def _make_hash(headline: str, source: str) -> str:
    return hashlib.sha256(f"{headline.strip().lower()}|{source}".encode()).hexdigest()[:24]


def _parse_feed_content(content: str | bytes, source: str, tier: str) -> list[dict[str, Any]]:
    """Parse RSS/Atom XML string with feedparser — purely in-memory, no I/O."""
    feed = feedparser.parse(content)
    articles: list[dict[str, Any]] = []
    for entry in feed.entries:
        headline = (getattr(entry, "title", "") or "").strip()
        if not headline:
            continue
        description = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        ).strip()
        url_link = getattr(entry, "link", None)
        published_at = _parse_dt(entry)
        articles.append(
            {
                "source": source,
                "tier": tier,
                "headline": headline,
                "url": url_link,
                "published_at": published_at,
                "raw_text": f"{headline}. {description}"[:1000],
                "topic_hash": _make_hash(headline, source),
            }
        )
    return articles


async def _fetch_one(
    client: httpx.AsyncClient, feed_cfg: dict[str, str]
) -> list[dict[str, Any]]:
    source = feed_cfg["source"]

    async def _do() -> list[dict[str, Any]]:
        resp = await client.get(feed_cfg["url"])
        resp.raise_for_status()
        articles = _parse_feed_content(resp.text, source, feed_cfg["tier"])
        logger.info("rss_fetched source=%s count=%d", source, len(articles))
        return articles

    result = await with_retry(_do, max_attempts=3, base_delay=1.0, label=f"rss:{source}")
    if result is None:
        logger.warning("rss_fetch_failed source=%s exhausted retries", source)
        return []
    return result


async def fetch_all_rss() -> list[dict[str, Any]]:
    """Fetch all configured RSS feeds concurrently. Never raises."""
    async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
        batches = await asyncio.gather(*[_fetch_one(client, f) for f in RSS_FEEDS])
    results: list[dict[str, Any]] = []
    for batch in batches:
        results.extend(batch)
    return results
