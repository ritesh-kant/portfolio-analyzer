"""Descriptive-only news lookup, attached to `mt_positions` for review.

Purely observational: not read by engine.py, not part of the catalyst-gate
hypothesis in catalyst.py (that gate reads the classified `nt_signals`
collection, which depends on the paused news-trader classifier). This module
scrapes RSS and NSE directly and on demand, so it works even while the
news-trader pipeline is paused, and it never influences a trade decision —
only what gets shown next to it afterwards.

NSE is asked for the one symbol over the lookback window: its exchange-wide
feed only carries the latest ~20 filings, so the traded name was almost never
in it. BSE is not queried — its announcement API answers 403 to non-browser
clients (from AWS and from a home connection alike), and the scanner only
trades NSE names.

Runs off the live-trading critical path (see scanner.py's background thread);
any failure here must degrade to an empty result, never raise.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from src.scrapers.nse import fetch_nse_symbol_announcements
from src.scrapers.rss import fetch_all_rss

logger = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=24)
MAX_ITEMS = 10


def _symbol_pattern(symbol: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)


async def _fetch_all(symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Gather RSS + NSE concurrently. One feed failing (network, layout
    change) never blanks out the other."""
    rss_result, nse_result = await asyncio.gather(
        fetch_all_rss(), fetch_nse_symbol_announcements(symbol, start, end),
        return_exceptions=True,
    )
    articles: list[dict[str, Any]] = []
    for label, result in (("rss", rss_result), ("nse", nse_result)):
        if isinstance(result, BaseException):
            logger.warning("news_context: %s fetch failed: %r", label, result)
            continue
        articles.extend(result[0] if label == "rss" else result)
    return articles


def _as_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _matches(article: dict[str, Any], symbol: str, pattern: re.Pattern[str]) -> bool:
    if str(article.get("nse_symbol", "")).upper() == symbol.upper():
        return True
    text = f"{article.get('headline', '')} {article.get('raw_text', '')}"
    return bool(pattern.search(text))


def _body(article: dict[str, Any]) -> str | None:
    """The article/filing text without the headline the scrapers prefix it with."""
    raw = str(article.get("raw_text") or "")
    prefix = f"{article.get('headline', '')}. "
    body = raw[len(prefix):] if raw.startswith(prefix) else raw
    return body.strip() or None


def select_matches(
    articles: list[dict[str, Any]], symbol: str, at: datetime, lookback: timedelta = LOOKBACK
) -> list[dict[str, Any]]:
    """Pure filtering step, split out from `recent_news` so it's testable
    without network access."""
    at_utc = _as_utc(at)
    window_start = at_utc - lookback
    pattern = _symbol_pattern(symbol)

    matches: list[dict[str, Any]] = []
    for article in articles:
        published_at = article.get("published_at")
        if published_at is None:
            continue
        published_at = _as_utc(published_at)
        if not (window_start <= published_at <= at_utc):
            continue
        if not _matches(article, symbol, pattern):
            continue
        matches.append(
            {
                "headline": article.get("headline"),
                "source": article.get("source"),
                "publisher": article.get("publisher", article.get("source")),
                "tier": article.get("tier"),
                "url": article.get("url"),
                "text": _body(article),
                "published_at": published_at.isoformat(),
            }
        )

    matches.sort(key=lambda m: m["published_at"], reverse=True)
    return matches[:MAX_ITEMS]


def recent_news(symbol: str, at: datetime, lookback: timedelta = LOOKBACK) -> list[dict[str, Any]]:
    """Best-effort: headlines mentioning `symbol` published in the window
    before `at`. Returns [] on any failure — never raises, never blocks a trade."""
    try:
        at_utc = _as_utc(at)
        articles = asyncio.run(_fetch_all(symbol, at_utc - lookback, at_utc))
    except Exception:  # noqa: BLE001
        logger.exception("news_context: fetch_all failed")
        return []
    return select_matches(articles, symbol, at, lookback)
