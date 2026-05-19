"""L5 portfolio layer — position sizing + capital allocation + hedging.

Replaces the static-Kelly logic that was stripped from order_simulator.py.

Modules (per plan §11):
    posterior_kelly.py — Bayesian Beta-Bernoulli win-prob posterior,
                         stratified by (strategy, regime, model_decile);
                         sizing at quarter-Kelly clipped to 5% per position
    (later: allocator.py — risk-parity across live strategies; beta_hedge.py)
"""
