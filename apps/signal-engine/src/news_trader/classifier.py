"""News classifier — calls the configured LLM and returns a structured signal.

Returns None when the LLM response can't be parsed or validated (not an error).
Raises LLMProviderError when the provider call itself fails (HTTP error, auth, quota).

Output schema:
    {
        "sector": str,
        "signal": "bullish" | "bearish" | "neutral",
        "magnitude": "major" | "moderate" | "minor",
        "stocks": list[str],           # NSE symbols, max 5
        "confidence": "high" | "medium" | "low",
        "reasoning": str,
        "event_type": str,             # see _EVENT_TYPE_TAXONOMY below
        "llm_model": str,              # model name used (e.g. "gemini-2.5-flash")
        "prompt_version": str,         # semver — bump when _SYSTEM prompt changes
    }

event_type taxonomy (Group A = information events; Group B = noise/control):
    Group A: m_and_a, earnings, order_win, regulatory, capital_action
    Group B: rating_analyst, generic_pr, macro_sector, management, other
"""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import Settings
from src.providers.llm_factory import get_llm

logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    """Raised when the LLM provider call fails (HTTP error, auth failure, quota, etc.)."""


# Fallback used when Settings is unavailable (tests, one-off scripts).
# The canonical value lives in Settings.nt_classifier_prompt_version (env: NT_CLASSIFIER_PROMPT_VERSION).
_DEFAULT_PROMPT_VERSION = "1.1.0"

# Valid event_type values. Any value the LLM returns outside this set is coerced to "other".
# Group A (information events — hypothesis: drifts post-signal):
#   m_and_a, earnings, order_win, regulatory, capital_action
# Group B (noise/control — hypothesis: no drift):
#   rating_analyst, generic_pr, macro_sector, management, other
_EVENT_TYPE_TAXONOMY = frozenset({
    "m_and_a", "earnings", "order_win", "regulatory", "capital_action",
    "rating_analyst", "generic_pr", "macro_sector", "management", "other",
})

_SYSTEM = """You are a senior Indian stock market analyst with deep expertise in NSE/BSE listed companies.

Given a news headline and body, output ONLY a JSON object (no markdown, no explanation) with:
- "sector": affected BSE/NSE sector name (e.g. "Banking", "IT", "Auto", "Pharma", "Energy")
- "signal": "bullish", "bearish", or "neutral"
- "magnitude": "major" (likely >2% move), "moderate" (0.5-2%), or "minor" (<0.5%)
- "stocks": list of NSE trading symbols most likely to be affected (e.g. ["HDFCBANK", "SBIN"]), max 5, empty list if none identified
- "confidence": "high" (clear direct impact), "medium" (probable impact), or "low" (speculative)
- "reasoning": one sentence explaining the signal
- "event_type": one of the following values describing the nature of the news event:
    "m_and_a"        — merger, acquisition, takeover bid, open offer, stake sale
    "earnings"       — quarterly/annual results, revenue, profit, EPS announcement or surprise
    "order_win"      — new contract win, order receipt, project award, deal closure
    "regulatory"     — SEBI/RBI/government ruling, policy change, licence grant/revocation, penalty, court order
    "capital_action" — buyback, rights issue, dividend, bonus issue, QIP, fundraise
    "rating_analyst" — broker upgrade/downgrade, target price change, analyst note
    "generic_pr"     — product launch, MOU/partnership, rebranding, CSR, awards, general corporate PR
    "macro_sector"   — sector-wide trend, commodity price, index movement, macro data (no single stock impact)
    "management"     — CEO/CFO/board change, promoter activity, ESOP
    "other"          — anything that does not fit the above categories

Rules:
- Only include stocks actually traded on NSE (use official NSE symbols without .NS suffix)
- Output ONLY the JSON object, no other text
- If the news is generic market noise with no specific sector impact, set confidence to "low"
"""

_HUMAN_TMPL = "NEWS:\n{text}"

