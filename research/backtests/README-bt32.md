# BT32 — backtest a handful of symbols and look at every trade

Two steps: run the engine over some symbols, then draw what it did. Both read
the parquet bar cache in `research/backtests/.cache_upstox/1m/`, so a run over
already-cached symbols needs no network and no Upstox token.

## The one-liner

```bash
pnpm bt:demo
```

Runs the deployed strategy over 15 mid-caps for 2024 and writes
`bt32_strategy_report.html`. Then:

```bash
pnpm bt:serve
```

and open <http://localhost:8899/bt32_strategy_report.html>. (The file is >1 MB,
which the desktop app's `file://` preview refuses — hence the tiny server.)

## Running it yourself

`pnpm bt` is bt17, `pnpm bt:report` is bt32. Pass flags after `--`:

```bash
pnpm bt -- --attention --multi-entry \
          --start 2024-01-01 --end 2024-12-31 \
          --symbols TITAN,TATASTEEL,IDFCFIRSTB --tag mytest

pnpm bt:report -- --trades research/backtests/bt17_trades_mytest.csv \
                  --output research/backtests/mytest.html
```

Useful bt17 flags:

| flag | what it does |
| --- | --- |
| `--attention` | replay the **deployed** arm (`attention_1m_merged`): +1.5%/1.5× promotion, 5-min trend context, high-volume 1-min confirmation, resting buy-stop fills, resistance-breakout requirement, one false-break reclaim, resistance-state exits |
| *(omit it)* | replay the legacy path instead: the 7 Warrior setups on 4–8% movers with RVOL ≥ 3 |
| `--multi-entry` | re-enter a symbol after its trade closes (matches the live default since 2026-09-13) |
| `--exit-mode` | `fixed_2r` \| `trend_min` \| `trend_full` \| `trend_resistance_state` |
| `--live-fill` | **replay entries the way production fills them** (`resting_sized` + the live-measured 0.03% entry slip). The default `next_open` is a legacy model that is ~0.22%/trade worse than the deployed system — see `research/hypotheses/2026-09-15-resting-buy-stop-execution.md` |
| `--entry-slip-pct` | slippage on resting fills, % of the trigger (default 0) |
| `--fill-mode` | `next_open` \| `trigger` \| `future_trigger` \| `resting` \| `resting_sized` |
| `--symbols` | comma-separated; omit to run the whole NIFTY 500 (slow, and it will fetch) |
| `--tag` | suffix for the output CSVs |

bt32 flags: `--trades`, `--output`, `--max-charts N` (0 = all; charts dominate
the file size), `--title`.

## What the report shows

One card per trade, with the same chart controls as the **/momentum** review
chart in the web app. Three stacked panels:

* **Price** — candles, EMA9, EMA20, VWAP (dashed), every support/resistance
  level with its source, `BUY` / `SELL` / `Stop` / `Target 2R` lines, ▲/▼ entry
  and exit markers, the holding period shaded, and every candlestick formation
  bracketed and labelled with its strength multiple.
* **MACD** — 12/26/9 histogram, line and signal, which is what the trend exits
  read.
* **Volume** — per-bar volume, plus the day RVOL and the 5-minute volume ratio
  at entry.

Controls (all per chart):

| | |
| --- | --- |
| **Timeframe** | 1-minute or 5-minute, switched for every chart at once from the side panel |
| **Zoom / pan** | `− Zoom` `+ Zoom` and `←` `→`; **drag** the chart to pan |
| **Price zoom / pan** | `− V-Zoom` `+ V-Zoom` and `↑` `↓` — separates stop, entry and target when they nearly coincide; drag pans vertically once zoomed |
| **Pinch** | ctrl+scroll or trackpad pinch zooms **both** axes around the cursor |
| **Keyboard** | click a chart, then `+` `−` zoom, `0` reset, `←` `→` pan, `↑` `↓` price |
| **Crosshair** | follows the pointer with a price / MACD / volume readout on the axis it is over, a time badge, and a full OHLC + indicator line under the chart |
| **Full screen** | `⛶ Full screen`; `Esc` exits. Native Fullscreen API where the browser allows it, with a fixed-position fallback where it does not (some embedded webviews refuse the request) |

The side panel filters by symbol, exit reason and outcome; clicking a table row
jumps to its chart.

A faint, dashed formation sits on a candle smaller than
`candles.STRENGTH_WEAK_BELOW` (0.75×) of the stock's recent average range — the
label is right, the candle is not worth acting on.

Two honesty rules are built in:

* **Levels are recomputed as of the entry bar only.** Drawing the day's final
  support/resistance would show a chart the strategy never had. A dashed level
  marked `?` is a single unconfirmed pivot; solid ones are structural.
* **Indicators are computed in the browser from the raw 1-minute bars**, the
  same way `momentum-trade-chart.tsx` does, so the report and the /momentum page
  cannot drift apart and the payload never carries the same numbers twice.
  Verified against the engine: the 5-minute resample is bar-for-bar identical
  and EMA9 / EMA20 / VWAP / MACD agree to ~12 decimals, **including the MACD's
  NaN warm-up**. (The /momentum chart substitutes 0 for the MACD line's warm-up,
  which pulls its signal line toward zero for the first ~30 bars; this report
  matches the engine instead, because the engine's exits read the pandas
  version.)
* **Costs are shown twice.** bt17's CSV is netted at its +40 bps/side Gate-0
  stress; the report also recomputes a realistic net per trade with
  `calc_costs`, the same itemised MIS model the engine books exits with
  (brokerage cap, sell-side STT, exchange, SEBI, stamp, GST) — which lands
  around 0.21% of notional per round trip.

## Caveats

* A run over symbols/years **not** in the cache will fetch from Upstox and needs
  `UPSTOX_ACCESS_TOKEN` in `.env`. A full NIFTY 500 pull is hours.
* bt17 applies the filters knowable historically (price, 20-day turnover,
  day-change, RVOL). Free-float, promoter and band filters are forward-only and
  are **not** applied — the live universe is stricter than the backtest's.
* `pnpm bt:demo` re-measures an already-measured window. It is a **descriptive**
  report for inspecting mechanics, not a new test — new hypotheses need a fresh
  window and a pre-registration (`research/hypotheses/`).

## Related

* Engine the backtest replays: `apps/signal-engine/src/momentum_trader/engine.py`
* Setup definitions: `research/specs/warrior-patterns-nse.md`
* Candlestick-formation report (different question): `README-bt29.md`
