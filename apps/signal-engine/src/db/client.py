"""Motor async MongoDB client — single shared instance per process."""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from src.config import Settings

_client: AsyncIOMotorClient | None = None
_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        s = _get_settings()
        _client = AsyncIOMotorClient(s.mongodb_uri)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    s = _get_settings()
    return get_client()[s.mongodb_db_name]


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
