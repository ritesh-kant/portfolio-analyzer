"""sector_agent — map classified news to affected sectors with directional scores.

Uses LLM to aggregate per-article sector tags into a ranked sector list
with bullish/bearish/neutral direction and a 0–100 confidence score.

Falls back to a vote-counting heuristic when LLM is unavailable.
"""

import logging
from collections import Counter, defaultdict
from typing import Any

from .base import BaseAgent
from ..state import TradingState
from ...providers.llm_utils import call_llm_json
from ...scrapers.sector_stocks import normalise_sector, SECTOR_STOCKS

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a sector analyst for Indian stock markets.
Given today's classified news, identify the most impacted sectors.
Output ONLY a valid JSON array — no prose, no markdown.
"""

_USER_TMPL = """\
Based on these market news items from today, identify up to 5 most significantly
impacted sectors. Consider direction (bullish/bearish/neutral) and confidence.

Market-relevant news:
{news_lines}

Valid sectors: {sector_list}

Return a JSON array of objects:
  name        (exact sector name from the valid list)
  direction   ("bullish" | "bearish" | "neutral")
  score       (integer 0-100, confidence in direction)
  reasoning   (one sentence ≤20 words)
  news_count  (how many news items support this)

Order by score descending. Output JSON array only.
"""


def _heuristic_sectors(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback: vote-count sectors from article tags."""
    sector_sentiment: dict[str, list[str]] = defaultdict(list)
    for article in articles:
        raw_sectors = article.get("affected_sectors") or []
        sentiment = article.get("sentiment", "neutral")
        for raw in raw_sectors:
            canon = normalise_sector(raw)
            if canon:
                sector_sentiment[canon].append(sentiment)

    results: list[dict[str, Any]] = []
    for sector, sentiments in sector_sentiment.items():
        counts = Counter(sentiments)
        total = len(sentiments)
        if counts["positive"] > counts["negative"]:
            direction = "bullish"
            score = int(counts["positive"] / total * 100)
        elif counts["negative"] > counts["positive"]:
            direction = "bearish"
            score = int(counts["negative"] / total * 100)
        else:
            direction = "neutral"
            score = 50
        results.append(
            {
                "name": sector,
                "direction": direction,
                "score": score,
                "reasoning": "Heuristic vote count",
                "news_count": total,
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:5]


def _normalise_llm_sectors(raw: list[Any]) -> list[dict[str, Any]]:
    """Normalise LLM sector output — fix sector names and filter unknowns."""
    results: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = normalise_sector(item.get("name", ""))
        if not name:
            continue
        direction = item.get("direction", "neutral")
        if direction not in ("bullish", "bearish", "neutral"):
            direction = "neutral"
        results.append(
            {
                "name": name,
                "direction": direction,
                "score": max(0, min(100, int(item.get("score", 50)))),
                "reasoning": item.get("reasoning", "")[:200],
                "news_count": int(item.get("news_count", 0)),
            }
        )
    return results


class SectorAgent(BaseAgent):
    name = "sector_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        articles = state.classified_news
        if not articles:
            logger.info("sector_agent no classified news — skipping")
            return state

        if state.llm is None:
            sectors = _heuristic_sectors(articles)
        else:
            news_lines = "\n".join(
                f"- [{a.get('sentiment','neutral')}][{','.join(a.get('affected_sectors') or [])}] "
                f"{a.get('summary') or a['headline']}"
                for a in articles[:50]  # cap to avoid context overflow
            )
            sector_list = ", ".join(sorted(SECTOR_STOCKS.keys()))
            prompt = _USER_TMPL.format(news_lines=news_lines, sector_list=sector_list)
            raw = await call_llm_json(state.llm, _SYSTEM_PROMPT, prompt)
            if isinstance(raw, list):
                sectors = _normalise_llm_sectors(raw)
            else:
                sectors = _heuristic_sectors(articles)

        logger.info(
            "sector_agent sectors=%s",
            [(s["name"], s["direction"], s["score"]) for s in sectors],
        )
        return state.model_copy(update={"sectors": sectors})


sector_agent = SectorAgent()
