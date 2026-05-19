"""Adversarial critic — finds 3 distinct ways a proposed trade could fail. Month 4.

Model: Deepseek-R1 primary, Gemini 2.5 Pro fallback.
This is a genuine reasoning task — repeat-rewording the same failure mode
is the most common cheap-model failure, so R1 (or Gemini Pro) earns its cost.

Input: proposed trade with full feature context + filing-parser output +
       retrieved similar historical trades from the vector DB.

Output schema:
    {
        "failure_modes": [
            {"reason": str, "severity": float 0-1, "citation": str},
            {"reason": str, "severity": float 0-1, "citation": str},
            {"reason": str, "severity": float 0-1, "citation": str}
        ],
        "overall_block_recommendation": "block"|"downsize"|"proceed"
    }

The output does NOT directly size positions. The L5 posterior-Kelly sizer
reads `overall_block_recommendation` as a feature; the actual sizing math
is unaffected by LLM output (plan §6 hard rule).
"""

from __future__ import annotations


def critique_trade(trade: dict, context: dict):
    raise NotImplementedError("critique_trade — Month 4")
