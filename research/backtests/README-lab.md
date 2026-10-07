# Backtest lab

One fixed **base trade**, a shelf of **plug-and-play indicators**, and a saved record of every
combination you try. Open it from the dashboard:

```bash
pnpm bt:dashboard        # then http://localhost:8898/lab
```

Everything lives in `research/backtests/btlab/`. The page is plain HTML/JS served by the same
`bt_dashboard.py` process (no Node, no build step), so `pnpm bt:dashboard` is the only thing to run.

## The base trade (frozen)

Changing any of this is a new `LAB_VERSION` in `btlab/__init__.py`; old saved runs then stay readable but
are never mixed with new ones.

| | |
| --- | --- |
| Stocks | Momentum names: day change **+4…+8 %** at the decision bar, time-of-day **RVOL ≥ 3**, price ₹60–2,000, 20-day turnover ₹3–50 cr (bt17's bands). Adjustable under *Base settings*; each setting rebuilds the base data (~20 s per year). |
| Trigger | A **bullish candlestick pattern** from `candles.py` (the v3 detector, untouched) completes on a **5-minute** bar. Nine names: hammer, inverted hammer, dragonfly doji, bullish engulfing, tweezer bottom, morning star, morning doji star, three white soldiers, rising three. Neutral/bearish names never trigger. |
| Entry | Resting **buy-stop one tick above the pattern's high**, live 15 minutes, dead if price trades under the pattern's low first. Fills at `max(trigger, open)`; +0.03 % slip when it fills at the trigger. No entries from 14:30. |
| Stop | The nearest **structural support** at least 0.3 % below the entry, 0.10 % under it. No support, or a stop wider than 3 % → no trade. |
| Target | The nearest **structural resistance**, 0.15 % under it (earlier sessions' highs included). None → no trade. |
| Exit | Stop · target · the bar starting 15:14 (closes at 15:15), whichever first. |
| Size | ₹500 risk per trade (risk ÷ stop distance), capped at ₹50,000 notional. One position at a time per stock; it can re-enter any number of times a day (cap under *Base settings*, 0 = no limit). |

"Structural" is the repo's own word (`levels.is_structural`): previous-day levels, opening range, round numbers,
volume shelves, prior-session highs, and pivots touched at least twice.

## US market

The **Market** switch at the top of step 1 flips the whole lab between NSE and US (NYSE/Nasdaq). Saved runs,
base data, the trial counter and the form settings are kept per market, and a run from one market never mixes
into the other. Everything not listed here is shared: the pattern detector, the structural levels, the 37
indicators and the exit replay (News + market reaction is NSE only).

| | NSE | US |
| --- | --- | --- |
| Bars | Upstox 1-minute cache (`.cache_upstox/`) | IBKR 1-minute cache (`.cache_ibkr_us/`, from `ibkr_us_history.py`), regular session, New York clock |
| Momentum band (default) | +4…+8 %, RVOL ≥ 3 | +10…+50 %, RVOL ≥ 3 (the US arm's own 10 % floor; editable) |
| Price / liquidity | ₹60–2,000, 20-day turnover ₹3–50 cr | $1–20 (the Warrior band), 20-day average traded value ≥ $0.3M |
| Tick | ₹0.05 | $0.01 (also used for the stop and the buffered target) |
| Last bar | 15:14 IST, entry cutoff 14:30 | 15:59 ET, entry cutoff 15:00 |
| Costs | itemised MIS model | the engine's IBKR model with a spread/impact cost (`us_costs`), priced at one level as bt32 does |
| Default risk / cap | ₹500 / ₹50,000 | $50 / $5,000 |

Limits: no float or news data exists for history, so those two screens of the Warrior guide are not applied, and the
cache holds only the late-September 2026 watchlist names (survivorship-biased, 78 symbols, 2026 only). Treat US
numbers as plumbing validation until a broader universe is downloaded. CSV column names still say `_inr`; the
`currency` column (`USD`) is what makes the candle-chart report draw dollars. CLI: `pnpm bt:lab build --market US --years 2026`,
`pnpm bt:lab apply --market US --years 2026 --with above_vwap`.

## Using it

1. **Data window** – tick years. A year marked ○ needs a one-time build of its base data; Apply does it for you.
2. **Base trade** – read-only summary, plus *Base settings* (momentum band, patterns, risk).
3. **Entry indicators** – each decides *which* base trades are taken. Tick, set parameters inline.
4. **Exit indicators** – each changes *how* a taken trade is closed. The three target
   replacements (fixed R, no target, checkpoint) are mutually exclusive – ticking one unticks the others.

**Apply ▶** runs the base trade (once per window, reused), then your selection, and shows the result next
to the base: trades, ₹ per trade (mean, **median**, mean **without the 5 best**), win rate, profit factor,
drawdown, cumulative curve, exit mix, by-year table, and a plain-English verdict. **⚡ Test each alone**
runs every indicator separately against the base and ranks them by return per rupee deployed.

Every Apply is **saved** (`lab_runs/<id>/run.json`) with the exact indicators and parameters, the base
settings, the git commit, the metrics and the comparison with the base. The *Saved runs* table survives
restarts; star, rename, add a note, compare up to four runs, **Load** a run's settings back into the form,
or export everything as CSV. *Open every trade on a candle chart* is the usual BT32 report (Group by exit
reason, pattern, month …) for that run.

Command line (same store): `pnpm bt:lab build --years 2024,2025`, `pnpm bt:lab apply --years 2026 --with above_vwap,ema9_exit`,
`pnpm bt:lab runs`, `pnpm bt:lab indicators`.

### News + market reaction (entry indicator, NSE only)

Takes a trade only if the stock had a **material NSE filing** (results, orders, deals, rating, dividend,
fund-raising, litigation… — `news.tier`) in the look-back window (default 24 h, max 72 h) **and** the price has
since risen by at least *min move %* (default 2) with RVOL ≥ *min* (default 3). Everything is as of the decision
bar: the reference price is the first 1-minute open at or after an in-session filing, else the previous close
(overnight/pre-open news). A filing with no post-publication minute before the decision is ignored. The reaction
price is the decision bar's close. Exchange queries ("spurt in volume")
never count (the move caused them). Several filings → the largest move counts.
Filings come from the `.cache_nse_news/` cache shared with *Check news*; the first Apply that needs symbols
not yet cached asks to fetch them (one polite request per symbol, ~2 s each, cached forever). Not available for US.

## How to read a result

* **Judge ₹ per trade, not total ₹** for filters that leave sizing unchanged. A filter can raise the total just
  by trading less. When a stop setting changes size, compare return per rupee deployed; the verdict does this.
* **Median and ex-top-5** sit next to every mean. On this strategy a handful of trades can carry a result.
* **Filters** (entry indicators) report *kept vs dropped*: did the trades it removed do worse than the ones
  it kept? **Exits** report a *paired* change on the trades both runs took. Both p-values use a normal
  approximation and are a guide, not a proof.
* A vetoed candidate lets a later one through, so a filtered run can contain trades the base never took.
  That is what live trading would do, and why the lab replays instead of deleting rows.
* **Trial counter.** Every distinct configuration run on a window is counted (deleting does not reset it) and
  shown beside the result. Try forty variants and a few will look good by luck. The hold-out rule was retired
  on 2026-10-04, so no window is untouched any more: the live paper record is the only untuned data.
* This lab is **exploratory**. It does not replace `research/hypotheses/`: anything you intend to believe or
  ship still gets a pre-registered hypothesis, a control and an arm (see AGENTS.md).

## Honesty rules built in

* Indicators are stamped **as of the decision bar** and are causal; levels, patterns and pullback counts are recomputed
  on bars up to and including it; the fill search starts the minute **after** it.
  `tests/test_real_data.py` scrambles the rest of the day and checks nothing moved.
* A 1-minute bar touching stop and target is a **stop**. The bar a buy-stop fills on can stop you out but never hit the target.
  A stop lifted by an event (breakeven, trail, checkpoint) applies from the **next** bar (the BT46 same-bar look-ahead).
  Indicator exits are judged on a **closed 5-minute bar** and fill at its close.
* Costs are shown twice: **real** (the itemised MIS model, incl. 5 bps/side slippage) is the headline;
  `net_inr`/`net_pct` in the trade CSV carry bt17's extra +40 bps/side stress so the file is dashboard-compatible.
* The base is replayed bar by bar and checked against an independent raw-bar replay (`test_real_data.py`).

## Adding an indicator

Add one `Plugin(...)` to `ENTRY` or `EXIT` in `btlab/plugins.py`. The UI, validation, saved runs and CLI pick it up.

* **Entry** plugin: a function `(candidates_df, params) -> boolean Series` over values already stamped in
  `base.py` (`close5`, `vwap5`, `macd1`, `vol_ratio5`, …). A *new* input means adding a column in `base._scan_day`
  (computed from bars up to the decision bar) and bumping `LAB_VERSION`.
* **Exit** plugin: a function `(ExitCfg, params) -> None` that sets a switch; implement the switch in `sim.simulate`
  and add a test in `tests/test_sim.py` for its ordering.

## Not included (on purpose)

* **Add-on / pyramiding** – BT44 killed adding to winners both ways; it also changes capital deployed, which
  needs per-rupee accounting rather than ₹ per trade. Say so if you want it.
* Short side and 1-minute pattern triggers. Historical US runs have no news/catalyst gate.
* The live daily guardrails (3 strikes, size ladder): a pool replay walks one stock at a time and has no coherent
  day-level P&L to apply them to – same limitation as bt17.

## Where things are

| | |
| --- | --- |
| `btlab/base.py` | the base rule, candidate builder, feature stamping |
| `btlab/plugins.py` | the indicator registry (one entry per checkbox) |
| `btlab/sim.py` | trade replay and every ordering rule |
| `btlab/runner.py` | selection + trade rows (dashboard CSV schema) |
| `btlab/metrics.py` | headline numbers, comparison with the base |
| `btlab/store.py` | saved runs, dedupe, trial ledger |
| `btlab/service.py`, `server.py`, `static/` | orchestration, HTTP routes, the page |
| `.lab_cache/` | candidate tables + per-day bars (git-ignored, rebuildable) |
| `lab_runs/` | `run.json` per run + `ledger.jsonl` (tracked); `trades.csv` (git-ignored, rebuilt on demand) |

Tests: `cd research/backtests && ../../apps/signal-engine/.venv/bin/python -m pytest btlab/tests -q`
(the real-data tests skip themselves when the 2026 base data is not built).

In a git worktree the 1.5 GB bar cache is not present; `btlab/paths.py` falls back to the main checkout's copy
(or set `MT_CACHE_ROOT` to the directory that contains `.cache_upstox/`).