# Defensive upper bound on article body sent to the LLM. The scrapers already
# cap raw_text at 1000 chars (rss.py / nse.py / bse.py), so this is a belt-and-
# suspenders guard, not a cost lever — at 1000 it truncates nothing in practice.
# The headline + lead carry the classification signal; lower this only if a
# future source starts emitting much longer bodies and token cost matters.
_MAX_ARTICLE_CHARS = 1000


def _partial_parse(raw: str) -> dict[str, Any] | None:
    """Salvage scalar fields from a truncated JSON response.

    When the LLM hits a token limit mid-array (e.g. stocks list cut off), the
    scalar fields (signal, confidence, magnitude, sector) are still usable.
    Returns a valid result dict with stocks=[] if all required scalars are present.
    """
    result: dict[str, Any] = {}
    for field in ("sector", "signal", "magnitude", "confidence", "reasoning", "event_type"):
        m = re.search(rf'"{field}"\s*:\s*"([^"]*)"', raw)
        if m:
            result[field] = m.group(1)
    required = {"sector", "signal", "magnitude", "confidence", "reasoning"}
    if not required.issubset(result.keys()):
        return None
    if "event_type" not in result:
        result["event_type"] = "other"
    # Try to extract stocks if present, else fall back to empty
    stocks_m = re.search(r'"stocks"\s*:\s*\[([^\]]*)', raw)
    if stocks_m:
        raw_stocks = stocks_m.group(1)
        # Extract quoted strings that are complete (have both opening and closing quote)
        result["stocks"] = [s.upper().strip() for s in re.findall(r'"([^"]+)"', raw_stocks)]
    else:
        result["stocks"] = []
    return result


def classify(raw_text: str, settings: Settings) -> dict[str, Any] | None:
    """Classify a news article. Returns parsed dict or None on failure."""
    try:
        llm = get_llm(settings=settings)
        llm_model = _resolve_model_name(settings)
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=_HUMAN_TMPL.format(text=raw_text[:_MAX_ARTICLE_CHARS])),
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
        required = {"sector", "signal", "magnitude", "stocks", "confidence", "reasoning", "event_type"}
        if not required.issubset(result.keys()):
            logger.warning("[LLM] incomplete response — missing keys, got: %s", list(result.keys()))
            return None

        # Normalise — guard against LLM returning null for these fields
        result["signal"] = str(result["signal"] or "neutral").lower()
        result["confidence"] = str(result["confidence"] or "low").lower()
        result["magnitude"] = str(result["magnitude"] or "minor").lower()
        result["stocks"] = [s.upper().strip() for s in result.get("stocks", [])[:5]]
        event_type = str(result.get("event_type") or "other").lower()
        result["event_type"] = event_type if event_type in _EVENT_TYPE_TAXONOMY else "other"

        result["llm_model"] = llm_model
        result["prompt_version"] = getattr(settings, "nt_classifier_prompt_version", _DEFAULT_PROMPT_VERSION)

        return result

    except json.JSONDecodeError as exc:
        logger.warning("[LLM] JSON parse failed err=%s raw=%.200s", exc, raw if 'raw' in dir() else '?')
        # Partial-recovery: salvage scalar fields from truncated JSON (e.g. stocks array cut off)
        recovered = _partial_parse(raw if 'raw' in dir() else '')
        if recovered:
            logger.info("[LLM] partial parse succeeded — recovered fields: %s", list(recovered.keys()))
            recovered["signal"] = str(recovered["signal"] or "neutral").lower()
            recovered["confidence"] = str(recovered["confidence"] or "low").lower()
            recovered["magnitude"] = str(recovered["magnitude"] or "minor").lower()
            et = str(recovered.get("event_type") or "other").lower()
            recovered["event_type"] = et if et in _EVENT_TYPE_TAXONOMY else "other"
            recovered["llm_model"] = llm_model
            recovered["prompt_version"] = getattr(settings, "nt_classifier_prompt_version", _DEFAULT_PROMPT_VERSION)
            return recovered
        return None
    except Exception as exc:
        logger.error("[LLM] unexpected error err=%s", exc)
        raise LLMProviderError(str(exc)) from exc


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
