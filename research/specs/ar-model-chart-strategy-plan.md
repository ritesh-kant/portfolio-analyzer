# Auction Resolution (AR) model in /chart — build plan

Goal: the course's strategy (rules in `orderflow-course-ar-model.md`) as a strategy in the web chart, backtested on
**NQ (Nasdaq-100) futures** (gold deferred until there is COMEX data), with every indicator he uses, and a UI a
non-quant can drive.

Status 2026-11-12: **phase 0 done** (NQ cache + recorder `pnpm bt:nq` + `/api/chart/bars` serving it, see
`research/backtests/README-nq-futures-cache.md`; save from the chart's History chip). **Phase 1 built**: five
indicators under "AR Model" in the picker (Sessions, Asia Range Levels, Range Fib Zone, Market Structure, Market
Environment) with an "Add all" button and an AR read-out panel. Maths in `apps/web/lib/tv/ar.ts` (tested, with a
no-look-ahead check on swings), drawing in `components/tv/ar-primitive.ts`, panel in `components/tv/ar-panel.tsx`.
Our own definitions where he gives none: session windows, swing size (2 × ATR 14), environment thresholds, and the
AVR unit (fitted to two screen readings; at today's ATR of ~750 it runs at its 12 % floor, outside the calibrated
range). **Phase 2 built**: "AR Strategy" (sixth AR indicator, in "Add all"): `apps/web/lib/tv/ar-strategy.ts`
walks the candles in order (tests prove any prefix gives the same checklist and finished trades, for both entry
modes) and draws trades (reward/risk boxes, entry ▲▼, exit with R), skipped setups (✕) and a 4-step checklist for
the candle under the cursor in the AR panel, plus a trade list that jumps the chart to a trade. Rules and their
OUR-definition parts are in the file header and the settings dialog.

First look on ~50 sessions of NQ 5 m (2026-09-03 → 11-12), exploratory, in points before costs: pull-back entry
2 trades (2 stops, −92 pts); close entry 3 trades (1 target, 2 stops, +80 pts). The rules are very selective:
230 confirmations fell outside his 09:30–11:00 New York window (NQ trades round the clock; most touches are
overnight), 52 touches got no confirmation in time, 15–21 failed the 1.5 reward ÷ risk minimum. Far too few
trades to judge anything.

**Phase 3 built**: a **Strategy Tester** bottom panel on the chart (Overview: net after costs, median beside mean,
profit factor, drawdown, equity curve, how trades ended, why setups were skipped; Trades: click to jump; Compare: A =
the chart vs B = any settings, with which trades each took; Costs: NQ/MNQ/ES/MES/GC/MGC, contracts, fees, slippage),
and **`pnpm bt:ar`**, which runs the same engine over every saved candle and saves each run to
`research/backtests/ar_runs/` with a ledger (`research/backtests/README-ar-backtest.md`). Costs/stats in
`apps/web/lib/tv/strategy-tester.ts`, shared by both, so they print the same numbers. On the saved 5 m history
(2026-09-03 → 11-12) at NQ × 1 and 0.725 pts a round trip: his defaults 2 trades, −$1,865; entry at the close 3
trades, +$1,564 (median −$696): too few to read.

**Phase 4 started (order-flow trigger, 2026-11-12)**: `apps/web/lib/tv/ar-orderflow.ts` codifies his step 4 on any
footprint: **absorption** on the candle holding the extreme since the touch (≥ 1.5 × the median volume of the 12
candles before it, POC in the third at the rejection end, close in the other half), then a **delta flip** on a later
candle (delta ≥ 10 % of its volume on the trade's side, closing in the trade's direction beyond the absorption
candle's close), and the **two-candle LVN entry** (limit at the confirmation candle's thinnest inner row). Thresholds
are ours. In the strategy: `confirm: 'orderflow'`, `entry: 'lvn'` (settings dialog, Compare, `pnpm bt:ar --set
confirm=orderflow --set entry=lvn`); each trade records `flow.source` = `ticks` (recorded NSE feed) or `estimated`
(1-minute bars), and the chart marks the absorption candle. First look, NQ 5 m with the footprint estimated from
1-minute bars (2026-10-14 → 11-12): his window gives **0 trades** (109 of 118 touches had no absorption + flip; the
other 7 confirmations were outside 09:30–11:00); the binding rule is "POC at the rejection end", which an estimate
that spreads each minute over the rows it traded rarely produces. Not loosened to manufacture trades. **Still open
in phase 4:** real aggressor-side data for NQ (no source: Yahoo has no bid/ask, IBKR has no CME), NSE ticks only
from 2026-11-12 and only while the recorder runs (schedule it), and big trades on futures (the Upstox feed
aggregates ~73 % of volume per snapshot, so single large prints are not seen).

**Phase 5 built (gamma regime, 2026-11-14)**: the history has to be recorded, so phase 5 is a recorder plus the
strategy hook, ready for when weeks of snapshots exist.
- **Recorder.** `pnpm bt:gex` (`research/backtests/gex_snapshot.mjs`) reads the same QQQ chain and maths as the
  chart's Gamma Levels indicator. That code moved out of the route into `apps/web/lib/tv/gex-us.ts`. Each run
  appends one line (flip, C1–C3, P1–P3, gamma wall, net GEX, QQQ→NQ scale) to
  `research/backtests/gamma_snapshots/NQ_F.jsonl`, which is committed, not cached: it cannot be re-fetched.
- **Strategy.** `lib/tv/gamma-history.ts` picks, at each candle, the newest snapshot read before that candle closed
  (at most 4 days old). The regime is price vs the flip, or the net GEX sign when no flip is in range. Every trade
  and every candle's read carries it: the AR panel shows a γ line, the Strategy Tester a Gamma column and a
  by-regime split, and `bt:ar` prints the split and CSV columns. `gamma: 'targets'` is OUR codification of his
  "positive gamma: take profit quickly": in positive gamma the target moves to the first gamma level before the
  leg's end, and the reward ÷ risk check uses it. Tests cover no look-ahead (a snapshot read after the candle
  closed is ignored) and prefix stability.
