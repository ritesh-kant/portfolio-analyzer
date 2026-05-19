"""Tests for the filing parser and LLM router (no real API calls)."""

from __future__ import annotations

import json
import pytest

from quant.agents.filing_parser import _neutral_output, _verify_citations, _OUTPUT_SCHEMA


# ── Tests: neutral output structure ───────────────────────────────────────────

def test_neutral_output_has_all_required_keys():
    out = _neutral_output()
    assert out["guidance_direction"] == "unknown"
    assert out["mgmt_tone_score"] == 0.0
    assert out["segment_commentary"] == []
    assert out["cited_paragraphs"] == []
    assert out["revenue_surprise_pct"] is None
    assert out["margin_surprise_pct"] is None


def test_neutral_output_guidance_is_valid_enum():
    out = _neutral_output()
    valid = {"up", "down", "unchanged", "withdrawn", "unknown"}
    assert out["guidance_direction"] in valid


# ── Tests: citation verifier ──────────────────────────────────────────────────

def test_verify_citations_all_found(caplog):
    source = "Revenue grew 20% year-on-year. Management is confident."
    output = {
        "cited_paragraphs": ["Revenue grew 20% year-on-year."],
    }
    import logging
    with caplog.at_level(logging.WARNING, logger="quant.agents.filing_parser"):
        _verify_citations(source, output)
    assert "hallucination" not in caplog.text.lower()


def test_verify_citations_missing_passage(caplog):
    source = "Revenue grew 20% year-on-year."
    output = {
        "cited_paragraphs": ["This sentence is completely made up and not in the document."],
    }
    import logging
    with caplog.at_level(logging.WARNING, logger="quant.agents.filing_parser"):
        _verify_citations(source, output)
    assert "hallucination" in caplog.text.lower()


def test_verify_citations_empty_cited(caplog):
    source = "Revenue grew 20%."
    output = {"cited_paragraphs": []}
    import logging
    with caplog.at_level(logging.WARNING, logger="quant.agents.filing_parser"):
        _verify_citations(source, output)
    assert "hallucination" not in caplog.text.lower()


# ── Tests: parse_filing graceful degradation (no API keys) ───────────────────

def test_parse_filing_no_api_keys_returns_neutral(monkeypatch):
    """When no API keys are set, parse_filing must return neutral output, not raise."""
    import os
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    from quant.agents.filing_parser import parse_filing
    result = parse_filing("Q2 FY2024 results. Revenue ₹1200 crore.")
    out = _neutral_output()
    assert result["guidance_direction"] == out["guidance_direction"]
    assert result["mgmt_tone_score"] == out["mgmt_tone_score"]


# ── Tests: output schema structure ────────────────────────────────────────────

def test_output_schema_has_required_fields():
    required = set(_OUTPUT_SCHEMA.get("required", []))
    assert "guidance_direction" in required
    assert "mgmt_tone_score" in required
    assert "cited_paragraphs" in required


def test_output_schema_guidance_enum():
    enum_vals = _OUTPUT_SCHEMA["properties"]["guidance_direction"]["enum"]
    assert set(enum_vals) == {"up", "down", "unchanged", "withdrawn", "unknown"}


def test_output_schema_tone_bounds():
    props = _OUTPUT_SCHEMA["properties"]["mgmt_tone_score"]
    assert props["minimum"] == -1.0
    assert props["maximum"] == 1.0


# ── Tests: LLM router JSON parser ─────────────────────────────────────────────

def test_llm_router_parse_json_clean():
    from quant.agents.llm_router import _parse_json
    raw = '{"guidance_direction": "up", "mgmt_tone_score": 0.8}'
    result = _parse_json(raw)
    assert result["guidance_direction"] == "up"
    assert result["mgmt_tone_score"] == 0.8


def test_llm_router_parse_json_with_markdown_fence():
    from quant.agents.llm_router import _parse_json
    raw = '```json\n{"guidance_direction": "down", "mgmt_tone_score": -0.5}\n```'
    result = _parse_json(raw)
    assert result["guidance_direction"] == "down"


def test_llm_router_parse_json_invalid_raises():
    from quant.agents.llm_router import _parse_json
    import pytest
    with pytest.raises(json.JSONDecodeError):
        _parse_json("this is not json {{{")


# ── Tests: LLM router role→model mapping ──────────────────────────────────────

def test_llm_router_role_models_complete():
    from quant.agents.llm_router import _ROLE_MODELS
    required_roles = {"parser", "classifier", "critic", "narrator", "postmortem", "counterfactual"}
    assert required_roles == set(_ROLE_MODELS.keys())


def test_llm_router_each_role_has_both_providers():
    from quant.agents.llm_router import _ROLE_MODELS
    for role, providers in _ROLE_MODELS.items():
        assert "deepseek" in providers, f"Role {role!r} missing deepseek"
        assert "gemini" in providers, f"Role {role!r} missing gemini"


def test_llm_router_parser_uses_v3_not_r1():
    from quant.agents.llm_router import _ROLE_MODELS
    # Extraction tasks must use chat (V3), not reasoner (R1)
    assert "reasoner" not in _ROLE_MODELS["parser"]["deepseek"]
    assert "reasoner" not in _ROLE_MODELS["classifier"]["deepseek"]


def test_llm_router_critic_uses_reasoner():
    from quant.agents.llm_router import _ROLE_MODELS
    assert "reasoner" in _ROLE_MODELS["critic"]["deepseek"]
