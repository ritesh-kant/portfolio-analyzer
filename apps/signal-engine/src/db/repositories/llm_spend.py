"""Daily LLM spend tracker — prevents runaway token costs."""

from motor.motor_asyncio import AsyncIOMotorDatabase

from .base import BaseRepository


class LlmSpendRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        super().__init__(db, "llm_daily_spend")

    async def get_daily_cost(self, day: str) -> float:
        doc = await self.find_one({"day": day})
        return float(doc["cost_usd"]) if doc else 0.0

    async def record_usage(self, day: str, tokens: int, cost_usd: float) -> None:
        await self.update_one(
            {"day": day},
            {"$inc": {"tokens": tokens, "cost_usd": cost_usd}, "$set": {"day": day}},
            upsert=True,
        )
