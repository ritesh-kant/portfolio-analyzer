"""Shared utilities for LLM invocation and response parsing."""

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# Conservative per-token cost covering Sonnet / GPT-4o tier pricing
_APPROX_COST_PER_TOKEN_USD = 3e-6


def extract_json(text: str) -> Any:
    """Extract the first valid JSON object or array from an LLM response."""
    text = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for start_char, end_char in [("[", "]"), ("{", "}")]:
        idx = text.find(start_char)
        if idx == -1:
            continue
        depth = 0
        for i in range(idx, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[idx : i + 1])
                    except json.JSONDecodeError:
                        break

    logger.warning("could not extract JSON from LLM response: %.200s", text)
    return None


async def _guard_spend(system_prompt: str, user_prompt: str) -> None:
    """Check daily LLM spend limit and record estimated usage. Raises RuntimeError if limit hit."""
    try:
        from ..config import Settings
        from ..db.client import get_db
        from ..db.repositories.llm_spend import LlmSpendRepository

        settings = Settings()
        repo = LlmSpendRepository(get_db())
        today = date.today().isoformat()

        daily_cost = await repo.get_daily_cost(today)
        if daily_cost >= settings.llm_daily_spend_limit_usd:
            raise RuntimeError(
                f"Daily LLM spend limit ${settings.llm_daily_spend_limit_usd:.2f} reached "
                f"(current: ${daily_cost:.4f}). No further LLM calls today."
            )

        approx_tokens = (len(system_prompt) + len(user_prompt)) // 4
        await repo.record_usage(today, approx_tokens, approx_tokens * _APPROX_COST_PER_TOKEN_USD)
    except RuntimeError:
        raise
    except Exception as exc:
        # Never let spend tracking break the pipeline — log and continue
        logger.warning("llm_spend_guard_failed: %s", exc)


async def call_llm_json(llm: Any, system_prompt: str, user_prompt: str) -> Any:
    """Invoke LLM and return parsed JSON, or None on failure.

    Applies two safety layers:
    - asyncio.wait_for timeout (configurable via LLM_TIMEOUT_S, default 30s)
    - daily spend circuit breaker (configurable via LLM_DAILY_SPEND_LIMIT_USD)
    """
    try:
        from ..config import Settings
        timeout_s = Settings().llm_timeout_s
    except Exception:
        timeout_s = 30.0

    try:
        await _guard_spend(system_prompt, user_prompt)
        response = await asyncio.wait_for(
            llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            ),
            timeout=timeout_s,
        )
        return extract_json(response.content)
    except RuntimeError as exc:
        logger.warning("LLM call blocked by spend guard: %s", exc)
        return None
    except asyncio.TimeoutError:
        logger.warning("LLM call timed out after %.0fs", timeout_s)
        return None
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None
