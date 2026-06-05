"""Lambda: news-classifier — SQS trigger from news-raw queue.

For each message:
  1. Load article from nt_news_raw
  2. Call Gemini Flash for structured classification
  3. Save signal to nt_signals
  4. If confidence is high/medium: enqueue to news-signals queue with
     nt_news_delay_seconds delay so trade_decision fires 15 min later
  5. Mark article as classified

Error propagation: if the LLM provider call fails (e.g. model deprecated, auth error),
LLMProviderError is raised by classify(). The handler tracks these per run_id and calls
fail_run() when all classification attempts for a run fail, surfacing the error to the
frontend pipeline status panel.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import boto3
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from src.config import Settings
from src.db.client import get_db
from src.news_trader.classifier import LLMProviderError, classify
from src.news_trader.db import ensure_indexes, news_raw, positions, signals

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_ACTIONABLE_CONFIDENCE = {"high", "medium"}

# Cap concurrent LLM calls per Lambda invocation — prevents rate-limit errors
# on providers with low RPM budgets (e.g. Anthropic free tier ~60 RPM).
# NOTE: the Semaphore is created per-invocation inside _run(), not at module
# scope. asyncio primitives bind to the running loop on first use, and the
# handler calls asyncio.run() (a fresh loop) on every Lambda invocation — a
# module-global semaphore would raise "bound to a different event loop" on the
# second (warm) invocation.
_LLM_MAX_CONCURRENCY = 5

# Articles older than this at classification time are skipped — the market has
# already had time to price in the news. Especially important for BSE backfills
# that arrive hours after the actual announcement.
_MAX_NEWS_AGE_SECONDS = 2 * 3600  # 2 hours

# Pre-LLM noise filter — headline patterns that are provably non-price-moving.
# Articles matching any pattern are skipped before hitting the LLM semaphore,
# saving the API call entirely. Two categories:
#
#   1. BSE/NSE admin filings: AGM notices, trading-window closures, postal-ballot
#      admin, scrutinizer reports, board-meeting invites (future date), audio-link
#      postings, SEBI Reg-30(11) rumour-verification boilerplate, IEPF transfers,
#      exchange fines, newspaper-publication compliance ads.
#
#   2. External market noise: foreign index wraps (Nikkei, S&P 500, etc.), broad
#      Sensex/Nifty end-of-day summaries, standalone INR/forex-reserve moves.
#      These never resolve to actionable Indian equities — the LLM would either
#      output low-confidence or hallucinate foreign tickers (NVDA, MSFT).
_NOISE_PATTERNS: list[str] = [
    # BSE/NSE admin filings
    r"trading window (?:closure|opening)",
    r"(?:agm|annual general meeting) notice",
    r"\bannual report \d{4}",
    r"scrutinizer[s']? report",
    r"postal ballot",
    r"\bnon-applicability\b",
    r"\bcorrigendum\b",
    r"board of directors.{0,60}scheduled on",
    r"\baudio (?:recordings?|link)\b",
    r"regulation 30\(11\)",
    r"\bfines imposed by (?:nse|bse)\b",
    r"\biepf authority\b",
    r"newspaper (?:publication|advertisement)",
    r"publication.{0,30}newspapers?",
    # Foreign market indices
    r"\b(?:nikkei|hang seng|dow jones|s&p 500|nasdaq|ftse|kospi|cac 40|dax)\b",
    r"\bshanghai (?:composite|stocks?|index)\b",
    # Broad Sensex/Nifty end-of-day wraps (not sector-index moves like "Nifty IT")
    r"\b(?:sensex|nifty)\b.{0,35}\b(?:end|close|open|fall|rise|slip|surge|tumble|gain|drop)s?\b",
    r"\b(?:nifty|sensex)\b.{0,15}\b(?:futures?|options?)\b",
    # Standalone forex moves with no specific equity trigger
    r"\b(?:rupee|inr)\b.{0,30}\b(?:slide|fall|rise|drop|weaken|strengthen|slip|gain)s?\b",
    r"\bforex reserves?\b",
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)


def _hour_bucket(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


async def _process_message(
    msg: dict[str, Any],
    settings: Settings,
    llm_semaphore: asyncio.Semaphore,
    run_id: str | None = None,
    open_positions: int = 0,
) -> tuple[bool, str | None]:
    """Returns (signal_enqueued, llm_error_message).

    signal_enqueued is True only when a NEW signal was created and enqueued.
    llm_error_message is non-None only when the LLM provider call itself failed.

    Dedup contract: at most one nt_signals row per (story_hash, hour-bucket).
    A second raw article in the same bucket bumps source_count + appends to
    news_ids, but does not produce a new signal or a trade-decision message.
    """
    db = get_db()
    news_id = msg.get("news_id")
    if not news_id:
        logger.warning("[CLASSIFIER] bad message — no news_id: %s", msg)
        return False, None

    article = await news_raw(db).find_one({"_id": ObjectId(news_id)})
    if not article:
        logger.warning("[CLASSIFIER] article not found news_id=%s", news_id)
        return False, None

    if article.get("classified"):
        logger.debug("[CLASSIFIER] already classified news_id=%s", news_id)
        return False, None

    # Filter B — freshness gate.
    # Use published_at (when the story actually broke) not ingested_at.
    # BSE backfills, late RSS syncs, and overnight queues all risk acting on
    # news the market has already priced — skip anything older than 2 hours.
    now = datetime.now(tz=timezone.utc)
    pub_ts: datetime | None = article.get("published_at") or article.get("ingested_at")
    if pub_ts is not None:
        # MongoDB returns datetimes as naive UTC — normalise before subtracting.
        if pub_ts.tzinfo is None:
            pub_ts = pub_ts.replace(tzinfo=timezone.utc)
        age_seconds = (now - pub_ts).total_seconds()
        if age_seconds > _MAX_NEWS_AGE_SECONDS:
            logger.info(
                "[CLASSIFIER] stale news_id=%s age=%.0fmin — marking classified without LLM",
                news_id, age_seconds / 60,
            )
            await news_raw(db).update_one(
                {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
            )
            return False, None

    headline = article.get("headline", "")
    story_hash = article.get("story_hash") or article.get("topic_hash")
    if not story_hash:
        logger.warning("[CLASSIFIER] no story_hash/topic_hash on article news_id=%s — skipping", news_id)
        return False, None

    window_bucket = _hour_bucket(now)

    # Pre-LLM dedup check: another article for the same story has already been
    # classified in this hour. Just absorb this raw doc into the existing signal.
    existing = await signals(db).find_one_and_update(
        {"story_hash": story_hash, "window_bucket": window_bucket},
        {
            "$inc": {"source_count": 1},
            "$push": {"news_ids": news_id},
            "$set": {"last_seen_at": now},
        },
    )
    if existing is not None:
        logger.info(
            "[CLASSIFIER] dedup hit story_hash=%s window=%s source_count→%d news_id=%s — skipping LLM",
            story_hash, window_bucket.isoformat(), existing.get("source_count", 1) + 1, news_id,
        )
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        return False, None

    # Pre-LLM noise filter: skip admin filings and foreign-market wraps before
    # acquiring the semaphore. These reliably produce low-confidence signals
    # anyway — skipping avoids the API call entirely.
    if _NOISE_RE.search(headline):
        logger.info(
            "[CLASSIFIER] noise filter news_id=%s headline=%.80s — skipping LLM",
            news_id, headline,
        )
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        return False, None

    # Portfolio capacity gate: skip the LLM when all position slots are occupied.
    # With 5-day holds a full book typically stays full for days, so classifying
    # those articles burns tokens with no realistic path to a trade.
    if open_positions >= settings.nt_max_positions:
        logger.info(
            "[CLASSIFIER] portfolio full (%d/%d) news_id=%s — skipping LLM",
            open_positions, settings.nt_max_positions, news_id,
        )
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        return False, None

    logger.info("[CLASSIFIER] processing news_id=%s headline=%.80s", news_id, headline)

    raw_text = article.get("raw_text", headline)
    logger.info("[CLASSIFIER] calling LLM (provider=%s) for news_id=%s...", settings.ai_provider, news_id)

    try:
        async with llm_semaphore:
            result = await asyncio.to_thread(classify, raw_text, settings)
    except LLMProviderError as exc:
        logger.error("[CLASSIFIER] LLM provider error news_id=%s err=%s — marking classified", news_id, exc)
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        return False, str(exc)

    if result is None:
        logger.warning("[CLASSIFIER] LLM parse/validation failed news_id=%s headline=%.80s — marking classified",
                       news_id, headline)
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        return False, None

    logger.info("[CLASSIFIER] LLM result news_id=%s signal=%s confidence=%s magnitude=%s stocks=%s sector=%s",
                news_id, result["signal"], result["confidence"], result["magnitude"],
                result["stocks"], result["sector"])

    # Determine actionability before inserting so acted_on is set correctly from the start.
    # Non-actionable signals are never enqueued to trade-decision, so _maybe_finalise_run
    # must not count them as "pending" — marking acted_on=True immediately prevents runs
    # from being permanently stuck in status "running".
    _actionable = (
        result["confidence"] in _ACTIONABLE_CONFIDENCE
        and result["signal"] != "neutral"
        and bool(result["stocks"])
    )

    # Capture stock prices at the moment of signal creation (before the 15-min
    # SQS delay). Paired with entry_price in trade_decision, this lets us compute
    # entry_chase_pct — how far the entry chased the move during the delay window.
    # Only fetched for actionable signals (~7–20/day) so yfinance load is negligible.
    # Fail-open: price fetch failure stores {} and never blocks classification.
    price_at_signal: dict[str, float] = {}
    if _actionable and result["stocks"]:
        try:
            from src.news_trader.prices import get_ltps  # lazy: yfinance/pandas
            price_at_signal = await asyncio.wait_for(
                asyncio.to_thread(get_ltps, result["stocks"]),
                timeout=15.0,
            )
            logger.info("[CLASSIFIER] price_at_signal news_id=%s %s",
                        news_id, {s: f"₹{p:.2f}" for s, p in price_at_signal.items()})
        except Exception as exc:
            logger.warning("[CLASSIFIER] price_at_signal fetch failed news_id=%s err=%s — continuing", news_id, exc)

    signal_doc = {
        "story_hash": story_hash,
        "window_bucket": window_bucket,
        "news_ids": [news_id],
        "source_count": 1,
        "first_seen_at": now,
        "last_seen_at": now,
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
        "created_at": now,
        "acted_on": not _actionable,
        # Links this signal back to the pipeline run that ingested its article.
        # Used by _maybe_finalise_run and sweep_stale_runs to count signals per
        # run without time-window overlap across concurrent runs.
        "pipeline_run_id": run_id,
        # Price of each named stock at classification time. Empty dict when fetch
        # failed or signal is non-actionable. trade_decision uses this to compute
        # entry_chase_pct = (entry_price - signal_price) / signal_price.
        "price_at_signal": price_at_signal,
    }
    try:
        insert_result = await signals(db).insert_one(signal_doc)
    except DuplicateKeyError:
        # Race: a concurrent worker inserted the same (story_hash, bucket) between
        # our find_one_and_update and insert_one. Retry the absorb path.
        await signals(db).update_one(
            {"story_hash": story_hash, "window_bucket": window_bucket},
            {
                "$inc": {"source_count": 1},
                "$push": {"news_ids": news_id},
                "$set": {"last_seen_at": now},
            },
        )
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        logger.info("[CLASSIFIER] dedup race resolved story_hash=%s — absorbed news_id=%s",
                    story_hash, news_id)
        return False, None

    signal_id = str(insert_result.inserted_id)

    await news_raw(db).update_one(
        {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
    )

    # Only actionable signals go to the trade-decision queue
    if not _actionable:
        if result["confidence"] not in _ACTIONABLE_CONFIDENCE:
            logger.info("[CLASSIFIER] signal not actionable confidence=%s — saved but not enqueued",
                        result["confidence"])
        elif result["signal"] == "neutral":
            logger.info("[CLASSIFIER] signal is neutral — saved but not enqueued")
        else:
            logger.info("[CLASSIFIER] no stocks identified — saved but not enqueued")
        return False, None

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
        await asyncio.to_thread(sqs.send_message, **enqueue_kwargs)
        logger.info("[CLASSIFIER] enqueued signal_id=%s to trade-decision queue delay=%ds",
                    signal_id, settings.nt_news_delay_seconds)
    else:
        logger.warning("[CLASSIFIER] NEWS_SIGNALS_QUEUE_URL not set — skipping SQS enqueue")

    return True, None


async def _run(event: dict[str, Any], settings: Settings) -> dict[str, int]:
    db = get_db()
    await ensure_indexes(db)

    records = event.get("Records", [])
    logger.info("[CLASSIFIER] processing %d SQS record(s)", len(records))

    # Snapshot open position count once per invocation — one DB call for the
    # whole batch rather than one per message.
    open_positions = await positions(db).count_documents({"status": "open"})
    logger.info("[CLASSIFIER] open positions=%d / max=%d", open_positions, settings.nt_max_positions)

    # Created here (not at module scope) so it binds to this invocation's loop.
    llm_semaphore = asyncio.Semaphore(_LLM_MAX_CONCURRENCY)

    # Track per-run_id outcomes to detect full LLM provider failures.
    # Keyed by run_id: {"llm_errors": int, "signals": int, "first_error": str}
    run_stats: dict[str, dict[str, Any]] = {}

    async def _handle_record(record: dict[str, Any]) -> bool:
        run_id: str | None = None
        try:
            msg = json.loads(record["body"])
            attrs = record.get("messageAttributes", {})
            run_id = attrs.get("run_id", {}).get("stringValue")
            signal_created, llm_error = await _process_message(
                msg, settings, llm_semaphore, run_id=run_id, open_positions=open_positions
            )
            if run_id:
                stats = run_stats.setdefault(run_id, {"llm_errors": 0, "signals": 0, "first_error": ""})
                if llm_error:
                    stats["llm_errors"] += 1
                    if not stats["first_error"]:
                        stats["first_error"] = llm_error
                if signal_created:
                    stats["signals"] += 1
            return signal_created
        except Exception as exc:
            logger.error("[CLASSIFIER] record error err=%s record=%.200s", exc, record)
            return False

    results = await asyncio.gather(
        *[_handle_record(r) for r in records], return_exceptions=True
    )
    acted = sum(1 for r in results if r is True)

    # Call fail_run for any run where every LLM attempt failed and no signals were created.
    # Skipped/stale/dedup articles don't count against this — only actual LLM provider errors.
    if run_stats:
        from src.news_trader.pipeline_lifecycle import fail_run
        for run_id, stats in run_stats.items():
            if stats["llm_errors"] > 0 and stats["signals"] == 0:
                error_msg = f"LLM provider error ({stats['llm_errors']} failed): {stats['first_error']}"
                logger.error("[CLASSIFIER] failing run_id=%s — %s", run_id, error_msg)
                try:
                    await fail_run(run_id, error_msg)
                except Exception as exc:
                    logger.error("[CLASSIFIER] fail_run error run_id=%s err=%s", run_id, exc)

    logger.info("[CLASSIFIER] done — processed=%d actionable=%d", len(records), acted)
    return {"processed": len(records), "acted_on": acted}


def handler(event: dict[str, Any], context: object) -> dict[str, int]:
    settings = Settings()
    return asyncio.run(_run(event, settings))
