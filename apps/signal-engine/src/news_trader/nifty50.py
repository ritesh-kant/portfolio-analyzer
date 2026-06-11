"""NIFTY 50 large-cap exclusion set.

Used by trade_decision to EXCLUDE large-caps from news-trading (opposite role
to nifty500.NIFTY_500, which is the inclusion whitelist). BT5 (2026-06-10,
n=33 closed trades): NIFTY50 names had negative GROSS P&L — the news is fully
priced in before our 15-min delayed entry, so there is no post-entry drift to
capture. Non-large-caps were gross-positive over the same window.

MAINTENANCE: refresh every 6 months (NSE rebalances in March and September):
    cd apps/signal-engine
    python scripts/refresh_nifty500.py

Last updated: 2026-06-11 (50 symbols)
"""

# fmt: off
NIFTY_50: frozenset[str] = frozenset({
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TMPV", "TRENT", "ULTRACEMCO", "WIPRO",
})
# fmt: on
