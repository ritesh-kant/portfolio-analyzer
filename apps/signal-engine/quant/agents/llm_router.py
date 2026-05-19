"""LLM provider router — Deepseek V3 primary, Gemini 2.5 Pro fallback.

Plan §6 + §6.1: never single-point the pipeline on one LLM provider.
Deepseek has known regional outages and rate limits during peak hours;
the failover is wired in from day one.

Provider cost tiers (rough, 2026-05):
  Deepseek V3:     ~$0.14 / 1M input tokens  (primary — extraction / classification)
  Deepseek R1:     ~$0.55 / 1M input tokens  (reasoning tasks — critic, counterfactual)
  Gemini 2.5 Flash: ~$0.15 / 1M              (failover for V3 tasks)
  Gemini 2.5 Pro:   ~$1.25 / 1M             (failover for R1 tasks; long-context PDFs)

Hard rule (plan §6): No LLM output is wired directly into position sizing.
LLMs produce features only; the ML model + Bayesian posterior do the sizing.

Environment variables required:
  DEEPSEEK_API_KEY    — from platform.deepseek.com
  GOOGLE_API_KEY      — from aistudio.google.com

If both are unset, router raises RuntimeError (cannot silently degrade —
caller decides whether to use mock output for tests).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Literal

import requests

logger = logging.getLogger(__name__)

Role = Literal["parser", "classifier", "critic", "narrator", "postmortem", "counterfactual"]

_DEEPSEEK_BASE = "https://api.deepseek.com/v1"
_DEEPSEEK_CHAT = f"{_DEEPSEEK_BASE}/chat/completions"

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

_ROLE_MODELS: dict[Role, dict[str, str]] = {
    "parser":        {"deepseek": "deepseek-chat",     "gemini": "gemini-2.5-pro"},
    "classifier":    {"deepseek": "deepseek-chat",     "gemini": "gemini-2.5-flash"},
    "critic":        {"deepseek": "deepseek-reasoner", "gemini": "gemini-2.5-pro"},
    "narrator":      {"deepseek": "deepseek-chat",     "gemini": "gemini-2.5-flash"},
    "postmortem":    {"deepseek": "deepseek-chat",     "gemini": "gemini-2.5-flash"},
    "counterfactual": {"deepseek": "deepseek-reasoner", "gemini": "gemini-2.5-pro"},
}

_MAX_RETRIES = 2
_RETRY_DELAY = 2.0


class LLMError(Exception):
    """Raised when all providers fail."""


def call(
    role: Role,
    system: str,
    user: str,
    response_schema: dict[str, Any] | None = None,
    temperature: float = 0.1,
) -> dict[str, Any] | str:
    """Call the appropriate LLM for a given role with provider failover.

    Parameters
    ----------
    role : Role
        Determines which model variant to use (parser → V3, critic → R1, etc.)
    system : str
        System prompt.
    user : str
        User prompt (may contain the document text or structured input).
    response_schema : dict | None
        If set, enforces JSON output and parses the response.
        The caller should pass a JSON Schema dict; the router adds a
        "respond in JSON following this schema" instruction.
    temperature : float
        0.1 for extraction tasks; 0.0 for classification.

    Returns
    -------
    dict  — if response_schema is set (parsed JSON)
    str   — if response_schema is None (raw text)

    Raises
    ------
    LLMError
        If all provider attempts fail.
    """
    if response_schema is not None:
        schema_instruction = (
            "\n\nRespond ONLY with valid JSON matching this schema (no markdown):\n"
            + json.dumps(response_schema, indent=2)
        )
        system = system + schema_instruction

    last_exc: Exception | None = None

    for provider in ("deepseek", "gemini"):
        for attempt in range(_MAX_RETRIES):
            try:
                if provider == "deepseek":
                    raw = _call_deepseek(role, system, user, temperature)
                else:
                    raw = _call_gemini(role, system, user, temperature)

                if response_schema is not None:
                    return _parse_json(raw)
                return raw

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "LLM call failed provider=%s role=%s attempt=%d/%d: %s",
                    provider, role, attempt + 1, _MAX_RETRIES, exc,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY)

        logger.warning("Switching from %s to next provider (role=%s)", provider, role)

    raise LLMError(
        f"All LLM providers failed for role={role!r}. Last error: {last_exc}"
    )


def _call_deepseek(
    role: Role,
    system: str,
    user: str,
    temperature: float,
) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")

    model = _ROLE_MODELS[role]["deepseek"]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": 4096,
    }

    resp = requests.post(
        _DEEPSEEK_CHAT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_gemini(
    role: Role,
    system: str,
    user: str,
    temperature: float,
) -> str:
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    model = _ROLE_MODELS[role]["gemini"]
    url = f"{_GEMINI_BASE}/models/{model}:generateContent?key={api_key}"

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 4096,
        },
    }

    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _parse_json(raw: str) -> dict[str, Any]:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove opening ``` and closing ```
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end])
    return json.loads(text)
