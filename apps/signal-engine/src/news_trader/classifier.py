"""News classifier — calls the configured LLM and returns a structured signal.

Returns None on any failure so the caller can skip gracefully.

Output schema:
    {
        "sector": str,
        "signal": "bullish" | "bearish" | "neutral",
        "magnitude": "major" | "moderate" | "minor",
        "stocks": list[str],           # NSE symbols, max 5
        "confidence": "high" | "medium" | "low",
        "reasoning": str,
        "llm_model": str,              # model name used (e.g. "gemini-2.0-flash")
        "prompt_version": str,         # semver — bump when _SYSTEM prompt changes
    }
"""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import Settings
from src.providers.llm_factory import get_llm

logger = logging.getLogger(__name__)

# Fallback used when Settings is unavailable (tests, one-off scripts).
# The canonical value lives in Settings.nt_classifier_prompt_version (env: NT_CLASSIFIER_PROMPT_VERSION).
_DEFAULT_PROMPT_VERSION = "1.0.0"

_SYSTEM = """You are a senior Indian stock market analyst with deep expertise in NSE/BSE listed companies.

Given a news headline and body, output ONLY a JSON object (no markdown, no explanation) with:
- "sector": affected BSE/NSE sector name (e.g. "Banking", "IT", "Auto", "Pharma", "Energy")
- "signal": "bullish", "bearish", or "neutral"
- "magnitude": "major" (likely >2% move), "moderate" (0.5-2%), or "minor" (<0.5%)
- "stocks": list of NSE trading symbols most likely to be affected (e.g. ["HDFCBANK", "SBIN"]), max 5, empty list if none identified
- "confidence": "high" (clear direct impact), "medium" (probable impact), or "low" (speculative)
- "reasoning": one sentence explaining the signal

Rules:
- Only include stocks actually traded on NSE (use official NSE symbols without .NS suffix)
- Output ONLY the JSON object, no other text
- If the news is generic market noise with no specific sector impact, set confidence to "low"
"""

_HUMAN_TMPL = "NEWS:\n{text}"


def classify(raw_text: str, settings: Settings) -> dict[str, Any] | None:
    """Classify a news article. Returns parsed dict or None on failure."""
    try:
        llm = get_llm(settings=settings)
        llm_model = _resolve_model_name(settings)
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=_HUMAN_TMPL.format(text=raw_text[:2000])),
        ]
        response = llm.invoke(messages)
        raw = response.content if isinstance(response.content, str) else str(response.content)
        raw = raw.strip()
        logger.info("[LLM] raw response (first 300 chars): %s", raw[:300])

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result: dict[str, Any] = json.loads(raw)

        # Validate required keys
        required = {"sector", "signal", "magnitude", "stocks", "confidence", "reasoning"}
        if not required.issubset(result.keys()):
            logger.warning("[LLM] incomplete response — missing keys, got: %s", list(result.keys()))
            return None

        # Normalise — guard against LLM returning null for these fields
        result["signal"] = str(result["signal"] or "neutral").lower()
        result["confidence"] = str(result["confidence"] or "low").lower()
        result["magnitude"] = str(result["magnitude"] or "minor").lower()
        result["stocks"] = [s.upper().strip() for s in result.get("stocks", [])[:5]]

        result["llm_model"] = llm_model
        result["prompt_version"] = getattr(settings, "nt_classifier_prompt_version", _DEFAULT_PROMPT_VERSION)

        return result

    except json.JSONDecodeError as exc:
        logger.warning("[LLM] JSON parse failed err=%s raw=%.200s", exc, raw if 'raw' in dir() else '?')
        # Partial-recovery: salvage scalar fields from truncated JSON (e.g. stocks array cut off)
        recovered = _partial_parse(raw if 'raw' in dir() else '')
        if recovered:
            logger.info("[LLM] partial parse succeeded — recovered fields: %s", list(recovered.keys()))
            return recovered
        return None
    except Exception as exc:
        logger.error("[LLM] unexpected error err=%s", exc)
        return None


def _resolve_model_name(settings: Settings) -> str:
    """Return the actual model string for the active provider."""
    p = settings.ai_provider.lower()
    mapping = {
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "kimi": settings.kimi_model,
        "deepseek": settings.deepseek_model,
        "ollama": settings.ollama_model,
    }
    return mapping.get(p, p)
