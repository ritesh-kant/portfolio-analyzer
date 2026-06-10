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

# `source` identifies the specific feed (one per row). `publisher` identifies
# the parent outlet — multiple feeds can share one publisher (ET has Markets +
# Economy; Moneycontrol and LiveMint each have two sections). The classifier
# counts DISTINCT publishers for corroboration, so two sections of the same
# outlet covering one story don't read as independent confirmation.
RSS_FEEDS: list[dict[str, Any]] = [
    {
        "url": "https://economictimes.indiatimes.com/markets/rss.cms",
        "source": "Economic Times Markets",
        "publisher": "Economic Times",
        "tier": "tier1",
    },
    {
        "url": "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
        "source": "Economic Times Economy",
        "publisher": "Economic Times",
        "tier": "tier1",
    },
    {
        "url": "https://www.moneycontrol.com/rss/latestnews.xml",
        "source": "Moneycontrol",
        "publisher": "Moneycontrol",
        "tier": "tier1",
    },
    {
        "url": "https://www.livemint.com/rss/markets",
        "source": "LiveMint",
        "publisher": "LiveMint",
        "tier": "tier1",
    },
    {
        "url": "https://www.business-standard.com/rss/markets-106.rss",
        "source": "Business Standard",
        "publisher": "Business Standard",
        "tier": "tier1",
        "headers": {"Referer": "https://www.business-standard.com/"},
    },
    {
        "url": "https://www.thehindubusinessline.com/markets/?service=rss",
        "source": "BusinessLine",
        "publisher": "BusinessLine",
        "tier": "tier2",
    },
    {
        "url": "https://feeds.feedburner.com/ndtvprofit-latest",
        "source": "NDTV Business",
        "publisher": "NDTV",
        "tier": "tier2",
    },
    {
        "url": "https://news.google.com/rss/search?q=india+stock+market+NSE&hl=en-IN&gl=IN&ceid=IN:en",
        "source": "Google News India Finance",
        # Aggregator: republishes other outlets, so collapse all Google items to
        # one publisher rather than letting them inflate corroboration counts.
        "publisher": "Google News",
        "tier": "tier2",
    },
    {
        "url": "https://news.google.com/rss/search?q=global+markets+fed+rbi+rate&hl=en-IN&gl=IN&ceid=IN:en",
        "source": "Google News Global Macro",
        "publisher": "Google News",
        "tier": "tier2",
    },
    # Added 2026-06-10 for cross-source corroboration. ToI is a new publisher;
    # the MC/Mint rows are new sections of existing publishers — they widen
    # coverage but share a publisher, so corroboration counting stays honest.
    {
        "url": "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms",
        "source": "Times of India Business",
        "publisher": "Times of India",
        "tier": "tier2",
    },
    {
        "url": "https://www.moneycontrol.com/rss/buzzingstocks.xml",
        "source": "Moneycontrol Buzzing Stocks",
        "publisher": "Moneycontrol",
        "tier": "tier1",
    },
    {
        "url": "https://www.livemint.com/rss/companies",
        "source": "LiveMint Companies",
        "publisher": "LiveMint",
        "tier": "tier1",
    },
]

_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=5.0, pool=5.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9,en-IN;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
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


def _make_story_hash(headline: str) -> str:
    # Source-independent: collapses cross-source duplicates of the same story
    # at the signal layer. Internal whitespace runs are normalised so minor
    # formatting differences don't produce distinct hashes.
    normalised = " ".join(headline.strip().lower().split())
    return hashlib.sha256(normalised.encode()).hexdigest()[:24]


def _parse_feed_content(
    content: str | bytes, source: str, tier: str, publisher: str | None = None
) -> list[dict[str, Any]]:
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
                # Parent outlet for corroboration counting; falls back to source
                # for any feed that predates the publisher field.
                "publisher": publisher or source,
                "tier": tier,
                "headline": headline,
                "url": url_link,
                "published_at": published_at,
                "raw_text": f"{headline}. {description}"[:1000],
                "topic_hash": _make_hash(headline, source),
                "story_hash": _make_story_hash(headline),
            }
        )
    return articles


async def _fetch_one(
    client: httpx.AsyncClient, feed_cfg: dict[str, str]
) -> tuple[str, list[dict[str, Any]], bool]:
    """Return (source_name, articles, success_bool)."""
    source = feed_cfg["source"]
    publisher = feed_cfg.get("publisher", source)

    extra_headers: dict[str, str] = feed_cfg.get("headers", {})  # type: ignore[assignment]

    async def _do() -> list[dict[str, Any]]:
        resp = await client.get(feed_cfg["url"], headers=extra_headers)
        resp.raise_for_status()
        articles = _parse_feed_content(resp.text, source, feed_cfg["tier"], publisher)
        logger.info("rss_fetched source=%s count=%d", source, len(articles))
        return articles

    result = await with_retry(_do, max_attempts=3, base_delay=1.0, label=f"rss:{source}")
    if result is None:
        logger.warning("rss_fetch_failed source=%s exhausted retries", source)
        return source, [], False
    return source, result, True


async def fetch_all_rss() -> tuple[list[dict[str, Any]], dict[str, bool]]:
    """Fetch all configured RSS feeds concurrently. Never raises.

    Returns (articles, feed_health) where feed_health maps source name → success.
    """
    async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
        results = await asyncio.gather(*[_fetch_one(client, f) for f in RSS_FEEDS])

    articles: list[dict[str, Any]] = []
    feed_health: dict[str, bool] = {}
    for source, arts, ok in results:
        articles.extend(arts)
        feed_health[source] = ok
    return articles, feed_health
