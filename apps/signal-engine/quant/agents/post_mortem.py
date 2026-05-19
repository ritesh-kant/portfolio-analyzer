"""Post-mortem agent — structures every closed trade into the memory layer. Month 4.

Model: Deepseek-V3.
Triggered after every closed trade (paper or live). Output is written to
the sqlite-vec store as one row per trade: (trade_id, embedding,
narrative, outcome_struct, lesson).

Used at decision time by retrieval-augmented decisioning: when considering
a new trade, retrieve top-50 most similar past trades and surface the
aggregate hit-rate + common failure modes.

Output schema:
    {
        "narrative": str,                      # 3-5 sentence description
        "proximate_exit_cause": str,           # TARGET|STOP|MAX_AGE|other
        "feature_extremes_at_entry": list[str], # features in top/bottom decile
        "regime_at_entry": str,
        "regime_at_exit": str,
        "lesson": str                          # one sentence; the part future
                                               # retrievals will match against
    }
"""

from __future__ import annotations


def write_post_mortem(closed_trade: dict, entry_features: dict):
    raise NotImplementedError("write_post_mortem — Month 4")
