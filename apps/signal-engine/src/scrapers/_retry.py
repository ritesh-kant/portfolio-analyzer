"""_retry.py — async exponential backoff for HTTP scrapers.

Usage:
    result = await with_retry(my_async_fn, max_attempts=3, base_delay=1.0, label="source:name")
    if result is None:
        return []  # caller decides the fallback
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.5,
    label: str = "",
) -> T | None:
    """Run `fn` up to max_attempts times with exponential backoff.

    Delay after attempt k = min(base_delay * 2^(k-1) + uniform(0, jitter), max_delay).
    Returns None if all attempts fail — never raises.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = min(
                base_delay * (2 ** (attempt - 1)) + random.uniform(0, jitter),
                max_delay,
            )
            logger.warning(
                "retry_backoff label=%s attempt=%d/%d error=%s delay=%.1fs",
                label,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    logger.warning(
        "retry_exhausted label=%s attempts=%d error=%s",
        label,
        max_attempts,
        last_exc,
    )
    return None
