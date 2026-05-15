"""news_agent — fetch, deduplicate, and LLM-classify Indian market news.

Sources:
  - 5 RSS feeds (ET, Moneycontrol, LiveMint, Business Standard, BusinessLine)
  - NSE corporate announcements
  - BSE corporate announcements

Pipeline:
  1. Fetch all sources concurrently (graceful per-source failures)
  2. Deduplicate by topic_hash (check MongoDB 24 h window)
  3. LLM classify new articles in batches of 15
  4. Persist market-relevant articles to MongoDB
  5. Populate state.raw_news (all) and state.classified_news (relevant only)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .base import BaseAgent
from ..state import TradingState
from ...db.client import get_db
from ...db.repositories.news_articles import NewsArticlesRepository
from ...providers.llm_utils import call_llm_json
from ...scrapers.rss import fetch_all_rss
from ...scrapers.nse import fetch_nse_announcements
from ...scrapers.bse import fetch_bse_announcements

logger = logging.getLogger(__name__)

_CLASSIFY_BATCH = 15

_SYSTEM_PROMPT = """\
You are a financial news analyst for Indian stock markets (NSE/BSE).
Classify market relevance and sentiment for each news article.
Output ONLY a valid JSON array — no prose, no markdown.
"""

_USER_TMPL = """\
Classify each headline below. Return a JSON array of objects with these fields:
  idx          (integer, 0-based index)
  is_market_relevant  (boolean — true if directly impacts listed Indian stocks/sectors)
  sentiment    ("positive" | "negative" | "neutral")
  affected_sectors  (array of sector strings from: IT, Banking, Pharma, Auto, FMCG,
                     Energy, Metals, Realty, Infrastructure, Finance, Chemicals,
                     Consumer, Telecom, Healthcare)
  affected_stocks   (array of NSE symbols with .NS suffix, e.g. ["TCS.NS","INFY.NS"])
  significance ("high" | "medium" | "low")
  summary      (one sentence ≤15 words)

Headlines:
{headlines}

Output JSON array only.
"""


async def _classify_batch(
    llm: Any,
    articles: list[dict[str, Any]],
    offset: int,
) -> list[dict[str, Any]]:
    lines = "\n".join(
        f"{i}. [{a['source']}] {a['headline']}"
        for i, a in enumerate(articles)
    )
    prompt = _USER_TMPL.format(headlines=lines)
    result = await call_llm_json(llm, _SYSTEM_PROMPT, prompt)
    if not isinstance(result, list):
        logger.warning("classify_batch got non-list result, skipping batch offset=%d", offset)
        return []
    return result


def _apply_classification(article: dict[str, Any], cls: dict[str, Any]) -> dict[str, Any]:
    return {
        **article,
        "is_market_relevant": bool(cls.get("is_market_relevant", False)),
        "sentiment": cls.get("sentiment", "neutral"),
        "affected_sectors": cls.get("affected_sectors") or [],
        "affected_stocks": cls.get("affected_stocks") or [],
        "significance": cls.get("significance", "low"),
        "summary": cls.get("summary", article["headline"][:80]),
    }


class NewsAgent(BaseAgent):
    name = "news_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        # 1. Fetch all sources concurrently
        rss_articles, nse_articles, bse_articles = await asyncio.gather(
            fetch_all_rss(),
            fetch_nse_announcements(),
            fetch_bse_announcements(),
        )
        raw_all = rss_articles + nse_articles + bse_articles
        logger.info("news_fetched total=%d", len(raw_all))

        # 2. Deduplicate via MongoDB 24 h hash check
        repo = NewsArticlesRepository(get_db())
        seen_hashes: set[str] = set()
        new_articles: list[dict[str, Any]] = []
        for article in raw_all:
            h = article["topic_hash"]
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            if not await repo.exists_by_hash(h):
                new_articles.append(article)

        logger.info("news_new_after_dedup count=%d", len(new_articles))

        # 3. LLM classification
        classified: list[dict[str, Any]] = []
        if state.llm is not None and new_articles:
            for offset in range(0, len(new_articles), _CLASSIFY_BATCH):
                batch = new_articles[offset : offset + _CLASSIFY_BATCH]
                cls_results = await _classify_batch(state.llm, batch, offset)
                cls_map = {r.get("idx"): r for r in cls_results if isinstance(r, dict)}
                for i, article in enumerate(batch):
                    cls = cls_map.get(i, {})
                    classified.append(_apply_classification(article, cls))
        else:
            # No LLM — pass all as neutral/relevant (stubs always succeed)
            for article in new_articles:
                classified.append(
                    {
                        **article,
                        "is_market_relevant": True,
                        "sentiment": "neutral",
                        "affected_sectors": [],
                        "affected_stocks": [],
                        "significance": "low",
                        "summary": article["headline"][:80],
                    }
                )

        # 4. Persist market-relevant articles
        market_relevant = [a for a in classified if a.get("is_market_relevant")]
        if market_relevant:
            await repo.bulk_insert(market_relevant)
        logger.info("news_saved count=%d", len(market_relevant))

        # 5. Update state
        return state.model_copy(
            update={
                "raw_news": raw_all,
                "classified_news": market_relevant,
            }
        )


news_agent = NewsAgent()
