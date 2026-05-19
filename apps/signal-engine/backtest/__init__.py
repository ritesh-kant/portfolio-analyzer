"""Backtest harness (transitional — under reconstruction).

Surviving modules (per the rebuild plan §1.2):
    data_loader     — OHLCV/VIX/Nifty/FII caching layer; will be refactored to
                      feed the point-in-time feature store in Month 2.
    indicators      — vectorized indicator math; reused as feature builders
                      (not as scorer) in the new pipeline.
    metrics         — Sharpe/Sortino/drawdown primitives. The Phase 1 decision
                      gate (GATE_MIN_SHARPE etc.) is being replaced by Deflated
                      Sharpe + capacity + anti-strategy + cost-stress in
                      quant/research/dsr.py during Month 2.
    order_simulator — Position/Portfolio/cost model. The static _WIN_PROB_TABLE
                      and _half_kelly are stubbed out and will be replaced by
                      quant/portfolio/posterior_kelly.py in Month 3.
    report          — console + CSV formatting helpers; kept as scaffolding.

Removed during demolition:
    engine.py       — overlapping 3-month walk-forward; replaced by
                      quant/research/purged_kfold.py.
    signal_replay   — additive 9-signal scorer; replaced by a calibrated LightGBM
                      classifier in quant/models/.
    recalibrate.py  — Kelly-table rewriter; the posterior-Kelly approach is online.
    run_backtest.py — CLI for the deleted engine.
"""
