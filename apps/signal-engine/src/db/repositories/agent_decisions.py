"""Repository for trading_agent_decisions collection."""

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class AgentDecisionsRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["AGENT_DECISIONS"])

    async def record(
        self,
        run_id: str,
        agent: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        input_summary: str,
        output_summary: str,
        reasoning: str | None = None,
    ) -> None:
        doc: dict[str, Any] = {
            "run_id": run_id,
            "agent": agent,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "latency_ms": latency_ms,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "createdAt": datetime.now(timezone.utc),
        }
        if reasoning:
            doc["reasoning"] = reasoning
        await self.insert_one(doc)
