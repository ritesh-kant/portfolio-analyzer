"""Shared utilities for LLM invocation and response parsing."""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


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


async def call_llm_json(llm: Any, system_prompt: str, user_prompt: str) -> Any:
    """Invoke LLM and return parsed JSON, or None on failure."""
    try:
        response = await llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        return extract_json(response.content)
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None
