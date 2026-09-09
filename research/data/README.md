# research/data

Static inputs for the momentum trader (spec: `research/specs/warrior-patterns-nse.md` §1).

| File | Source | Refresh | Used by |
|---|---|---|---|
| `mt_universe.csv` | Trendlyne StratQ export (free-float mcap, promoter %) + NSE price-band / surveillance files | quarterly (shareholding) + daily (bands, if automated) | scanner `universe.load_facts` |
| `nifty_smallcap250.txt`, `nifty_microcap250.txt` | NSE index constituent CSVs, one symbol per line | quarterly | `MT_EXTRA_UNIVERSE_FILES`, bt17 `--extra` |
| `results_dates.csv` | StratQ results calendar: `symbol,date[,event_type]` | per results season | bt17 `--events` (dated catalyst subset) |

`mt_universe.csv` columns:

```
symbol,free_float_mcap_cr,promoter_pct,band_pct,series,surveillance
```

If the file is absent the scanner runs on NIFTY 500 with the float/band filters
skipped and stamps every log row `float_filter_applied=0`.
