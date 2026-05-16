"""Walk-forward backtesting harness for the NSE signal engine.

Replays the exact signal/order logic from the production pipeline
(signal_agent, order_agent, guard_agent) against historical NSE data
to measure out-of-sample performance before any live trading.

Modules:
    data_loader   — fetch + cache OHLCV, VIX, Nifty, FII, earnings
    indicators    — replicate technical_agent.py indicator computation
    signal_replay — replicate signal_agent.py scoring (no LLM)
    order_simulator — replicate order_agent.py sizing + portfolio management
    engine        — walk-forward driver
    metrics       — Sharpe, Sortino, drawdown, win rate, profit factor
    report        — console + CSV output
    recalibrate   — update order_agent.py Kelly table from results
    run_backtest  — CLI entry point
"""
