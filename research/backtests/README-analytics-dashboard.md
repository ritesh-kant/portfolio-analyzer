# Momentum analytics dashboard

A trade-journal view of momentum trades at `/momentum/analytics` in the web app —
the same kind of breakdowns Tradervue gives a discretionary trader, over the
paper ledger this repo already writes.

It answers, for any trade set:

- **what kind of stock** — share price band, day move at entry, relative volume,
  stop distance, position size, per-symbol
- **what time** — half-hour slots across the NSE session
- **which day** — weekday, and every individual trading day as a bar
- plus exit reason, setup, strategy arm, time in trade, pullback number, MACD
  sign, room to resistance, candle pattern, and month

Every row is clickable and filters the whole page, so questions compose:
_"in the 10:15 slot, on stocks under ₹500, how did the 2h+ holds do?"_

A date window sits above the breakdowns — **All · 1 day · 3 days · 7 days ·
1 month · 3 months · Custom**. The presets are anchored to the **most recent
trade in the selected set, not to today's clock**: measured from now, "last 7
days" would be empty on a 2022 backtest, and on live paper trades a Monday
morning would silently drop Friday's session. The resolved dates, trading-day
count and trade count are always printed under the pills, because 7 calendar
days spanning a weekend is fewer sessions than 7 midweek. Custom opens
pre-filled with the window already on screen (so the first edit narrows rather
than resets) and its inputs are bounded by the set's own first and last trade.

The window narrows the set first; bucket filters then slice what is left, and
the filter row counts against the window (`69 of 245 trades in the window`).
Changing the trade set resets the window, since the anchor date moves with it.

There is no sector or industry breakdown. The only sector map in this repo
(`src/pipeline/sector_map.py`) covers ~100 large caps and almost none of the
NIFTY-500 mid/small caps this strategy actually trades, so the dimension would
be mostly blank and misleading. Add one when a real mapping for the traded
universe exists.

## Where the numbers come from

| Source | Collection | Written by |
| --- | --- | --- |
| Live paper trades | `mt_positions` | the live scanner's `PaperLedger` |
| Backtest runs | `mt_backtest_trades` + `mt_backtest_runs` | `import_trades_to_mongo.py` |

Backtest rows are kept in their own collection, carry a `run_tag`, and are
labelled `BACKTEST` everywhere in the UI. They can never be mistaken for, or
silently mixed into, the live ledger.

API: `GET /mt/analytics/sources` lists the sets, `GET /mt/analytics?source=<id>`
returns the closed trades of one, slimmed to the analysis fields (the review
page's `GET /mt/trades` still carries the full candle snapshots).

## Importing a backtest run

```bash
pnpm bt:import --trades research/backtests/bt17_trades.csv \
               --tag bt17_2024_25 --label "BT17 pool · 2024–25"

pnpm bt:import --list              # what is already imported
pnpm bt:import --delete bt17_2026  # remove one run
```

The importer **refuses** a CSV with a known P&L defect — more than one trade per
symbol-day (the multi-entry arm killed on 2022-23), or the pre-2026-09-06
breakeven-lock defect that logged ~40% of real 2R winners as flat scratches.
`--force` overrides it and prints the warning.

Currently imported (three clean, non-overlapping windows):

| Tag | Window | Trades | Source CSV |
| --- | --- | --- | --- |
| `bt17_2022_23` | 2022-01-17 → 2023-12-29 | 1,814 | `bt17_trades_1ma_off.csv` |
| `bt17_2024_25` | 2024-01-01 → 2025-12-31 | 1,876 | `bt17_trades.csv` |
| `bt17_2026` | 2026-01-01 → 2026-09-04 | 689 | `bt17_trades_loc26.csv` |

## The cost-model switch

bt17 charges real MIS costs **plus** a +40 bps/side stress; the live ledger
charges real costs only. Comparing the two as-recorded compares cost models
rather than trading, so the importer stores each backtest trade's stress rupees
separately as `stress_inr` and the dashboard can move between:

| Mode | What it charges | Round trip |
| --- | --- | --- |
| As recorded | each source as stored | — |
| Real broker costs | brokerage, STT, stamp, GST, modelled slippage | **0.206%** |
| Stress-tested | the above + 40 bps/side | **0.806%** |

Verified against the source CSVs — these are reproduced, not asserted:

| Window | Gross | Net @ real | Net @ stress |
| --- | --- | --- | --- |
| 2022-23 | +0.035% | −0.171% | −0.971% |
| 2024-25 | −0.003% | −0.209% | −1.009% |
| 2026 | +0.022% | −0.184% | −0.984% |

## Reading it honestly

- Gross and net are always shown together. On this strategy the gross edge is
  ≈ 0 and costs are the entire loss; a net-only view hides which of the two a
  bucket is telling you about.
- Every bucket shows its own `n`. Below `MIN_READABLE_TRADES` (30) a row is
  dimmed and tagged **THIN** — a three-trade bucket must never read as a rule.
- A dimension counts and reports the trades that never recorded its field
  instead of bucketing them as zero.
- Anything you find here is **in-sample on a window that has already been used**
  for entry-side mining. It is a hypothesis to pre-register on fresh data, not a
  result.

## Code

| File | Role |
| --- | --- |
| `apps/web/lib/momentum-analytics.ts` | pure aggregation: measures, dimensions, filters, series |
| `apps/web/lib/momentum-analytics.test.mjs` | unit tests (`node --experimental-strip-types --test`) |
| `apps/web/components/momentum-analytics-panels.tsx` | KPI row, breakdown tables, inline-SVG charts |
| `apps/web/app/momentum/analytics/page.tsx` | page, source/cost/metric controls, cross-filtering |
| `apps/portfolio-service/src/handlers/mt-analytics.ts` | both API routes |
| `research/backtests/import_trades_to_mongo.py` | CSV → Mongo importer |
