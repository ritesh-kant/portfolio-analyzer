"""L1 data lake — point-in-time ingestion of NSE/BSE source data.

Every row written here carries (symbol, business_date, as_of_timestamp).
The as_of_timestamp is the exact moment the data became publicly knowable —
used by every downstream layer to prevent lookahead bias.

Public surface:
    pit_loader.load()      — PIT-correct read from the lake
    ingest.ingest_range()  — download and write Bhavcopy to the lake
    ingest._main()         — CLI: python -m quant.data.ingest
"""

def __getattr__(name: str):
    if name == "load_pit":
        from quant.data.pit_loader import load
        return load
    if name == "ingest_range":
        from quant.data.ingest import ingest_range
        return ingest_range
    raise AttributeError(name)
