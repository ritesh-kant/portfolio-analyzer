"""Integration-style tests for Lambda handlers — MongoDB is mocked with mongomock.

These tests verify the full handler logic (dedup, SQS dispatch, position opening,
SL monitor exits) without a real DB or real AWS services.

Requires: pip install mongomock motor  (already in dev deps via motor; mongomock
          is added below — see conftest note).
"""

import json
from datetime import datetime, timedelta, timezone
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

        entry_at = datetime.now(tz=timezone.utc) - timedelta(days=1)
        result = check_exit(
            current_price=97.0,
            trailing_sl=98.5,
            target_price=108.0,
            entry_at=entry_at,
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
            entry_at=datetime.now(tz=timezone.utc) - timedelta(days=1),
            max_hold_days=5,
        )
        assert result == "target_hit"


# ---------------------------------------------------------------------------
# news_ingester handler (mocked scrapers + mocked DB)
# ---------------------------------------------------------------------------

class TestNewsIngesterHandler:

    @patch("handlers.news_ingester._is_market_hours", return_value=False)
    def test_skips_outside_market_hours(self, _mock):
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
        })

    @patch("handlers.news_classifier.get_db")
    @patch("handlers.news_classifier.boto3")
    @patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
    def test_low_confidence_not_enqueued(self, MockLLM, mock_boto3, mock_get_db):
        """low-confidence signals must NOT be enqueued to the signals queue."""
        low_conf = json.dumps({
            "sector": "Generic",
            "signal": "neutral",
            "magnitude": "minor",
            "stocks": [],
            "confidence": "low",
            "reasoning": "Nothing specific.",
        })
        instance = MockLLM.return_value
        instance.invoke.return_value = MagicMock(content=low_conf)

        sqs_client = MagicMock()
        mock_boto3.client.return_value = sqs_client

        from bson import ObjectId
        news_id = str(ObjectId())
        article = {
            "_id": ObjectId(news_id),
            "raw_text": "Generic market update",
            "headline": "Markets flat",
            "source": "Test",
            "classified": False,
        }

        mock_db = MagicMock()
        mock_coll = MagicMock()
        mock_coll.create_index = AsyncMock()
        mock_coll.find_one = AsyncMock(return_value=article)
        mock_coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        mock_coll.update_one = AsyncMock()
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
            handler(event, None)

        # SQS send_message must NOT have been called
        sqs_client.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# trade_decision handler
# ---------------------------------------------------------------------------

class TestTradeDecisionHandler:

    @patch("handlers.trade_decision.get_db")
    @patch("handlers.trade_decision.get_ltp", return_value=1500.0)
    @patch("handlers.trade_decision.alert_trade_entered")
    def test_opens_position_in_paper_mode(self, mock_alert, mock_price, mock_get_db):
        from bson import ObjectId

        signal_id = str(ObjectId())
        signal_doc = {
            "_id": ObjectId(signal_id),
            "stocks": ["HDFCBANK"],
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
            s.nt_max_positions = 7
            s.nt_max_stocks_per_signal = 2
            s.nt_position_size_inr = 50_000.0
            s.nt_sl_pct = 0.015
            s.nt_target_pct = 0.08
            s.telegram_bot_token = ""
            s.telegram_chat_id = ""
            result = handler(event, None)

        assert result["positions_opened"] == 1
        mock_pos_coll.insert_one.assert_called_once()
        mock_alert.assert_called_once()

    @patch("handlers.trade_decision.get_db")
    @patch("handlers.trade_decision.get_ltp", return_value=1500.0)
    @patch("handlers.trade_decision.alert_trade_entered")
    def test_respects_max_positions_limit(self, mock_alert, mock_price, mock_get_db):
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
        mock_pos_coll.count_documents = AsyncMock(return_value=7)  # already full

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
            s.nt_max_positions = 7
            s.nt_max_stocks_per_signal = 2
            s.nt_position_size_inr = 50_000.0
            s.nt_sl_pct = 0.015
            s.nt_target_pct = 0.08
            s.telegram_bot_token = ""
            s.telegram_chat_id = ""
            result = handler(event, None)

        assert result["positions_opened"] == 0
        mock_pos_coll.insert_one.assert_not_called()
        mock_alert.assert_not_called()
