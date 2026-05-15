"""Repository for trading_news_articles collection."""

from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.db.constants import COLLECTION_NAMES

from .base import BaseRepository


class NewsArticlesRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
        super().__init__(db, COLLECTION_NAMES["NEWS_ARTICLES"])

    async def exists_by_hash(self, topic_hash: str) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        doc = await self._col.find_one(
            {"topic_hash": topic_hash, "createdAt": {"$gte": cutoff}}
        )
        return doc is not None

    async def insert_article(self, article: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        article.setdefault("createdAt", now)
        article.setdefault("updatedAt", now)
        return await self.insert_one(article)

    async def bulk_insert(self, articles: list[dict[str, Any]]) -> int:
        if not articles:
            return 0
        now = datetime.now(timezone.utc)
        for a in articles:
            a.setdefault("createdAt", now)
            a.setdefault("updatedAt", now)
        result = await self._col.insert_many(articles)
        return len(result.inserted_ids)
