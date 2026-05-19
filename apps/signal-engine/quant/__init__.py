"""quant — new signal platform pipeline (under construction).

This package replaces the previous LangGraph-based agent chain that was
removed during the demolition phase. The new architecture is five layers
(see ~/.claude/plans/based-on-the-full-harmonic-gosling.md §2):

    L1 data       — point-in-time data lake (parquet, as_of-stamped)
    L2 features   — feature builders + PSI drift monitor
    L3 models     — per-strategy LightGBM + isotonic calibration
    L4 strategies — strategy filters (PEAD, index recon, ...)
    L5 portfolio  — posterior-Kelly sizing + risk-parity allocation

Orthogonal:
    research      — purged k-fold validator, DSR, hold-out lock,
                    hypothesis registry interface
    execution     — paper + live broker adapters
    agents        — LLM agents (filing parser, adversarial critic,
                    post-mortem) — Deepseek V3/R1 primary, Gemini failover
"""
