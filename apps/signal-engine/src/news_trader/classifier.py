"""Gemini 1.5 Flash news classifier.

Calls Gemini with a structured prompt and parses the JSON response.
Returns None on any failure so the caller can skip gracefully.

Output schema (matches plan spec):
    {
        "sector": str,
        "signal": "bullish" | "bearish" | "neutral",
        "magnitude": "major" | "moderate" | "minor",
        "stocks": list[str],           # NSE symbols, max 5
        "confidence": "high" | "medium" | "low",
        "reasoning": str
    }
"""

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import Settings
from src.providers.llm_factory import get_llm

logger = logging.getLogger(__name__)

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

        # Normalise
        result["signal"] = result["signal"].lower()
        result["confidence"] = result["confidence"].lower()
        result["magnitude"] = result["magnitude"].lower()
        result["stocks"] = [s.upper().strip() for s in result.get("stocks", [])[:5]]

        return result

    except json.JSONDecodeError as exc:
        logger.warning("[LLM] JSON parse failed err=%s raw=%.200s", exc, raw if 'raw' in dir() else '?')
        return None
    except Exception as exc:
        logger.error("[LLM] unexpected error err=%s", exc)
        return None
