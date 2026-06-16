"""Tests for the news classifier — all LLM calls are mocked via get_llm."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings
from src.news_trader.classifier import LLMProviderError, classify


def _settings() -> Settings:
    return Settings(ai_provider="ollama")


def _mock_llm_response(content: str):
    msg = MagicMock()
    msg.content = content
    return msg


def _patched_llm(mock_get_llm: MagicMock, content: str) -> None:
    mock_get_llm.return_value.invoke.return_value = _mock_llm_response(content)


VALID_RESPONSE = json.dumps({
    "sector": "Banking",
    "signal": "bullish",
    "magnitude": "major",
    "stocks": ["HDFCBANK", "SBIN", "ICICIBANK"],
    "confidence": "high",
    "reasoning": "RBI rate cut improves net interest margins for lenders.",
    "event_type": "regulatory",
})


@patch("src.news_trader.classifier.get_llm")
def test_classify_returns_dict_on_valid_response(mock_get_llm):
    _patched_llm(mock_get_llm, VALID_RESPONSE)

    result = classify("RBI cuts repo rate by 25bps", _settings())

    assert result is not None
    assert result["sector"] == "Banking"
    assert result["signal"] == "bullish"
    assert result["confidence"] == "high"
    assert "HDFCBANK" in result["stocks"]


@patch("src.news_trader.classifier.get_llm")
def test_classify_strips_markdown_fences(mock_get_llm):
    fenced = f"```json\n{VALID_RESPONSE}\n```"
    _patched_llm(mock_get_llm, fenced)

    result = classify("some headline", _settings())
    assert result is not None
    assert result["signal"] == "bullish"


@patch("src.news_trader.classifier.get_llm")
def test_classify_uppercases_stock_symbols(mock_get_llm):
    response = json.dumps({
        "sector": "IT",
        "signal": "bearish",
        "magnitude": "moderate",
        "stocks": ["infy", "tcs", "wipro"],
        "confidence": "medium",
        "reasoning": "US recession fears hit IT exports.",
        "event_type": "earnings",
    })
    _patched_llm(mock_get_llm, response)

    result = classify("IT sector faces headwinds", _settings())
    assert result is not None
    assert result["stocks"] == ["INFY", "TCS", "WIPRO"]


@patch("src.news_trader.classifier.get_llm")
def test_classify_returns_none_on_bad_json(mock_get_llm):
    _patched_llm(mock_get_llm, "not json at all")

    result = classify("some headline", _settings())
    assert result is None


@patch("src.news_trader.classifier.get_llm")
def test_classify_returns_none_on_missing_keys(mock_get_llm):
    incomplete = json.dumps({"sector": "Banking", "signal": "bullish"})
    _patched_llm(mock_get_llm, incomplete)

    result = classify("some headline", _settings())
    assert result is None


@patch("src.news_trader.classifier.get_llm")
def test_classify_truncates_stocks_to_5(mock_get_llm):
    response = json.dumps({
        "sector": "Banking",
        "signal": "bullish",
        "magnitude": "minor",
        "stocks": ["A", "B", "C", "D", "E", "F", "G"],
        "confidence": "low",
        "reasoning": "test",
        "event_type": "generic_pr",
    })
    _patched_llm(mock_get_llm, response)

    result = classify("some headline", _settings())
    assert result is not None
    assert len(result["stocks"]) == 5


@patch("src.news_trader.classifier.get_llm")
def test_classify_raises_provider_error_on_llm_exception(mock_get_llm):
    mock_get_llm.return_value.invoke.side_effect = Exception("network error")

    with pytest.raises(LLMProviderError, match="network error"):
        classify("some headline", _settings())


@patch("src.news_trader.classifier.get_llm")
def test_classify_returns_event_type(mock_get_llm):
    _patched_llm(mock_get_llm, VALID_RESPONSE)

    result = classify("RBI rate cut", _settings())
    assert result is not None
    assert result["event_type"] == "regulatory"


@patch("src.news_trader.classifier.get_llm")
def test_classify_coerces_unknown_event_type_to_other(mock_get_llm):
    response = json.dumps({
        "sector": "IT",
        "signal": "bullish",
        "magnitude": "minor",
        "stocks": ["TCS"],
        "confidence": "low",
        "reasoning": "test",
        "event_type": "completely_made_up_value",
    })
    _patched_llm(mock_get_llm, response)

    result = classify("some headline", _settings())
    assert result is not None
    assert result["event_type"] == "other"


@patch("src.news_trader.classifier.get_llm")
def test_classify_defaults_missing_event_type_to_other(mock_get_llm):
    # LLM response missing event_type entirely → should return None (required key missing)
    response = json.dumps({
        "sector": "IT",
        "signal": "neutral",
        "magnitude": "minor",
        "stocks": [],
        "confidence": "low",
        "reasoning": "nothing happening",
    })
    _patched_llm(mock_get_llm, response)

    result = classify("some headline", _settings())
    assert result is None


@patch("src.news_trader.classifier.get_llm")
def test_classify_partial_parse_defaults_event_type(mock_get_llm):
    # Truncated JSON (stocks array cut off) — partial parse path must set event_type
    truncated = '{"sector": "Energy", "signal": "bullish", "magnitude": "moderate", ' \
                '"confidence": "high", "reasoning": "oil prices rise", "event_type": "macro_sector", ' \
                '"stocks": ["OIL", "BPCL'  # deliberately cut off
    _patched_llm(mock_get_llm, truncated)

    result = classify("oil prices spike", _settings())
    assert result is not None
    assert result["event_type"] == "macro_sector"


@patch("src.news_trader.classifier.get_llm")
def test_classify_partial_parse_missing_event_type_falls_back_to_other(mock_get_llm):
    truncated = '{"sector": "Energy", "signal": "bullish", "magnitude": "moderate", ' \
                '"confidence": "high", "reasoning": "oil prices rise", ' \
                '"stocks": ["OIL", "BPCL'  # no event_type, cut off
    _patched_llm(mock_get_llm, truncated)

    result = classify("oil prices spike", _settings())
    assert result is not None
    assert result["event_type"] == "other"


@patch("src.news_trader.classifier.get_llm")
def test_classify_normalizes_event_type_to_lowercase(mock_get_llm):
    response = json.dumps({
        "sector": "Auto",
        "signal": "bullish",
        "magnitude": "major",
        "stocks": ["MARUTI"],
        "confidence": "high",
        "reasoning": "Maruti wins large fleet order.",
        "event_type": "ORDER_WIN",  # uppercase from LLM
    })
    _patched_llm(mock_get_llm, response)

    result = classify("Maruti wins fleet order", _settings())
    assert result is not None
    assert result["event_type"] == "order_win"
