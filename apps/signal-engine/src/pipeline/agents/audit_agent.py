"""audit_agent — finalize pipeline_run in MongoDB and fire alert webhook.

Phase 2: writes pipeline_run status (completed/partial/failed) and posts
a summary JSON to ALERT_WEBHOOK_URL if configured.
Full alert formatting: Phase 8.
"""

import json
import logging

import httpx

from .base import BaseAgent
from ..state import TradingState
from ...config import Settings
from ...db.client import get_db
from ...db.repositories.pipeline_runs import PipelineRunsRepository

logger = logging.getLogger(__name__)


class AuditAgent(BaseAgent):
    name = "audit_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        repo = PipelineRunsRepository(get_db())

        has_errors = bool(state.errors)
        has_signals = bool(state.signals)

        if has_errors and not has_signals:
            final_status = "failed"
        elif has_errors:
            final_status = "partial"
        else:
            final_status = "completed"

        stats = {
            "news_count": len(state.raw_news),
            "signals_count": len(state.signals),
            "orders_count": len(state.orders),
            "errors_count": len(state.errors),
        }
        error_summary = "; ".join(state.errors) if state.errors else None

        await repo.finalize(state.run_id, final_status, stats, error_summary)

        settings = Settings()
        if settings.alert_webhook_url and has_signals:
            await _post_webhook(settings.alert_webhook_url, state)

        return state


async def _post_webhook(url: str, state: TradingState) -> None:
    payload = {
        "run_id": state.run_id,
        "date": state.date,
        "signals": len(state.signals),
        "orders": len(state.orders),
        "errors": state.errors,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        logger.warning("webhook post failed: %s", exc)


audit_agent = AuditAgent()
