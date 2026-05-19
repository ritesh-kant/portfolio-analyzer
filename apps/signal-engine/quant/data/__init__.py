"""L1 data lake — point-in-time ingestion of NSE/BSE source data.

Every row written here carries (symbol, business_date, as_of_timestamp).
The as_of_timestamp is the exact moment the data became publicly knowable —
used by every downstream layer to prevent lookahead bias.

Modules (per plan §11):
    pit_loader.py — PIT-correct OHLCV / fundamentals / filings loader
                    (built on top of the surviving backtest/data_loader.py
                    caching layer)
"""
