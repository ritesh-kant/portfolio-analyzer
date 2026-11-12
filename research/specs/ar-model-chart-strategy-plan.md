# Auction Resolution (AR) model in /chart — build plan

Goal: the course's strategy (rules in `orderflow-course-ar-model.md`) as a strategy in the web chart, backtested on
**NQ (Nasdaq-100) and GC (gold) futures**, with every indicator he uses, and a UI a non-quant can drive.

Status: proposal, 2026-11-12. Nothing built.

## Design in one paragraph

One **TypeScript strategy engine** (`apps/web/lib/tv/strategies/ar/`) of pure functions: bars in → levels, setups,
entries, exits out. The same code draws the chart overlays (live and in replay) **and** runs the backtest, so what you
see on the chart is exactly what was tested. That mistake has bitten this repo before: the Lab's CVD needed a
separate Python port kept in parity by hand. The backtest runs in the browser over the loaded history for quick
checks, and from a Node script over the multi-year cache for real runs. Both use the same engine and write the
same trade list.

## What exists vs. what is missing

| He uses | In /chart today | Work |
|---|---|---|
| Footprint (bid × ask), stacked imbalances, per-candle POC/value area | Yes (`footprint-series.ts`), but **estimated from 1-minute bars** | Real data needs ticks (see Data) |
| Delta, CVD, delta divergence | Yes, estimated | Same |
| Session / anchored volume profile, POC, VAH/VAL | Yes (`svp`, `avp`) | Add **fixed-range profile between two auto-detected swings** |
| Fibonacci retracement | Drawing tool only | **Auto range-fib**: latest significant swing → 0.705–0.886 zone, 0.886-close invalidation, 1.1 extension, target at the opposite swing |
| Premium / discount | No | Shade above VAH / below VAL of the active range |
| Market structure (HH/HL/LH/LL on 4h/1h/15m/5m) | No | Swing labels + a bias read-out per timeframe |
| Environment (expansion / rotation / compression) | No | Classifier from overlap ratio, failed-breakout count, range vs. ATR (our definitions; he gives none) |
| Asia/London/NY sessions | Regular-hours shading only | Session boxes |
| AVR levels (Asia range ± ATR(20) projections) | No | Rebuild from the numbers read off his screen: unit ≈ 13–15 % of ATR(20), down = 1.067 × up, offsets in the rules note. Approximate, not his formula |
| Gamma: call wall, put wall, HVL, positive/negative regime | Yes (`gex`, QQQ→NQ and GLD→GC proxies, current chain only) | Regime flag into the strategy; **daily snapshot recorder** to build history |
| Absorption, delta flip, two-candle LVN entry | No | Detectors; honest only with tick data |
| Big trades | In progress (NSE only, uncommitted, another session) | Extend to futures once tick data exists |
| DOM / liquidity heatmap | No | Needs live order-book data; out of scope for backtests |
| "Darkpool decoder" | — | His private indicator; skipped |

## Data — the real constraint

1. **1-minute bars, multi-year, including overnight** (the Asia session needs it): IB Gateway, already used for US
   stocks (`research/backtests/ibkr_us_history.py`). Add continuous-futures contracts (NQ, GC; MNQ/MGC share
   the price series) and an option for outside-regular-hours bars. Needs CME + COMEX market-data permissions on
   the IBKR account. Yahoo (`NQ=F`, `GC=F`) keeps 1-minute bars for only about a week: fine for the live chart, not
   for backtests.
2. **Trades with aggressor side** for real footprint, delta and absorption: IBKR historical ticks come back 1,000
   per request, which is impractical for NQ beyond a few days. A CME data vendor with an aggressor flag (e.g.
   Databento, usage-priced) is the practical source. Without it, the confirmation step can only use the
   bar-estimated delta, and the UI must label it "estimated".
3. **Gamma history**: none for free. Start recording a daily pre-open snapshot now. Backtests run with the gamma
   filter **off** until enough history exists.

## UI (follows TradingView patterns the chart already copies)

1. **Indicators**: each one appears in the existing indicator dialog under an **"AR Model"** group. One click on an
   **"AR Model" template** turns the whole stack on with his defaults.
2. **Strategy panel** (top bar → Strategy → Auction Resolution): a **4-step checklist card** (Environment ·
   Location · Path · Confirmation), each step green/amber/red with a one-line reason for the current candle. On
   the chart: zone box, invalidation line, target line, entry/stop/target markers, and why a setup was skipped.
3. **Strategy Tester** (bottom panel):
   - **Overview**: net after costs, trades, win %, average and **median** per trade, profit factor, max drawdown,
     equity curve.
   - **Trades**: one row per trade. Clicking a row jumps the chart there in replay.
   - **Inputs**: plain-English labels, and each filter can be toggled on or off.
   - **Compare**: re-run with one filter changed and show the difference side by side.
4. **Replay**: step through a session and watch the checklist change candle by candle.

## Phases

| # | Deliverable | Depends on |
|---|---|---|
| 0 | IBKR futures downloader (NQ, GC, 24 h, 2–3 years) and `/api/chart/bars` serving that cache for `NQ1!`/`GC1!`; decide on the tick vendor | IBKR permissions |
| 1 | The mechanical indicators: auto range-fib zone, premium/discount, market structure, environment, session boxes, AVR levels | 0 for history; works on Yahoo bars meanwhile |
| 2 | Strategy engine + chart overlays + checklist card | 1 |
| 3 | Strategy Tester (browser) + Node runner over the cache, trade list in bt17 column names | 2 |
| 4 | Real order flow: tick import, absorption / delta-flip / two-candle entry, big trades on futures | tick data |
| 5 | Gamma regime in the backtest | months of recorded snapshots |

## Honesty rules carried over from this repo

- Costs: commission plus at least one tick of slippage per side (NQ tick = $5 on NQ, $0.50 on MNQ; GC tick =
  $10 on GC, $1 on MGC). Show net, and the median beside the mean.
- Every level is computed **as of the decision candle** (no look-ahead). Replay must reproduce the backtest's
  entries exactly.
- "Bias" is discretionary in the course. The backtest tests **our codification** of it (e.g. 1 h structure), not
  his judgement. Say so in the UI.
- Exploratory runs are fine. Anything to be believed or traded gets a pre-registered BT number first.
- Several sessions share this checkout and `tv-chart.tsx` currently has uncommitted big-trades/layout work. Build
  in a separate worktree, or after that work is committed.