- **Free history checked and rejected.** SqueezeMetrics publishes daily SPX GEX back to 2011, but it was negative on
  only 18 days since mid-2024 and on none in the 5 m window. It is S&P, not Nasdaq, and has no levels, so it cannot
  separate the regimes here.
- **First snapshot 2026-11-14** (Friday's chain): flip 28,613, NQ 28,035, so negative gamma. **Open:** run
  `pnpm bt:gex` every pre-market (not scheduled yet). A gamma test needs a few months of snapshots and a
  pre-registered BT number.

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
| "Darkpool decoder" | Fitted stand-in (built 2026-11-12) | His private indicator. Read off 5 uses in the video (course notes §2a′): a box at ≈ 0.62 / 0.75 / 0.81 of the leg, not a fixed ratio. Drawn by Range Fib Zone; `zone: 'decoder'` in the strategy. First look on NQ: worse than his zone |

## Data — the real constraint

**Decision 2026-11-12: NQ only for now.** Gold is dropped. The IBKR account has no CME or COMEX market data, and NQ
is a CME contract, so IBKR can't supply NQ history either. NQ comes from **Yahoo `NQ=F`**. Measured 2026-11-12
(continuous front month, all 24 hours incl. the Asia session, real volume):

| Interval | History Yahoo serves | Enough for |
|---|---|---|
| 1 m | last ~7 days per request (a `30d` range returns HTTP 422; 7-day chunks back to ~30 days probably work, untested) | estimated footprint/delta, going forward only |
| 5 m / 15 m | ~70 days (2026-09-02 → 11-12) | **his execution timeframe**: ~50 sessions to test the mechanical rules now |
| 60 m | ~2.4 years (2024-06-23 → 11-12) | structure, ATR(20), Asia-range levels, 1 h ranges over a long window |

So the history has to be **recorded**: a daily job appends Yahoo's 1 m and 5 m NQ bars to a local cache, and the
backtest window grows by ~21 sessions a month. Yahoo's futures quotes are delayed (~10 min), which is fine for
backtests but means the live chart lags.

Options to get long history sooner, all optional:
- **QQQ as a proxy** via the IBKR stock data already in use: years of 1 m bars and native QQQ gamma, but no
  overnight session, so it can only test the New York-session rules (fib zone, value area, structure), not the
  Asia-range levels.
- **Buy NQ history** later: an IBKR CME subscription or a one-off 1 m history file from a data vendor. Either
  would load into the same cache.

The original plan, kept for when CME data exists:

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
| 0 | **NQ only:** backfill Yahoo `NQ=F` (1 m ~7–30 days, 5 m/15 m ~70 days, 60 m ~2.4 years) into a local cache, a daily recorder that appends new 1 m/5 m bars, and `/api/chart/bars` serving cache + live Yahoo as one series. (Later, with CME data: the IBKR downloader for NQ and GC.) | nothing |
| 1 | The mechanical indicators: auto range-fib zone, premium/discount, market structure, environment, session boxes, AVR levels | 0 for history; works on Yahoo bars meanwhile |
| 2 | Strategy engine + chart overlays + checklist card | 1 |
| 3 | Strategy Tester (browser) + Node runner over the cache, trade list in bt17 column names | 2 |
| 4 | Real order flow: tick import, absorption / delta-flip / two-candle entry, big trades on futures | tick data |
| 5 | Gamma regime in the backtest: **built 2026-11-14** (recorder `pnpm bt:gex`, regime on every trade, `gamma: 'targets'`); waiting on history | months of recorded snapshots |

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
