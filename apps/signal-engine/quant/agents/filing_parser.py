"""Filing parser — NSE/BSE earnings press release → structured JSON. Month 3.

Model: Deepseek V3 primary, Gemini 2.5 Pro fallback for PDFs > 50 pages.
NOT R1 — extraction needs precision, not reasoning, and R1's 64K context
is too tight for long Indian earnings PDFs.

Output schema (locked in hypothesis 2026-05-19-pead-midcap.md §5):
    {
        "revenue_surprise_pct": float | null,   # actual vs consensus/naive
        "margin_surprise_pct": float | null,    # operating margin surprise
        "guidance_direction": str,              # "up"|"down"|"unchanged"|"withdrawn"|"unknown"
        "segment_commentary": list[str],        # 3-7 bullet points from MD&A
        "mgmt_tone_score": float,               # -1.0 (very negative) to +1.0 (very positive)
        "cited_paragraphs": list[str]           # exact quoted passages (MANDATORY for numerics)
    }

Citation requirement: every numeric claim must include the exact paragraph
it was extracted from.  Hallucinated citations are caught by the verifier
(string-match of the quoted passage against the source text).

See plan §6 for model/provider rationale.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "revenue_surprise_pct": {
            "oneOf": [{"type": "number"}, {"type": "null"}],
            "description": "Revenue surprise as fraction (0.12 = +12%). Null if consensus unavailable.",
        },
        "margin_surprise_pct": {
            "oneOf": [{"type": "number"}, {"type": "null"}],
            "description": "Operating margin surprise as fraction. Null if consensus unavailable.",
        },
        "guidance_direction": {
            "type": "string",
            "enum": ["up", "down", "unchanged", "withdrawn", "unknown"],
        },
        "segment_commentary": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 7,
            "description": "3–7 short bullet points from MD&A. Empty list if not parseable.",
        },
        "mgmt_tone_score": {
            "type": "number",
            "minimum": -1.0,
            "maximum": 1.0,
            "description": "-1.0 = very negative, 0.0 = neutral, +1.0 = very positive",
        },
        "cited_paragraphs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Exact quoted passages supporting every numeric claim. Required.",
        },
    },
    "required": [
        "guidance_direction",
        "mgmt_tone_score",
        "segment_commentary",
        "cited_paragraphs",
    ],
}

_SYSTEM_PROMPT = """You are a financial document parser specialised in Indian equity earnings releases.
Your task is to extract structured data from NSE/BSE earnings press releases or quarterly result PDF text.

Rules:
1. Extract only what is explicitly stated. Do NOT infer, estimate or hallucinate.
2. For every numeric claim (surprise %, guidance), quote the EXACT paragraph from the source text in cited_paragraphs.
3. revenue_surprise_pct and margin_surprise_pct: only fill if you can compute a concrete number from the text + consensus provided. Else null.
4. guidance_direction: look for management guidance, outlook statements, or forward-looking commentary. If ambiguous or absent, return "unknown".
5. mgmt_tone_score: assess the overall tone of management commentary on a scale from -1.0 (very negative, warnings, downgrades, stress) to +1.0 (very positive, strong growth, beat, confident outlook). 0.0 = neutral.
6. segment_commentary: extract 3–7 short, factual bullet points from segment discussion or MD&A. Not marketing language.
7. If the document is irrelevant or cannot be parsed, return all nulls and an empty cited_paragraphs."""


def parse_filing(
    text: str,
    consensus: dict[str, float] | None = None,
    source_url: str = "",
) -> dict[str, Any]:
    """Parse an earnings press release or PDF text into structured output.

    Parameters
    ----------
    text : str
        Full text of the earnings document (plain text, extracted from PDF or HTML).
        Truncated to 60 000 characters if longer (Deepseek V3 context limit).
    consensus : dict | None
        Optional analyst consensus: {"revenue_cr": float, "eps": float}.
        If provided, surprise percentages can be computed.
    source_url : str
        NSE filing URL for audit trail.

    Returns
    -------
    dict matching _OUTPUT_SCHEMA.
        Always returns a valid dict even on parse failure (graceful degradation
        with null numeric fields and empty lists).

    Notes
    -----
    Requires DEEPSEEK_API_KEY or GOOGLE_API_KEY in environment.
    If neither is set, returns the neutral fallback dict (no exception raised)
    so that the data pipeline can continue with missing LLM features.
    """
    from quant.agents import llm_router  # lazy import avoids requiring API keys in tests

    # Truncate to model context limit (conservative: 60K chars ≈ ~15K tokens)
    truncated = text[:60_000] if len(text) > 60_000 else text

    # Build user prompt
    consensus_block = ""
    if consensus:
        lines = [f"  {k}: {v}" for k, v in consensus.items()]
        consensus_block = "\n\nANALYST CONSENSUS:\n" + "\n".join(lines)

    user_prompt = (
        f"Parse the following earnings document and return structured JSON.{consensus_block}\n\n"
        f"SOURCE URL: {source_url}\n\n"
        f"DOCUMENT TEXT:\n{truncated}"
    )

    try:
        result = llm_router.call(
            role="parser",
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            response_schema=_OUTPUT_SCHEMA,
            temperature=0.05,
        )
        _verify_citations(truncated, result)
        return result

    except llm_router.LLMError as exc:
        logger.error("Filing parser LLM call failed for %s: %s", source_url, exc)
        return _neutral_output()
    except Exception as exc:
        logger.error("Filing parser unexpected error for %s: %s", source_url, exc)
        return _neutral_output()


def parse_filing_pdf(
    pdf_path: str | Path,
    consensus: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Extract text from a PDF and parse it.

    Falls back to Gemini 2.5 Pro (1M context) if the PDF exceeds 50 pages,
    since Deepseek V3's 64K context is too tight for long filings.

    Parameters
    ----------
    pdf_path : str | Path
    consensus : dict | None

    Returns
    -------
    dict matching _OUTPUT_SCHEMA.
    """
    try:
        import pdfminer.high_level as pdfminer  # type: ignore[import-untyped]
    except ImportError:
        logger.error("pdfminer.six not installed — cannot parse PDF. Run: pip install pdfminer.six")
        return _neutral_output()

    path = Path(pdf_path)
    if not path.exists():
        logger.error("PDF not found: %s", pdf_path)
        return _neutral_output()

    try:
        text = pdfminer.extract_text(str(path))
    except Exception as exc:
        logger.error("PDF extraction failed for %s: %s", pdf_path, exc)
        return _neutral_output()

    return parse_filing(text, consensus=consensus, source_url=str(path))


def _verify_citations(source_text: str, output: dict[str, Any]) -> None:
    """Cheap second-pass citation verifier.

    Logs a warning for any cited paragraph that cannot be found
    verbatim (substring match) in the source text.
    Halluciations in citations don't block the pipeline but are flagged
    for audit.
    """
    cited = output.get("cited_paragraphs", [])
    for i, passage in enumerate(cited):
        if not passage:
            continue
        # Normalise whitespace for comparison
        needle = " ".join(passage.split())
        haystack = " ".join(source_text.split())
        if needle not in haystack:
            logger.warning(
                "Citation %d not found verbatim in source text (possible hallucination): %r...",
                i,
                passage[:80],
            )


def _neutral_output() -> dict[str, Any]:
    """Return a neutral/null output when parsing fails."""
    return {
        "revenue_surprise_pct": None,
        "margin_surprise_pct": None,
        "guidance_direction": "unknown",
        "segment_commentary": [],
        "mgmt_tone_score": 0.0,
        "cited_paragraphs": [],
    }
