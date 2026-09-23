# BT48 — trades a MACD "open" buffer lets in

Draws every trade that `warrior_strict` takes with a MACD buffer but refuses
under the strict rule (histogram must rise vs the previous 1-minute bar).

Inputs are the four bt17 runs of 2026-09-23 (`bt17_trades_macdtol_{0,0p05,...}_1y{25,26}.csv`):

```bash
# 1. replay (per tolerance, per cached year)
apps/signal-engine/.venv/bin/python research/backtests/bt17_momentum_pool.py \
  --warrior-strict --live-fill --multi-entry --cached-year 2026 \
  --start 2026-01-01 --end 2026-09-22 --macd-open-tolerance 0.05 --tag macdtol_0p05_1y26
# 2. report (defaults: strict=macdtol_0, buffered=macdtol_0p05, tolerance=0.05)
pnpm bt:macdbuffer -- --buffered macdtol_0p10 --tolerance 0.10
pnpm bt:serve   # http://localhost:8899/bt48_macd_buffer_report.html
```

The 1-minute MACD panel is the engine's own warmed series (asserted equal to
`exits.indicator_frame`); each card re-runs `_attention_confirmation` on the
decision candle under both rules and the script exits non-zero if any added
trade was not refused by MACD alone. The 5-minute view's MACD is the BT32
browser recomputation (unwarmed) and is labelled as such.
