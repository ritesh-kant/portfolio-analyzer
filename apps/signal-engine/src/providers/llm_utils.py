"""Shared utilities for LLM invocation and response parsing."""

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# Per-provider cost rates (USD per token).
# Input tokens dominate pipeline prompts; output is typically short JSON.
# Rates are approximate — use for circuit-breaker budgeting, not billing.
#
# Sources (May 2025):
#   Anthropic  — Sonnet: $3/1M in, $15/1M out
#   OpenAI     — GPT-4o: $2.5/1M in, $10/1M out
#   Gemini     — 2.5 Flash: $0.30/1M in, $2.50/1M out (thinking billed as output)
#   DeepSeek   — R1 (ceiling, uncached): $2.19/1M in, $8.19/1M out
#                V3 (deepseek-chat) is ~2x cheaper, but we don't know the model at runtime
#   NVIDIA/Kimi — billed via OpenAI-compatible endpoint; use OpenAI rates as ceiling
#   Ollama     — local inference, no API cost
_PROVIDER_RATES: dict[str, tuple[float, float]] = {
    # class-name prefix → (cost_per_input_token, cost_per_output_token)
    "ChatAnthropic":              (3e-6,    15e-6),
    "ChatOpenAI":                 (2.5e-6,  10e-6),
    "ChatGoogleGenerativeAI":     (0.3e-6,   2.5e-6),
    "ChatDeepSeek":               (2.19e-6,  8.19e-6),  # R1 ceiling; V3 is cheaper
    "ChatOllama":                 (0.0,     0.0),        # local — always free
}

# Fallback for unknown providers: use Anthropic Sonnet rates (highest realistic cost)
# so the circuit breaker errs on the side of caution.
_FALLBACK_RATE: tuple[float, float] = (3e-6, 15e-6)

# Rough output budget assumption when actual output length is unknown pre-call.
# Most pipeline responses are short JSON objects (< 200 tokens).
_ASSUMED_OUTPUT_TOKENS = 150


def _estimate_cost(llm: Any, input_chars: int) -> float:
    """Estimate USD cost for one LLM call given input character count."""
    class_name = type(llm).__name__
    input_rate, output_rate = _FALLBACK_RATE
    for prefix, rates in _PROVIDER_RATES.items():
        if class_name.startswith(prefix):
            input_rate, output_rate = rates
            break
    else:
        logger.debug("llm_spend: unknown provider class %r — using fallback rate", class_name)

    input_tokens = input_chars // 4
    return input_tokens * input_rate + _ASSUMED_OUTPUT_TOKENS * output_rate


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


async def _guard_spend(llm: Any, system_prompt: str, user_prompt: str) -> None:
    """Check daily LLM spend limit and record estimated usage.

    Cost is estimated per-provider using _PROVIDER_RATES. Ollama (local) is
    always free and never counted. Raises RuntimeError if the daily limit is hit.
    """
    estimated_cost = _estimate_cost(llm, len(system_prompt) + len(user_prompt))
    if estimated_cost == 0.0:
        return  # free provider (Ollama) — skip DB entirely

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
                f"(current: ${daily_cost:.4f}, provider: {type(llm).__name__}). "
                "No further LLM calls today."
            )

        approx_tokens = (len(system_prompt) + len(user_prompt)) // 4 + _ASSUMED_OUTPUT_TOKENS
        await repo.record_usage(today, approx_tokens, estimated_cost)
    except RuntimeError:
        raise
    except Exception as exc:
        # Never let spend tracking break the pipeline — log and continue
        logger.warning("llm_spend_guard_failed: %s", exc)


async def call_llm_json(llm: Any, system_prompt: str, user_prompt: str) -> Any:
    """Invoke LLM and return parsed JSON, or None on failure.

    Applies two safety layers:
    - asyncio.wait_for timeout (configurable via LLM_TIMEOUT_S, default 30s)
    - daily spend circuit breaker (configurable via LLM_DAILY_SPEND_LIMIT_USD),
      with per-provider cost rates so Ollama (free) is never blocked
    """
    try:
        from ..config import Settings
        timeout_s = Settings().llm_timeout_s
    except Exception:
        timeout_s = 30.0

    try:
        await _guard_spend(llm, system_prompt, user_prompt)
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
