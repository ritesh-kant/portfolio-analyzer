"""Local Lambda invoker for news-trader handlers.

Simulates what EventBridge / SQS would pass to each handler.
Requires: local MongoDB running (docker compose up -d mongodb)
          GEMINI_API_KEY set in .env or environment

Usage:
    # From apps/signal-engine/
    uv run python scripts/invoke_news_trader.py ingester
    uv run python scripts/invoke_news_trader.py classifier <news_id>
    uv run python scripts/invoke_news_trader.py trade <signal_id>
    uv run python scripts/invoke_news_trader.py monitor
    uv run python scripts/invoke_news_trader.py pipeline     # ingester → classifier → trade
"""

import asyncio
import json
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _sqs_event(records: list[dict]) -> dict:
    return {"Records": [{"body": json.dumps(r)} for r in records]}


# ── individual invokers ──────────────────────────────────────────────────────

def run_ingester():
    print("\n=== news_ingester ===")
    from handlers.news_ingester import handler
    result = handler({}, None)
    print(json.dumps(result, indent=2, default=str))
    return result


def run_classifier(news_id: str):
    print(f"\n=== news_classifier  news_id={news_id} ===")
    from handlers.news_classifier import handler
    event = _sqs_event([{"news_id": news_id}])
    result = handler(event, None)
    print(json.dumps(result, indent=2, default=str))
    return result


def run_trade(signal_id: str):
    print(f"\n=== trade_decision  signal_id={signal_id} ===")
    from handlers.trade_decision import handler
    event = _sqs_event([{"signal_id": signal_id}])
    result = handler(event, None)
    print(json.dumps(result, indent=2, default=str))
    return result


def run_monitor():
    print("\n=== sl_monitor ===")
    from handlers.sl_monitor import handler
    result = handler({}, None)
    print(json.dumps(result, indent=2, default=str))
    return result


# ── end-to-end pipeline ──────────────────────────────────────────────────────

async def _get_new_ids_since(collection_name: str, since_ts) -> list[str]:
    """Query MongoDB for docs inserted since since_ts."""
    from bson import ObjectId
    from src.db.client import get_db
    db = get_db()
    cursor = db[collection_name].find({"ingested_at": {"$gte": since_ts}}, {"_id": 1})
    return [str(doc["_id"]) async for doc in cursor]


async def _get_signal_ids_for_news(news_ids: list[str]) -> list[str]:
    from src.db.client import get_db
    db = get_db()
    cursor = db["nt_signals"].find({"news_id": {"$in": news_ids}}, {"_id": 1})
    return [str(doc["_id"]) async for doc in cursor]


def run_pipeline():
    """Run ingester → classifier (for each new article) → trade (for each signal)."""
    import asyncio
    from datetime import datetime, timezone

    print("\n=== FULL PIPELINE ===")
    start = datetime.now(tz=timezone.utc)

    # 1. Ingest
    ingest_result = run_ingester()
    n_new = ingest_result.get("new_articles", 0)
    if n_new == 0:
        print("No new articles — nothing to classify (may be outside market hours or no new news)")
        return

    # 2. Fetch IDs inserted in this run
    news_ids = asyncio.run(_get_new_ids_since("nt_news_raw", start))
    print(f"\nFound {len(news_ids)} new article IDs to classify")

    # 3. Classify each one (skip SQS delay — direct invoke)
    for news_id in news_ids[:10]:  # cap at 10 for local testing
        run_classifier(news_id)
        time.sleep(0.5)  # avoid Gemini rate limits

    # 4. Fetch signals that were just created
    signal_ids = asyncio.run(_get_signal_ids_for_news(news_ids))
    print(f"\nFound {len(signal_ids)} signals to act on")

    # 5. Trade decision for each actionable signal
    for signal_id in signal_ids:
        run_trade(signal_id)
        time.sleep(0.2)

    print("\n=== PIPELINE COMPLETE ===")


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "ingester":
        run_ingester()

    elif cmd == "classifier":
        if len(sys.argv) < 3:
            print("Usage: invoke_news_trader.py classifier <news_id>")
            sys.exit(1)
        run_classifier(sys.argv[2])

    elif cmd == "trade":
        if len(sys.argv) < 3:
            print("Usage: invoke_news_trader.py trade <signal_id>")
            sys.exit(1)
        run_trade(sys.argv[2])

    elif cmd == "monitor":
        run_monitor()

    elif cmd == "pipeline":
        run_pipeline()

    else:
        print(__doc__)
