"""Lambda: news-classifier — SQS trigger from news-raw queue.

For each message:
  1. Load article from nt_news_raw
  2. Call Gemini 1.5 Flash for structured classification
  3. Save signal to nt_signals
  4. If confidence is high/medium: enqueue to news-signals queue with
     nt_news_delay_seconds delay so trade_decision fires 15 min later
  5. Mark article as classified
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from bson import ObjectId

from src.config import Settings
from src.db.client import get_db
from src.news_trader.classifier import classify
from src.news_trader.db import ensure_indexes, news_raw, signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_ACTIONABLE_CONFIDENCE = {"high", "medium"}


async def _process_message(msg: dict[str, Any], settings: Settings, run_id: str | None = None) -> bool:
    """Returns True if a signal was created and enqueued."""
    db = get_db()
    news_id = msg.get("news_id")
    if not news_id:
        logger.warning("[CLASSIFIER] bad message — no news_id: %s", msg)
        return False

    article = await news_raw(db).find_one({"_id": ObjectId(news_id)})
    if not article:
        logger.warning("[CLASSIFIER] article not found news_id=%s", news_id)
        return False

    if article.get("classified"):
        logger.debug("[CLASSIFIER] already classified news_id=%s", news_id)
        return False

    headline = article.get("headline", "")
    logger.info("[CLASSIFIER] processing news_id=%s headline=%.80s", news_id, headline)

    raw_text = article.get("raw_text", headline)
    logger.info("[CLASSIFIER] calling LLM (provider=%s) for news_id=%s...", settings.ai_provider, news_id)
    result = classify(raw_text, settings)

    if result is None:
        logger.warning("[CLASSIFIER] LLM failed for news_id=%s headline=%.80s — marking classified",
                       news_id, headline)
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        return False

    logger.info("[CLASSIFIER] Gemini result news_id=%s signal=%s confidence=%s magnitude=%s stocks=%s sector=%s",
                news_id, result["signal"], result["confidence"], result["magnitude"],
                result["stocks"], result["sector"])

    signal_doc = {
        "news_id": news_id,
        "headline": headline,
        "source": article.get("source", ""),
        "sector": result["sector"],
        "signal": result["signal"],
        "magnitude": result["magnitude"],
        "stocks": result["stocks"],
        "confidence": result["confidence"],
        "reasoning": result["reasoning"],
        "llm_model": result["llm_model"],
        "prompt_version": result["prompt_version"],
        "created_at": datetime.now(tz=timezone.utc),
        "acted_on": False,
    }
    insert_result = await signals(db).insert_one(signal_doc)
    signal_id = str(insert_result.inserted_id)

    await news_raw(db).update_one(
        {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
    )

    # Only actionable signals go to the trade-decision queue
    if result["confidence"] not in _ACTIONABLE_CONFIDENCE:
        logger.info("[CLASSIFIER] signal not actionable confidence=%s — saved but not enqueued",
                    result["confidence"])
        return False
    if result["signal"] == "neutral":
        logger.info("[CLASSIFIER] signal is neutral — saved but not enqueued")
        return False
    if not result["stocks"]:
        logger.info("[CLASSIFIER] no stocks identified — saved but not enqueued")
        return False

    if settings.news_signals_queue_url:
        sqs = boto3.client("sqs")
        enqueue_kwargs: dict[str, Any] = {
            "QueueUrl": settings.news_signals_queue_url,
            "MessageBody": json.dumps({"signal_id": signal_id}),
            "DelaySeconds": settings.nt_news_delay_seconds,  # 15-min wait before entry
        }
        if run_id:
            enqueue_kwargs["MessageAttributes"] = {
                "run_id": {"DataType": "String", "StringValue": run_id}
            }
        sqs.send_message(**enqueue_kwargs)
        logger.info("[CLASSIFIER] enqueued signal_id=%s to trade-decision queue delay=%ds",
                    signal_id, settings.nt_news_delay_seconds)
    else:
        logger.warning("[CLASSIFIER] NEWS_SIGNALS_QUEUE_URL not set — skipping SQS enqueue")

    return True


async def _run(event: dict[str, Any], settings: Settings) -> dict[str, int]:
    db = get_db()
    await ensure_indexes(db)

    records = event.get("Records", [])
    logger.info("[CLASSIFIER] processing %d SQS record(s)", len(records))
    acted = 0
    for record in records:
        try:
            msg = json.loads(record["body"])
            attrs = record.get("messageAttributes", {})
            run_id: str | None = attrs.get("run_id", {}).get("stringValue")
            if await _process_message(msg, settings, run_id=run_id):
                acted += 1
        except Exception as exc:
            logger.error("[CLASSIFIER] record error err=%s record=%.200s", exc, record)

    logger.info("[CLASSIFIER] done — processed=%d actionable=%d", len(records), acted)
    return {"processed": len(records), "acted_on": acted}


def handler(event: dict[str, Any], context: object) -> dict[str, int]:
    settings = Settings()
    return asyncio.run(_run(event, settings))
