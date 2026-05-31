"""Motor async MongoDB client — single shared instance per process."""

import asyncio

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from src.config import Settings

_client: AsyncIOMotorClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None
_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_client() -> AsyncIOMotorClient:
    global _client, _client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _client is None or current_loop is not _client_loop:
        if _client is not None:
            _client.close()
        s = _get_settings()
        _client = AsyncIOMotorClient(s.mongodb_uri)
        _client_loop = current_loop
    return _client


def get_db() -> AsyncIOMotorDatabase:
    s = _get_settings()
    return get_client()[s.mongodb_db_name]


async def close_client() -> None:
    global _client, _client_loop
    if _client is not None:
        _client.close()
        _client = None
        _client_loop = None
