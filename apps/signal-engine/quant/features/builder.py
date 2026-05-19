"""Feature builders for L2 feature store. To be implemented Month 2.

Each builder is a pure function (PIT-input → PIT-feature) with explicit
as_of_timestamp propagation. No hidden state, no global indicator caches —
the previous system's signal_replay.compute_signal_score scored stocks by
running indicator math + market context together; this module separates
the two cleanly.

Planned builders for Strategy A (PEAD):
    - revenue_surprise_pct
    - margin_surprise_pct
    - guidance_direction (from filing_parser agent output)
    - residualized_momentum (5d/20d, controlled for Nifty + sector beta)
    - earnings_day_reaction_pct (fraction of historical PEAD response)
    - turnover_z_score (liquidity / participation)
    - regime_label (output of regime classifier; not a tuned threshold)
"""

from __future__ import annotations


def build_features(symbol: str, business_date: str):
    raise NotImplementedError("builder.build_features — Month 2")
