# AR strategy backtest (NQ futures)

The Auction Resolution strategy from the order-flow course (rules: `research/specs/orderflow-course-ar-model.md`,
plan: `research/specs/ar-model-chart-strategy-plan.md`) runs from one TypeScript engine,
`apps/web/lib/tv/ar-strategy.ts`, in two places:

| Where | What it tests | Use it for |
|---|---|---|
| **/chart → bottom bar → Strategy Tester** | the candles loaded on the chart (5 m: last 60 days) | looking at trades, A/B-ing a setting, costs |
| **`pnpm bt:ar`** | every candle saved in `.cache_yahoo_futures/` (5 m since 2026-09-03, 60 m since 2024-06) | the full saved history, saved runs |

Both use the same cost and stat maths (`apps/web/lib/tv/strategy-tester.ts`), so they print the same numbers on
the same candles. The Strategy Tester's tabs:

- **Overview**: net after costs, how trades ended, longs/shorts, and why setups did not trade.
- **Trades**: click a row to jump the chart to that trade.
- **Compare**: change any setting and see A (the chart) beside B, with which trades each run took.
- **Costs**: contract (NQ, MNQ, …, NSE futures), contracts or lots, fees, slippage, and the cost of one round trip
  item by item. NSE futures take their lot and tick size from Upstox's contract list and pay ₹20 brokerage an
  order, STT 0.05 % of the sell value (since 1 April 2026; 0.02 % before), the NSE charge ~0.00183 % a side,
  SEBI ₹10 a crore, stamp duty 0.002 % of the buy value and 18 % GST on the fees: about ₹990 a NIFTY lot round
  trip at 25,300, of which ₹823 is STT.

## Command line

```bash
pnpm bt:ar
```

```bash
pnpm bt:ar --vs entry=close
```

```bash
pnpm bt:ar --set minRR=2 --set windowEnd=12:00 --tag later_window
```

```bash
pnpm bt:ar --interval 60m --contract MNQ --contracts 2 --slip 2
```

Record today's gamma levels (QQQ options scaled to NQ, the chart's Gamma Levels maths). Run it once a day, before
New York opens. The file cannot be re-fetched later, so `gamma_snapshots/` is not git-ignored and should be committed:

```bash
pnpm bt:gex
```

```bash
pnpm bt:gex --show
```

```bash
pnpm bt:ar --from 2026-10-14 --set confirm=orderflow --set entry=lvn
```

- `--set key=value` changes A; `--vs key=value` makes B = A plus that change, printed side by side. Keys are the
  fields of `ArStrategyCfg` (`entry` = `pullback`/`close`/`lvn`, `entryWait`, `windowStart`, `windowEnd`, `flatAt`,
  `minRR`, `swingMult`, `confirmWithin`, `stopBuffer`, `allowWarnings`, `requireOutsideValue`, `requireStack`,
  `confirm` = `candles`/`orderflow`, `absorbVol`, `flipShare`, `valueRows`, `envWindow`); times as `HH:MM` New York.
