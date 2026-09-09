"""Momentum trader — Warrior-Trading-derived intraday setups adapted to NSE.

Spec (frozen, versioned): research/specs/warrior-patterns-nse.md
Hypothesis: research/hypotheses/2026-09-05-momentum-catalyst-upstox-v2.md

Everything here is a deterministic rule on OHLCV bars. No chart images, no LLM
in the entry path. The catalyst gate (LLM-tagged hard event) lives in the
scanner, not here.
"""
