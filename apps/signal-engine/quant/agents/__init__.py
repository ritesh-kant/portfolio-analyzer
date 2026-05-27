"""LLM agents — concrete, not decorative.

Replaces the previous LangGraph chain. Each agent here has a single
well-defined responsibility and outputs structured JSON (not free text
wired into position sizing).

Provider strategy (plan §6):
    Primary: Deepseek (V3 for extraction/classification, R1 for reasoning)
    Failover: Gemini (2.5 Flash + 2.5 Pro) — wired in from day one
    Never Claude as default — cost-prohibitive for this workload

Modules (per plan §11):
    post_mortem.py         — Deepseek-V3; structures every closed trade for
                             the vector-DB retrieval memory layer

Hard rule: LLM output is NEVER wired directly into position sizing. LLMs
produce features and critiques; the L3 ML model + L5 posterior-Kelly do
the sizing math. This is the separation the previous system lacked.
"""
