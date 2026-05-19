"""Execution layer — paper + live broker adapters.

Modules (to be implemented Month 4-5):
    paper.py — paper-trade adapter; mirrors live execution surface exactly
    kite.py  — Zerodha Kite Connect live adapter (subscribed in Month 5
               once Strategy A passes paper validation; cost ~Rs 2k/month)

Both adapters expose the same place_order(symbol, side, qty, ...) API.
Strategies do not know which one they're talking to.
"""
