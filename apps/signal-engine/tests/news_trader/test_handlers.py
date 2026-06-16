"""Integration-style tests for Lambda handlers — MongoDB is mocked with mongomock.

These tests verify the full handler logic (dedup, SQS dispatch, position opening,
SL monitor exits) without a real DB or real AWS services.

Requires: pip install mongomock motor  (already in dev deps via motor; mongomock
          is added below — see conftest note).
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Import handler modules up-front so mock.patch can resolve dotted names
import handlers.news_ingester      # noqa: F401
import handlers.news_classifier    # noqa: F401
import handlers.trade_decision     # noqa: F401
import handlers.sl_monitor         # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sqs_event(bodies: list[dict]) -> dict:
    """Wrap dicts in the SQS Records envelope a Lambda receives."""
    return {
        "Records": [
            {"body": json.dumps(b), "receiptHandle": f"rh-{i}"}
            for i, b in enumerate(bodies)
        ]
    }


# ---------------------------------------------------------------------------
# trailing_sl logic (no I/O — fast smoke tests at handler level)
# ---------------------------------------------------------------------------

class TestTrailingSLInSlMonitor:
    """Verify sl_monitor correctly closes positions via check_exit."""

    @pytest.mark.asyncio
    async def test_sl_hit_closes_position(self, tmp_path):
        from src.news_trader.trailing_sl import check_exit

        result = check_exit(
            current_price=97.0,
            trailing_sl=98.5,
            target_price=108.0,
            held_sessions=1,
            max_hold_days=5,
        )
        assert result == "sl_hit"

    @pytest.mark.asyncio
    async def test_target_hit_closes_position(self, tmp_path):
        from src.news_trader.trailing_sl import check_exit

        result = check_exit(
            current_price=109.0,
            trailing_sl=98.5,
            target_price=108.0,
            held_sessions=1,
            max_hold_days=5,
        )
        assert result == "target_hit"


class TestMonitorPositionNaiveDatetime:
    """entry_at comes back from Mongo as a NAIVE UTC datetime. The held_minutes
    computation must not raise (offset-naive vs offset-aware TypeError) — this
    exact bug silently killed every monitor cycle on 2026-06-11."""

    @pytest.mark.asyncio
    @patch("handlers.sl_monitor.alert_trade_closed")
    @patch("handlers.sl_monitor.get_db")
    async def test_naive_entry_at_time_stop_fires(self, mock_get_db, mock_alert):
        from datetime import timedelta
        from bson import ObjectId
        from handlers.sl_monitor import _monitor_position

        mock_db = MagicMock()
        mock_pos_coll = MagicMock()
        mock_pos_coll.update_one = AsyncMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_pos_coll)
        mock_get_db.return_value = mock_db

        pos = {
            "_id": ObjectId(),
            "symbol": "TESTSTOCK",
            "paper": True,
            "entry_price": 100.0,
            "current_price": 100.5,
            "highest_price": 100.5,
            "lowest_price": 99.8,
            "trailing_sl": 97.0,
            "target_price": 101.0,
            "qty": 10,
            # naive, 2 hours old — past the 90-min time-stop
            "entry_at": datetime.utcnow() - timedelta(minutes=120),
            "initial_sl_pct_used": 0.03,
            "trail_sl_pct_used": 0.015,
            "trail_activate_pct_used": 0.02,
            "max_hold_minutes_used": 90,
            "force_close_eod_used": True,
        }

        settings = MagicMock()
        settings.nt_max_hold_days = 5
        settings.nt_max_hold_minutes = 90
        settings.telegram_bot_token = ""
        settings.telegram_chat_id = ""

        result = await _monitor_position(pos, settings, price=100.5)
        assert result == "closed:time_stop"


# ---------------------------------------------------------------------------
# news_ingester handler (mocked scrapers + mocked DB)
# ---------------------------------------------------------------------------

class TestNewsIngesterHandler:

    @patch("handlers.news_ingester._is_market_hours", return_value=False)
    @patch("handlers.news_ingester.Settings")
    def test_skips_outside_market_hours(self, MockSettings, _mock):
        MockSettings.return_value.nt_bypass_market_hours = False
        from handlers.news_ingester import handler
        result = handler({}, None)
        assert result == {"skipped": "outside_market_hours"}

    @patch("handlers.news_ingester._is_market_hours", return_value=True)
    @patch("handlers.news_ingester.fetch_all_rss")
    @patch("handlers.news_ingester.fetch_nse_announcements")
    @patch("handlers.news_ingester.fetch_bse_announcements")
    @patch("handlers.news_ingester.get_db")
    @patch("handlers.news_ingester.boto3")
    def test_no_articles_returns_zero(
        self, mock_boto3, mock_get_db, mock_bse, mock_nse, mock_rss, _market
    ):
        import asyncio

        async def _empty_rss(): return [], {}
        async def _empty(): return []

        mock_rss.side_effect = _empty_rss
        mock_nse.side_effect = _empty
        mock_bse.side_effect = _empty

        # Fake async DB
        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_coll.create_index = AsyncMock()
        mock_coll.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=[]))
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_get_db.return_value = mock_db

        from handlers.news_ingester import handler
        result = handler({}, None)
        assert result["new_articles"] == 0


# ---------------------------------------------------------------------------
# news_classifier handler (mocked Gemini + mocked DB)
# ---------------------------------------------------------------------------

class TestNewsClassifierHandler:

    def _make_signal_response(self) -> str:
        return json.dumps({
            "sector": "Banking",
            "signal": "bullish",
            "magnitude": "major",
            "stocks": ["HDFCBANK", "SBIN"],
            "confidence": "high",
            "reasoning": "Rate cut boosts margins.",
            "event_type": "regulatory",
        })

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_dedup_hit_skips_llm_and_bumps_source_count(self, mock_get_llm, mock_boto3, mock_get_db):
        """Second article with same story_hash + same hour bucket: NO LLM call,
        the existing signal gets source_count bumped and news_ids appended.
        """
        from bson import ObjectId
        news_id = str(ObjectId())
        article = {
            "_id": ObjectId(news_id),
            "raw_text": "RBI cuts repo rate by 25bp",
            "headline": "RBI cuts repo rate by 25bp",
            "source": "Moneycontrol",
            "story_hash": "abc123storyhashfoobar99",
            "topic_hash": "topic-mc-xxxxxxxxxxxxxxxx",
            "classified": False,
        }

        # find_one_and_update returns the pre-existing signal — dedup hit path.
        existing_signal = {
            "_id": ObjectId(),
            "story_hash": "abc123storyhashfoobar99",
            "source_count": 1,
            "news_ids": ["pre-existing-id"],
        }

        mock_db = MagicMock()
        mock_news_coll = MagicMock()
        mock_news_coll.create_index = AsyncMock()
        mock_news_coll.find_one = AsyncMock(return_value=article)
        mock_news_coll.update_one = AsyncMock()

        mock_sig_coll = MagicMock()
        mock_sig_coll.create_index = AsyncMock()
        mock_sig_coll.find_one_and_update = AsyncMock(return_value=existing_signal)
        mock_sig_coll.insert_one = AsyncMock()  # must NOT be called

        def _get_coll(name):
            if name == "nt_news_raw":
                return mock_news_coll
            if name == "nt_signals":
                return mock_sig_coll
            c = MagicMock(); c.create_index = AsyncMock(); c.count_documents = AsyncMock(return_value=0); return c

        mock_db.__getitem__ = MagicMock(side_effect=_get_coll)
        mock_get_db.return_value = mock_db

        sqs_client = MagicMock()
        mock_boto3.client.return_value = sqs_client

        from handlers.news_classifier import handler
        event = _make_sqs_event([{"news_id": news_id}])

        with patch("handlers.news_classifier.Settings") as MockSettings:
            s = MockSettings.return_value
            s.ai_provider = "ollama"
            s.gemini_api_key = "fake"
            s.gemini_model = "gemini-1.5-flash"
            s.news_signals_queue_url = "https://sqs.example.com/signals"
            s.nt_news_delay_seconds = 900
            s.nt_max_positions = 10
            handler(event, None)

        # Dedup hit: LLM never called, no new signal inserted, no SQS dispatch.
        mock_get_llm.assert_not_called()
        mock_sig_coll.insert_one.assert_not_called()
        sqs_client.send_message.assert_not_called()
        # The find_one_and_update call should have $inc source_count and $push news_ids.
        mock_sig_coll.find_one_and_update.assert_called_once()
        call_args = mock_sig_coll.find_one_and_update.call_args
        update_doc = call_args[0][1]
        assert update_doc["$inc"] == {"source_count": 1}
        assert update_doc["$push"] == {"news_ids": news_id}

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_new_story_creates_signal_with_dedup_fields(self, mock_get_llm, mock_boto3, mock_get_db):
        """First sighting of a story: LLM is called, signal inserted with
        story_hash, window_bucket, source_count=1, news_ids=[news_id].
        """
        from bson import ObjectId
        news_id = str(ObjectId())
        article = {
            "_id": ObjectId(news_id),
            "raw_text": "RBI cuts repo rate by 25bp",
            "headline": "RBI cuts repo rate by 25bp",
            "source": "Economic Times",
            "story_hash": "xyz789newstoryhash22aa",
            "topic_hash": "topic-et-yyyyyyyyyyyyyyyy",
            "classified": False,
        }

        mock_get_llm.return_value.invoke.return_value = MagicMock(content=json.dumps({
            "sector": "Banking",
            "signal": "bullish",
            "magnitude": "major",
            "stocks": ["HDFCBANK"],
            "confidence": "high",
            "reasoning": "Rate cut.",
            "event_type": "regulatory",
        }))

        mock_db = MagicMock()
        mock_news_coll = MagicMock()
        mock_news_coll.create_index = AsyncMock()
        mock_news_coll.find_one = AsyncMock(return_value=article)
        mock_news_coll.update_one = AsyncMock()

        mock_sig_coll = MagicMock()
        mock_sig_coll.create_index = AsyncMock()
        mock_sig_coll.find_one_and_update = AsyncMock(return_value=None)  # new story
        mock_sig_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))

        def _get_coll(name):
            if name == "nt_news_raw":
                return mock_news_coll
            if name == "nt_signals":
                return mock_sig_coll
            c = MagicMock(); c.create_index = AsyncMock(); c.count_documents = AsyncMock(return_value=0); return c

        mock_db.__getitem__ = MagicMock(side_effect=_get_coll)
        mock_get_db.return_value = mock_db

        sqs_client = MagicMock()
        mock_boto3.client.return_value = sqs_client

        from handlers.news_classifier import handler
        event = _make_sqs_event([{"news_id": news_id}])

        with patch("handlers.news_classifier.Settings") as MockSettings:
            s = MockSettings.return_value
            s.ai_provider = "ollama"
            s.gemini_api_key = "fake"
            s.gemini_model = "gemini-1.5-flash"
            s.news_signals_queue_url = "https://sqs.example.com/signals"
            s.nt_news_delay_seconds = 900
            s.nt_max_positions = 10
            handler(event, None)

        mock_sig_coll.insert_one.assert_called_once()
        inserted = mock_sig_coll.insert_one.call_args[0][0]
        assert inserted["story_hash"] == "xyz789newstoryhash22aa"
        assert inserted["news_ids"] == [news_id]
        assert inserted["source_count"] == 1
        # Publisher set seeded from the article (falls back to source here).
        assert inserted["publishers"] == ["Economic Times"]
        assert "window_bucket" in inserted
        assert inserted["window_bucket"].minute == 0
        assert inserted["window_bucket"].second == 0
        # Actionable signal → SQS send_message called.
        sqs_client.send_message.assert_called_once()

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_entity_merge_absorbs_cross_source_story(self, mock_get_llm, mock_boto3, mock_get_db):
        """Different headline (different story_hash) but same direction +
        overlapping stock within the merge window: LLM IS called (we need the
        entity list), but the result is absorbed into the existing signal —
        no new insert, no SQS dispatch.
        """
        from bson import ObjectId
        news_id = str(ObjectId())
        article = {
            "_id": ObjectId(news_id),
            "raw_text": "HDFC Bank surges as RBI rate cut lifts lenders",
            "headline": "HDFC Bank surges as RBI rate cut lifts lenders",
            "source": "LiveMint",
            "story_hash": "DIFFERENT-hash-from-et-version",
            "topic_hash": "topic-lm-zzzzzzzzzzzzzzzz",
            "classified": False,
        }

        mock_get_llm.return_value.invoke.return_value = MagicMock(content=json.dumps({
            "sector": "Banking",
            "signal": "bullish",
            "magnitude": "major",
            "stocks": ["HDFCBANK"],
            "confidence": "high",
            "reasoning": "Rate cut boosts margins.",
            "event_type": "regulatory",
        }))

        existing_signal = {
            "_id": ObjectId(),
            "story_hash": "original-et-version-hash",
            "stocks": ["HDFCBANK", "SBIN"],
            "signal": "bullish",
            "source_count": 1,
            "news_ids": ["original-news-id"],
        }

        mock_db = MagicMock()
        mock_news_coll = MagicMock()
        mock_news_coll.create_index = AsyncMock()
        mock_news_coll.find_one = AsyncMock(return_value=article)
        mock_news_coll.update_one = AsyncMock()

        mock_sig_coll = MagicMock()
        mock_sig_coll.create_index = AsyncMock()
        # 1st find_one_and_update = story_hash dedup (miss), 2nd = entity merge (hit).
        mock_sig_coll.find_one_and_update = AsyncMock(side_effect=[None, existing_signal])
        mock_sig_coll.insert_one = AsyncMock()  # must NOT be called

        def _get_coll(name):
            if name == "nt_news_raw":
                return mock_news_coll
            if name == "nt_signals":
                return mock_sig_coll
            c = MagicMock(); c.create_index = AsyncMock(); c.count_documents = AsyncMock(return_value=0); return c

        mock_db.__getitem__ = MagicMock(side_effect=_get_coll)
        mock_get_db.return_value = mock_db

        sqs_client = MagicMock()
        mock_boto3.client.return_value = sqs_client

        from handlers.news_classifier import handler
        event = _make_sqs_event([{"news_id": news_id}])

        with patch("handlers.news_classifier.Settings") as MockSettings:
            s = MockSettings.return_value
            s.ai_provider = "ollama"
            s.gemini_api_key = "fake"
            s.gemini_model = "gemini-1.5-flash"
            s.news_signals_queue_url = "https://sqs.example.com/signals"
            s.nt_news_delay_seconds = 900
            s.nt_max_positions = 10
            handler(event, None)

        # Absorbed: no new signal, no SQS dispatch, article marked classified.
        mock_sig_coll.insert_one.assert_not_called()
        sqs_client.send_message.assert_not_called()
        mock_news_coll.update_one.assert_called_once()

        # The merge query must match on direction + overlapping stocks + recency,
        # and the update must bump source_count and append the news id.
        assert mock_sig_coll.find_one_and_update.call_count == 2
        merge_call = mock_sig_coll.find_one_and_update.call_args_list[1]
        merge_filter, merge_update = merge_call[0][0], merge_call[0][1]
        assert merge_filter["signal"] == "bullish"
        assert merge_filter["stocks"] == {"$in": ["HDFCBANK"]}
        assert "$gte" in merge_filter["created_at"]
        # Only signals still awaiting trade-decision may absorb articles —
        # otherwise a strong story could merge into a dead signal and never trade.
        assert merge_filter["acted_on"] is False
        assert merge_update["$inc"] == {"source_count": 1}
        assert merge_update["$push"] == {"news_ids": news_id}
        # Distinct-publisher set is grown on merge (LiveMint, here).
        assert merge_update["$addToSet"] == {"publishers": "LiveMint"}

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_low_confidence_not_enqueued(self, mock_get_llm, mock_boto3, mock_get_db):
        """low-confidence signals must NOT be enqueued to the signals queue."""
        low_conf = json.dumps({
            "sector": "Generic",
            "signal": "neutral",
            "magnitude": "minor",
            "stocks": [],
            "confidence": "low",
            "reasoning": "Nothing specific.",
            "event_type": "other",
        })
        mock_get_llm.return_value.invoke.return_value = MagicMock(content=low_conf)

        sqs_client = MagicMock()
        mock_boto3.client.return_value = sqs_client

        from bson import ObjectId
        news_id = str(ObjectId())
        article = {
            "_id": ObjectId(news_id),
            "raw_text": "Generic market update",
            "headline": "Markets flat",
            "source": "Test",
            "story_hash": "lowconfstoryhashxxxxxxxx",
            "topic_hash": "lowconftopichashxxxxxxxx",
            "classified": False,
        }

        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_coll.create_index = AsyncMock()
        mock_coll.find_one = AsyncMock(return_value=article)
        mock_coll.find_one_and_update = AsyncMock(return_value=None)  # new story
        mock_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        mock_coll.update_one = AsyncMock()
        mock_coll.count_documents = AsyncMock(return_value=0)
        mock_db.__getitem__ = MagicMock(return_value=mock_coll)
        mock_get_db.return_value = mock_db

        from handlers.news_classifier import handler
        event = _make_sqs_event([{"news_id": news_id}])

        # Patch settings to have a queue URL so we can verify it's NOT called
        with patch("handlers.news_classifier.Settings") as MockSettings:
            s = MockSettings.return_value
            s.gemini_api_key = "fake"
            s.gemini_model = "gemini-1.5-flash"
            s.news_signals_queue_url = "https://sqs.example.com/signals"
            s.nt_news_delay_seconds = 900
            s.nt_max_positions = 10
            handler(event, None)

        # SQS send_message must NOT have been called
        sqs_client.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Freshness gate — Filter B
# ---------------------------------------------------------------------------

class TestFreshnessGate:
    """Verify the freshness gate handles naive/aware datetimes and age correctly."""

    def _base_article(self, news_id, published_at):
        from bson import ObjectId
        return {
            "_id": ObjectId(news_id),
            "raw_text": "RBI cuts repo rate",
            "headline": "RBI cuts repo rate",
            "source": "ET",
            "story_hash": "freshtest123456789abcde",
            "topic_hash": "freshtest123456789abcde",
            "classified": False,
            "published_at": published_at,
        }

    def _run_classifier(self, article, mock_get_llm, mock_boto3, mock_get_db):
        from bson import ObjectId
        news_id = str(article["_id"])

        mock_db = MagicMock()
        mock_news_coll = MagicMock()
        mock_news_coll.create_index = AsyncMock()
        mock_news_coll.find_one = AsyncMock(return_value=article)
        mock_news_coll.update_one = AsyncMock()

        mock_sig_coll = MagicMock()
        mock_sig_coll.create_index = AsyncMock()
        mock_sig_coll.find_one_and_update = AsyncMock(return_value=None)
        mock_sig_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))

        def _get_coll(name):
            if name == "nt_news_raw": return mock_news_coll
            if name == "nt_signals": return mock_sig_coll
            c = MagicMock(); c.create_index = AsyncMock(); c.count_documents = AsyncMock(return_value=0); return c

        mock_db.__getitem__ = MagicMock(side_effect=_get_coll)
        mock_get_db.return_value = mock_db

        sqs_client = MagicMock()
        mock_boto3.client.return_value = sqs_client

        from handlers.news_classifier import handler
        event = _make_sqs_event([{"news_id": news_id}])
        with patch("handlers.news_classifier.Settings") as MockSettings:
            s = MockSettings.return_value
            s.ai_provider = "ollama"
            s.gemini_api_key = "fake"
            s.gemini_model = "gemini-1.5-flash"
            s.news_signals_queue_url = "https://sqs.example.com/signals"
            s.nt_news_delay_seconds = 900
            s.nt_max_positions = 10
            handler(event, None)
        return mock_get_llm, mock_sig_coll

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_naive_utc_published_at_does_not_crash(self, mock_get_llm, mock_boto3, mock_get_db):
        """MongoDB returns naive datetimes; the gate must not raise TypeError."""
        from bson import ObjectId
        from datetime import timedelta
        # Naive datetime — as returned by pymongo/motor
        recent_naive = datetime.utcnow() - timedelta(minutes=10)
        article = self._base_article(str(ObjectId()), recent_naive)
        # Should not raise — test passes if no exception is thrown
        self._run_classifier(article, mock_get_llm, mock_boto3, mock_get_db)

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_stale_article_skips_llm(self, mock_get_llm, mock_boto3, mock_get_db):
        """Article older than 2 hours must be marked classified without LLM call."""
        from bson import ObjectId
        from datetime import timedelta
        stale_naive = datetime.utcnow() - timedelta(hours=3)
        article = self._base_article(str(ObjectId()), stale_naive)
        mock_llm, mock_sig = self._run_classifier(article, mock_get_llm, mock_boto3, mock_get_db)
        mock_get_llm.assert_not_called()
        mock_sig.insert_one.assert_not_called()

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_fresh_article_reaches_llm(self, mock_get_llm, mock_boto3, mock_get_db):
        """Article published 20 minutes ago must proceed to the LLM."""
        from bson import ObjectId
        from datetime import timedelta
        recent_naive = datetime.utcnow() - timedelta(minutes=20)
        article = self._base_article(str(ObjectId()), recent_naive)
        mock_get_llm.return_value.invoke.return_value = MagicMock(content=json.dumps({
            "sector": "Banking", "signal": "bullish", "magnitude": "major",
            "stocks": ["HDFCBANK"], "confidence": "high", "reasoning": "RBI cut.",
            "event_type": "regulatory",
        }))
        _, mock_sig = self._run_classifier(article, mock_get_llm, mock_boto3, mock_get_db)
        mock_get_llm.assert_called_once()
        mock_sig.insert_one.assert_called_once()

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.get_llm")
    def test_missing_published_at_passes_gate(self, mock_get_llm, mock_boto3, mock_get_db):
        """Articles with no published_at (e.g. some RSS feeds) must not be blocked."""
        from bson import ObjectId
        article = self._base_article(str(ObjectId()), None)
        del article["published_at"]  # truly absent field
        mock_get_llm.return_value.invoke.return_value = MagicMock(content=json.dumps({
            "sector": "IT", "signal": "bullish", "magnitude": "moderate",
            "stocks": ["TCS"], "confidence": "medium", "reasoning": "order win.",
            "event_type": "order_win",
        }))
        _, mock_sig = self._run_classifier(article, mock_get_llm, mock_boto3, mock_get_db)
        # Gate is fail-open — LLM should still be called
        mock_get_llm.assert_called_once()


# ---------------------------------------------------------------------------
# trade_decision handler
# ---------------------------------------------------------------------------

class TestTradeDecisionHandler:

    # get_ltps / get_market_snapshot / fetch_nifty_vix_sync are lazy-imported inside
    # the handler (yfinance/pandas cost), so they must be patched at their source
    # modules — they are not module-level attributes of handlers.trade_decision.
    # CANBK not HDFCBANK: the position symbol must be in NIFTY_500 but NOT in
    # NIFTY_50 — the large-cap exclusion (Filter E, default-on) would otherwise
    # reject the entry before it reaches the insert.
    @patch("src.scrapers.nse_market.fetch_nifty_vix_sync", return_value={})
    @patch("src.news_trader.prices.get_market_snapshot", return_value={"nifty50": None, "sector_index": None})
    @patch("handlers.trade_decision.get_db")
    @patch("src.news_trader.prices.get_ltps", return_value={"CANBK": 1500.0})
    @patch("handlers.trade_decision.alert_trade_entered")
    def test_opens_position_in_paper_mode(self, mock_alert, mock_price, mock_get_db, mock_snapshot, _regime):
        from bson import ObjectId

        signal_id = str(ObjectId())
        signal_doc = {
            "_id": ObjectId(signal_id),
            "stocks": ["CANBK"],
            "signal": "bullish",
            "sector": "Banking",
            "confidence": "high",
            "reasoning": "Rate cut.",
            "acted_on": False,
        }

        mock_db = MagicMock()
        mock_pos_coll = MagicMock()
        mock_pos_coll.create_index = AsyncMock()
        mock_pos_coll.count_documents = AsyncMock(return_value=0)   # 0 open positions
        mock_pos_coll.find_one = AsyncMock(return_value=None)        # no existing position
        mock_pos_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        # Backstop aggregation: ₹0 currently deployed.
        _agg_cursor = MagicMock()
        _agg_cursor.to_list = AsyncMock(return_value=[])
        mock_pos_coll.aggregate = MagicMock(return_value=_agg_cursor)

        mock_sig_coll = MagicMock()
        mock_sig_coll.create_index = AsyncMock()
        mock_sig_coll.find_one = AsyncMock(return_value=signal_doc)
        mock_sig_coll.update_one = AsyncMock()

        def _get_coll(name):
            if name == "nt_positions":
                return mock_pos_coll
            if name == "nt_signals":
                return mock_sig_coll
            coll = MagicMock()
            coll.create_index = AsyncMock()
            return coll

        mock_db.__getitem__ = MagicMock(side_effect=_get_coll)
        mock_get_db.return_value = mock_db

        from handlers.trade_decision import handler
        event = _make_sqs_event([{"signal_id": signal_id}])

        with patch("handlers.trade_decision.Settings") as MockSettings:
            s = MockSettings.return_value
            s.trading_mode = "paper"
            s.nt_max_positions = 10
            s.nt_max_stocks_per_signal = 2
            s.nt_max_positions_per_sector = 2
            s.nt_total_capital_inr = 100_000.0
            s.nt_sl_pct = 0.015
            s.nt_initial_sl_pct = 0.03
            s.nt_trail_sl_pct = 0.015
            s.nt_trail_activate_pct = 0.02
            s.nt_target_pct = 0.08
            s.nt_nifty50_exclusion = True     # default-on; CANBK is not NIFTY50 so passes
            s.nt_bypass_market_hours = True   # skip entry-cutoff gate in tests
            s.nt_entry_cutoff_ist = 870
            s.telegram_bot_token = ""
            s.telegram_chat_id = ""
            result = handler(event, None)

        assert result["positions_opened"] == 1
        mock_pos_coll.insert_one.assert_called_once()
        mock_alert.assert_called_once()

    # Patched at source modules — these are lazy imports inside the handler.
    # (Prices aren't actually fetched here: the capacity check returns first.)
    @patch("src.scrapers.nse_market.fetch_nifty_vix_sync", return_value={})
    @patch("handlers.trade_decision.get_db")
    @patch("src.news_trader.prices.get_ltps", return_value={"SBIN": 1500.0})
    @patch("handlers.trade_decision.alert_trade_entered")
    def test_respects_max_positions_limit(self, mock_alert, mock_price, mock_get_db, _regime):
        """When already at max, no new position should be opened."""
        from bson import ObjectId

        signal_id = str(ObjectId())
        signal_doc = {
            "_id": ObjectId(signal_id),
            "stocks": ["SBIN"],
            "signal": "bullish",
            "sector": "Banking",
            "confidence": "high",
            "reasoning": "test",
            "acted_on": False,
        }

        mock_db = MagicMock()
        mock_pos_coll = MagicMock()
        mock_pos_coll.create_index = AsyncMock()
        mock_pos_coll.count_documents = AsyncMock(return_value=10)  # at max_positions → full

        mock_sig_coll = MagicMock()
        mock_sig_coll.create_index = AsyncMock()
        mock_sig_coll.find_one = AsyncMock(return_value=signal_doc)
        mock_sig_coll.update_one = AsyncMock()

        def _get_coll(name):
            if name == "nt_positions": return mock_pos_coll
            if name == "nt_signals": return mock_sig_coll
            c = MagicMock(); c.create_index = AsyncMock(); return c

        mock_db.__getitem__ = MagicMock(side_effect=_get_coll)
        mock_get_db.return_value = mock_db

        from handlers.trade_decision import handler
        event = _make_sqs_event([{"signal_id": signal_id}])

        with patch("handlers.trade_decision.Settings") as MockSettings:
            s = MockSettings.return_value
            s.trading_mode = "paper"
            s.nt_max_positions = 10
            s.nt_max_stocks_per_signal = 2
            s.nt_total_capital_inr = 100_000.0
            s.nt_sl_pct = 0.015
            s.nt_initial_sl_pct = 0.03
            s.nt_trail_sl_pct = 0.015
            s.nt_trail_activate_pct = 0.02
            s.nt_target_pct = 0.08
            s.telegram_bot_token = ""
            s.telegram_chat_id = ""
            result = handler(event, None)

        assert result["positions_opened"] == 0
        mock_pos_coll.insert_one.assert_not_called()
        mock_alert.assert_not_called()


class TestNifty50Exclusion:
    """Filter E — NIFTY50 large-cap exclusion (BT5: large-caps have negative
    gross P&L; news is priced in before the 15-min delayed entry)."""

    @staticmethod
    def _mock_db(signal_doc):
        mock_db = MagicMock()
        mock_pos_coll = MagicMock()
        mock_pos_coll.create_index = AsyncMock()
        mock_pos_coll.count_documents = AsyncMock(return_value=0)
        mock_pos_coll.find_one = AsyncMock(return_value=None)
        mock_pos_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id=None))
        _agg_cursor = MagicMock()
        _agg_cursor.to_list = AsyncMock(return_value=[])
        mock_pos_coll.aggregate = MagicMock(return_value=_agg_cursor)

        mock_sig_coll = MagicMock()
        mock_sig_coll.create_index = AsyncMock()
        mock_sig_coll.find_one = AsyncMock(return_value=signal_doc)
        mock_sig_coll.update_one = AsyncMock()

        def _get_coll(name):
            if name == "nt_positions":
                return mock_pos_coll
            if name == "nt_signals":
                return mock_sig_coll
            c = MagicMock()
            c.create_index = AsyncMock()
            return c

        mock_db.__getitem__ = MagicMock(side_effect=_get_coll)
        return mock_db, mock_pos_coll, mock_sig_coll

    @staticmethod
    def _signal_doc(stocks):
        from bson import ObjectId

        return {
            "_id": ObjectId(),
            "stocks": stocks,
            "signal": "bullish",
            "sector": "Energy",
            "confidence": "high",
            "magnitude": "moderate",
            "reasoning": "test",
            "acted_on": False,
        }

    @staticmethod
    def _configure_settings(s, exclusion: bool):
        s.trading_mode = "paper"
        s.nt_max_positions = 10
        s.nt_max_stocks_per_signal = 2
        s.nt_max_positions_per_sector = 2
        s.nt_total_capital_inr = 100_000.0
        s.nt_sl_pct = 0.015
        s.nt_initial_sl_pct = 0.03
        s.nt_trail_sl_pct = 0.015
        s.nt_trail_activate_pct = 0.02
        s.nt_target_pct = 0.01
        s.nt_nifty50_exclusion = exclusion
        s.nt_bypass_market_hours = True
        s.nt_entry_cutoff_ist = 870
        s.telegram_bot_token = ""
        s.telegram_chat_id = ""

    @patch("src.scrapers.nse_market.fetch_nifty_vix_sync", return_value={})
    @patch("src.news_trader.prices.get_market_snapshot", return_value={"nifty50": None, "sector_index": None})
    @patch("handlers.trade_decision.get_db")
    @patch("src.news_trader.prices.get_ltps", return_value={"RELIANCE": 1500.0})
    @patch("handlers.trade_decision.alert_trade_entered")
    def test_all_nifty50_signal_rejected_with_gate_reason(
        self, mock_alert, mock_price, mock_get_db, mock_snapshot, _regime
    ):
        signal_doc = self._signal_doc(["RELIANCE"])
        mock_db, mock_pos_coll, mock_sig_coll = self._mock_db(signal_doc)
        mock_get_db.return_value = mock_db

        from handlers.trade_decision import handler
        event = _make_sqs_event([{"signal_id": str(signal_doc["_id"])}])

        with patch("handlers.trade_decision.Settings") as MockSettings:
            self._configure_settings(MockSettings.return_value, exclusion=True)
            result = handler(event, None)

        assert result["positions_opened"] == 0
        mock_pos_coll.insert_one.assert_not_called()
        # Rejection must be visible in the signal funnel as its own gate reason —
        # excluded signals are the zero-risk counterfactual cohort for validation.
        update = mock_sig_coll.update_one.call_args.args[1]
        assert update["$set"]["gate_result"] == "nifty50_excluded"

    @patch("src.scrapers.nse_market.fetch_nifty_vix_sync", return_value={})
    @patch("src.news_trader.prices.get_market_snapshot", return_value={"nifty50": None, "sector_index": None})
    @patch("handlers.trade_decision.get_db")
    @patch("src.news_trader.prices.get_ltps", return_value={"ONGC": 250.0, "OIL": 450.0})
    @patch("handlers.trade_decision.alert_trade_entered")
    def test_mixed_signal_trades_only_non_nifty50(
        self, mock_alert, mock_price, mock_get_db, mock_snapshot, _regime
    ):
        # ONGC is NIFTY50, OIL is not — only OIL should fill.
        signal_doc = self._signal_doc(["ONGC", "OIL"])
        mock_db, mock_pos_coll, _ = self._mock_db(signal_doc)
        mock_get_db.return_value = mock_db

        from handlers.trade_decision import handler
        event = _make_sqs_event([{"signal_id": str(signal_doc["_id"])}])

        with patch("handlers.trade_decision.Settings") as MockSettings:
            self._configure_settings(MockSettings.return_value, exclusion=True)
            result = handler(event, None)

        assert result["positions_opened"] == 1
        inserted = mock_pos_coll.insert_one.call_args.args[0]
        assert inserted["symbol"] == "OIL"

    @patch("src.scrapers.nse_market.fetch_nifty_vix_sync", return_value={})
    @patch("src.news_trader.prices.get_market_snapshot", return_value={"nifty50": None, "sector_index": None})
    @patch("handlers.trade_decision.get_db")
    @patch("src.news_trader.prices.get_ltps", return_value={"RELIANCE": 1500.0})
    @patch("handlers.trade_decision.alert_trade_entered")
    def test_flag_off_allows_nifty50(
        self, mock_alert, mock_price, mock_get_db, mock_snapshot, _regime
    ):
        signal_doc = self._signal_doc(["RELIANCE"])
        mock_db, mock_pos_coll, _ = self._mock_db(signal_doc)
        mock_get_db.return_value = mock_db

        from handlers.trade_decision import handler
        event = _make_sqs_event([{"signal_id": str(signal_doc["_id"])}])

        with patch("handlers.trade_decision.Settings") as MockSettings:
            self._configure_settings(MockSettings.return_value, exclusion=False)
            result = handler(event, None)

        assert result["positions_opened"] == 1
        inserted = mock_pos_coll.insert_one.call_args.args[0]
        assert inserted["symbol"] == "RELIANCE"
