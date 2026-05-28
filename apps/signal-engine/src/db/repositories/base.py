"""Base repository providing typed collection access."""

from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase


class BaseRepository:
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str) -> None:
        self._col: AsyncIOMotorCollection = db[collection_name]

    @property
    def col(self) -> AsyncIOMotorCollection:
        return self._col

    async def insert_one(self, doc: dict[str, Any]) -> str:
        result = await self._col.insert_one(doc)
        return str(result.inserted_id)

    async def find_one(self, filter: dict[str, Any]) -> dict[str, Any] | None:
        return await self._col.find_one(filter)

    async def update_one(
        self,
        filter: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> None:
        await self._col.update_one(filter, update, upsert=upsert)
