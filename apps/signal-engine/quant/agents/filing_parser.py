"""Filing parser — NSE/BSE earnings press release -> structured JSON. Month 3.

Model: Deepseek-V3 primary, Gemini 2.5 Pro fallback for PDFs > 50 pages.
NOT Deepseek-R1 — extraction needs precision, not reasoning, and R1's
64K context is too tight for some Indian earnings PDFs.

Output schema (locked at module-creation time; changes require a new
hypothesis registration):

    {
        "revenue_surprise_pct": float,        # actual vs consensus
        "margin_surprise_pct": float,
        "guidance_direction": "up"|"down"|"unchanged"|"withdrawn"|"unknown",
        "segment_commentary": list[str],      # 3-7 short bullet points
        "mgmt_tone_score": float,             # -1 to +1
        "cited_paragraphs": list[str]         # exact quoted passages (MANDATORY)
    }

Citation requirement: every numeric claim must include the exact paragraph
it was extracted from. Hallucinated citations are caught by a cheap
second-pass verifier (string-match the quoted passage against the PDF).
"""

from __future__ import annotations


def parse_filing(pdf_path: str, consensus: dict | None = None):
    raise NotImplementedError("parse_filing — Month 3")