- **Order flow (phase 4).** `confirm=orderflow` replaces the candle rule with his footprint read
  (`apps/web/lib/tv/ar-orderflow.ts`): absorption on the extreme candle (≥ `absorbVol` × usual volume, POC at the
  rejection end, closed in the other half), then a delta flip (≥ `flipShare` of a later candle's volume on the
  trade's side). `entry=lvn` rests the order at the confirmation candle's low-volume row. The runner builds the
  footprint from the saved 1-minute bars (only ~4 weeks; it prints how many candles have one), exactly as the chart
  does, so on NQ it is **estimated**: each minute's volume is called buying or selling by where that minute closed.
  The chart uses recorded ticks instead where they exist (NSE futures), and every trade says which (`flow_source`).
  Extra CSV columns: `confirm`, `flow_source`, `absorb_time`, `absorb_vol_x`, `flip_share`, `entry_rule`, `stack`.
- **Decoder zone.** `zone=decoder` swaps his 0.705–0.886 zone for the "dark pool decoder" stand-in: touch at 0.62
  of the leg, cancel on a close past 0.81 (`DECODER_LEVELS` in `apps/web/lib/tv/ar.ts`). The levels are the median
  of five boxes measured off his screen, not his formula (`research/specs/orderflow-course-ar-model.md` §2a′).
- **Gamma (phase 5).** Every run reads the recorded gamma snapshots in `gamma_snapshots/<SYMBOL>.jsonl`
  (`pnpm bt:gex`, below). Each candle uses the newest snapshot read before it closed, if it is at most 4 days old.
  Every trade records its regime: price above the gamma flip is positive gamma, below it is negative. The run also
  prints a by-regime split. `gamma=targets` adds our version of his "positive gamma: take profit quickly": in
  positive gamma, the target moves to the first gamma level (flip, C1–C3, P1–P3, gamma wall) between entry and the
  leg's end, and the reward ÷ risk check uses that target. Extra CSV columns: `gamma_regime`, `gamma_flip`,
  `gamma_level`, `leg_target`. **Recording started 2026-11-14**, so earlier candles have no regime. Until weeks of
  snapshots exist, `gamma=targets` changes nothing.
- `--from` / `--to` (YYYY-MM-DD) cut the window; `--interval` is `5m` (default, his timeframe), `15m` or `60m`.
- Costs default to NQ × 1, one tick of slippage per side, ~IBKR all-in fees: 0.725 points a round trip.
- Every run is saved to `ar_runs/<id>/` (`run.json` with the exact settings, `trades.csv`, `trades_b.csv` with
  `--vs`) and counted in `ar_runs/ledger.jsonl`, like the Lab. `--no-save` skips that.

`trades.csv` uses bt17 column names where they apply (`date, symbol, entry_time, exit_time, entry, stop, target,
exit, exit_reason, qty, gross_pct, net_pct, setup`), plus `side`, `gross_pts`, `net_pts`, `net_r`, `gross_usd`,
`net_usd` and `warnings`. Money is in USD, so there are no `_inr` columns, and times are New York time. The backtest
dashboard does not list these runs: it charts NSE longs only. The chart's Strategy Tester is the viewer.

## Read the numbers honestly

- **Exploratory.** Confirmation is estimated from candles or from a footprint built from 1-minute bars (no bid/ask
  data for NQ), the trend is our codification of a
  discretionary read, and no window here is held out. A result to believe or trade needs a pre-registered BT
  number first (AGENTS.md step 1).
- **Few trades.** On ~60 days of 5-minute NQ, his defaults take 2–3 trades: most zone touches happen overnight,
  outside his 09:30–11:00 New York window. Nothing can be judged from that; the cache grows by ~21 sessions a
  month (save it from the chart's History chip or `pnpm bt:nq`).
- **60-minute candles** reach back ~2.4 years, but the window then admits only the 10:00 candle, so they are not his
  method.
- Prices are not rounded to the 0.25 tick, and fills are conservative: a stop on the entry candle counts, a target
  on it does not.

## First looks (exploratory, not BT-numbered)

**Decoder box vs his zone, 2026-11-12** (`pnpm bt:ar --vs zone=decoder`, NQ 5 m, 2026-09-03 → 11-12, NQ × 1,
0.725 pts a round trip):

| | his zone 0.705–0.886 | decoder 0.62–0.81 |
|---|---|---|
| His window 09:30–11:00 | 2 trades, −$1,865 | 2 trades, −$1,645 |
| All day (window 00:00–23:59) | 58 trades, −$7,194, median −$457, PF 0.72 | 59 trades, −$13,023, median −$479, PF 0.54 |

All day, 41 trades are the same in both, with the same result (−$11,388). The difference is entirely in the trades
only one of them takes:
- **only his zone**, 17 trades: +$4,194 (median +$366, 6 targets);
- **only the decoder**, 18 trades: −$1,635 (median −$432, 4 targets).

So on this window, touching the shallower 0.62 edge added losing trades, and cancelling at 0.81 instead of 0.886
dropped winning ones. This is one 70-day window on bar-estimated confirmation, with a 17-vs-18 trade difference:
far too small to settle anything, and the all-day window is not his method. Runs:
`ar_runs/20261112-163638_5m_decoder_vs_fib_5m/` and `ar_runs/20261112-163644_5m_decoder_vs_fib_5m_allday/`.

