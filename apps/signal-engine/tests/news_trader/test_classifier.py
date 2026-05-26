"""Tests for the Gemini classifier — all LLM calls are mocked."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.news_trader.classifier import classify


def _mock_llm_response(content: str):
    """Build a mock LangChain response with the given content string."""
    msg = MagicMock()
    msg.content = content
    return msg


VALID_RESPONSE = json.dumps({
    "sector": "Banking",
    "signal": "bullish",
    "magnitude": "major",
    "stocks": ["HDFCBANK", "SBIN", "ICICIBANK"],
    "confidence": "high",
    "reasoning": "RBI rate cut improves net interest margins for lenders.",
})


@patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
def test_classify_returns_dict_on_valid_response(MockLLM):
    instance = MockLLM.return_value
    instance.invoke.return_value = _mock_llm_response(VALID_RESPONSE)

    result = classify("RBI cuts repo rate by 25bps", gemini_api_key="test-key")

    assert result is not None
    assert result["sector"] == "Banking"
    assert result["signal"] == "bullish"
    assert result["confidence"] == "high"
    assert "HDFCBANK" in result["stocks"]


@patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
def test_classify_strips_markdown_fences(MockLLM):
    fenced = f"```json\n{VALID_RESPONSE}\n```"
    instance = MockLLM.return_value
    instance.invoke.return_value = _mock_llm_response(fenced)

    result = classify("some headline", gemini_api_key="test-key")
    assert result is not None
    assert result["signal"] == "bullish"


@patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
def test_classify_uppercases_stock_symbols(MockLLM):
    response = json.dumps({
        "sector": "IT",
        "signal": "bearish",
        "magnitude": "moderate",
        "stocks": ["infy", "tcs", "wipro"],
        "confidence": "medium",
        "reasoning": "US recession fears hit IT exports.",
    })
    instance = MockLLM.return_value
    instance.invoke.return_value = _mock_llm_response(response)

    result = classify("IT sector faces headwinds", gemini_api_key="test-key")
    assert result is not None
    assert result["stocks"] == ["INFY", "TCS", "WIPRO"]


@patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
def test_classify_returns_none_on_bad_json(MockLLM):
    instance = MockLLM.return_value
    instance.invoke.return_value = _mock_llm_response("not json at all")

    result = classify("some headline", gemini_api_key="test-key")
    assert result is None


@patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
def test_classify_returns_none_on_missing_keys(MockLLM):
    incomplete = json.dumps({"sector": "Banking", "signal": "bullish"})
    instance = MockLLM.return_value
    instance.invoke.return_value = _mock_llm_response(incomplete)

    result = classify("some headline", gemini_api_key="test-key")
    assert result is None


@patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
def test_classify_truncates_stocks_to_5(MockLLM):
    response = json.dumps({
        "sector": "Banking",
        "signal": "bullish",
        "magnitude": "minor",
        "stocks": ["A", "B", "C", "D", "E", "F", "G"],
        "confidence": "low",
        "reasoning": "test",
    })
    instance = MockLLM.return_value
    instance.invoke.return_value = _mock_llm_response(response)

    result = classify("some headline", gemini_api_key="test-key")
    assert result is not None
    assert len(result["stocks"]) == 5


@patch("src.news_trader.classifier.ChatGoogleGenerativeAI")
def test_classify_returns_none_on_llm_exception(MockLLM):
    instance = MockLLM.return_value
    instance.invoke.side_effect = Exception("network error")

    result = classify("some headline", gemini_api_key="test-key")
    assert result is None
