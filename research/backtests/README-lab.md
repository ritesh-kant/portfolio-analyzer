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
| Size | ₹500 risk per trade (risk ÷ stop distance), capped at ₹50,000 notional. One position at a time per stock; it can re-enter any number of times a day (cap under *Base settings*, 0 = no limit). **Max open positions, all stocks** (*Base settings*, 0 = no limit) caps how many positions are open at the same time across stocks: a candidate that would fill while that many are open is skipped. |

"Structural" is the repo's own word (`levels.is_structural`): previous-day levels, opening range, round numbers,
volume shelves, prior-session highs, and pivots touched at least twice.

## Stop levels (Base setting)

**Stop under** (`stop_levels`): *Nearest structural support* (default, the frozen base) or *Today's own levels only*
(`local`). `local` leaves out the previous day and earlier sessions' highs and pivots (`prev_day`, `session_high`,
`session_pivot`; the same price often exists under two of these names, so dropping one does nothing) and puts the
stop under a pivot, opening-range, round-number or shelf level that today's tape made. A trade with none of those
within 3% is not taken; the target search is unchanged. It is a base setting, so it rebuilds the base data
(a separate cache) and is saved with every run. Exploratory 2026 result on 19 news trades: 9 survive, -₹51 vs
-₹1,010 for the same 9 with the default stop; mostly one trade (DEVYANI), so not evidence.

## Candle timeframe and entry trigger (Base settings)

Two switches under *Adjust base settings* change how a trade starts. Both are part of the base: a change
rebuilds the base data for that setting (a separate cache, ~25–70 s per year) and saved runs record it.
The defaults (5-minute candles, pattern trigger) are exactly the frozen base above; nothing already saved moves.

| Setting | Options | What it does |
| --- | --- | --- |
| **Candles** (`pattern_tf`) | 5-minute (default) · 1-minute | The bar the trigger is read from, and the **decision bar** every entry indicator is judged on. |
| **Entry trigger** (`entry_mode`) | Pattern, buy-stop (default) · Doji / spinning top after a downtrend · No pattern, market buy | Pattern: the base trade above. Doji / spinning top: see below. No pattern: see below. |

**1-minute candles.** The detector already supports them. The momentum screen, the pattern and the stamped
`day_chg_pct` / `rvol` / `macd1` are read at the 1-minute bar that just closed. The 5-minute inputs (EMAs, VWAP,
MACD, volume ratio, structural levels, the surge / uptrend / pullback features) are read from the **last 5-minute bar
that had closed** at the decision, never the one still forming (`base.last5_start`). A pattern needs ten earlier
1-minute bars the same day, so the first decisions come at ~09:26. Expect many more candidates than with 5-minute
candles (2026 NSE default band: 99 → 642) and noisier ones: 1-minute candles on small caps are a few trades each.
The detector's checks were audited on 5-minute candles, and its patterns had no measured edge there (BT29).

**Nine patterns + doji / spinning top after a downtrend** (`entry_mode="hybrid"`, code in `btlab/hybrid.py`). The
detector labels doji and spinning tops *neutral* and never trades them. This trigger keeps the nine patterns (the
chips still apply) and also trades a doji or spinning top that completes on a candle after the five candles before
it fell (`prior_trend == "down"`). Both are entered the same way (buy-stop one tick over the high, cancelled under
the low). To test the new candles alone, untick all nine pattern chips. `candles.py` is untouched; the candles are
relabelled in `hybrid.indecision_matches`. Measured 2022-26 on the frozen momentum screen: gross about 0% per
trade, about -₹86/trade at real costs on 1-minute candles, the same as the nine patterns (BT28/BT29's result
again), so treat any run with it as exploratory. Saved run `20261012-121518-3685a1` used an earlier standalone
version of this trigger plus an "Indecision candle" indicator, both since removed: its saved trades and metrics
still open, but it cannot be re-run.

**No pattern (market buy).** Turns off the pattern's price lines. There is no *trigger* price (pattern high + 1 tick)
and no *invalidation* price (pattern low), so there is no buy-stop and nothing to cancel.

* **Candidates:** every closed decision bar that passes the momentum screen (band + RVOL + price / liquidity).
* **Entry:** a market order at the **open of the next minute** after the decision bar, plus the same +0.03 % slip
  a buy-stop fill gets. Not filled from the entry cutoff (14:30 IST / 15:00 ET).
* **Stop and target:** unchanged. The nearest structural support / resistance, searched around the decision
  bar's **close** (it plays the part of the trigger price); same 0.3–3 % stop band, same "no level, no trade".
* **Who decides when to buy:** only the entry indicators. *Run experiment* therefore refuses this mode with no entry
  indicator ticked (it would buy on every bar that passes the screen), and **Pattern strength** cannot be combined
  with it. *Test each indicator separately* is disabled (its base trade has no entry).
* **Re-entry:** after an exit the next candidate that fills later can enter again, so set *Max entries / stock / day*
  to 1 for a one-shot-per-day reading. Trades carry `setup = market_entry`; `trigger` in the CSV is the reference
  close, and `strength` is empty.
* The pattern chips are ignored (shown dimmed) and the news indicator's reaction price is the decision bar's close
  (`close_dec`), which with 1-minute candles is a 1-minute close.

Candidate counts for the no-pattern mode are large (2026 NSE: ~15k with 5-minute bars, ~77k with 1-minute), so the
build is quick but the table is much bigger than the pattern one.

## US market

The **Market** switch at the top of step 1 flips the whole lab between NSE and US (NYSE/Nasdaq). Saved runs,
base data, the trial counter and the form settings are kept per market, and a run from one market never mixes
into the other. Everything not listed here is shared: the pattern detector, the structural levels, the 37
indicators and the exit replay. News and Market reaction support both markets; the descriptive
*Check news* works on both tapes: NSE announcements, or SEC EDGAR filings for a US run (8-K item codes, 6-K, 425; share offerings and routine forms never count as news; times are SEC acceptance times; a ticker with no CIK shows as unchecked, never as "no news"). The Ollama classifier remains NSE only.

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

The page has three tabs; the URL hash (`#build`, `#result`, `#runs`) keeps the one you are on across a refresh.
*? How it works* in the header opens a four-step explainer and a glossary (RVOL, VWAP, R, median, p-value …).

**Build experiment**

1. **Data** – market (NSE / US) and years. ● = base data ready for the current base setting; ○ = needs a
   one-time build, which Run does for you.
2. **Base trade** – four boxes (Stocks · Entry · Stop · Target and size) spell out the rule every experiment is
   compared with; a box turns amber with *changed* when you move it off the default. *Adjust base settings* holds
   the controls, grouped as *Which stocks*, *How a trade starts* (candles, trigger, patterns), *Stop* and
   *Size and limits*, plus *Reset base to defaults*.
3. **Indicators** – an *Entry* / *Exit* switch, a search box, category chips (News, Momentum & time, Trend,
   VWAP, MACD, Volume, Pattern, Risk & levels; Target, Stop management, Indicator exits) and *Selected only*.
   A ticked indicator opens its parameters, each showing its default, with *Reset to defaults*. Badges flag
   the rules: *needs News* (ticking Market reaction or News pullback ticks News for you) and *replaces target*
   (only one of those at a time; ticking another unticks the first and says so).

The sticky **Your experiment** panel on the right says in words what will run (data, base, each indicator with
the parameters you changed; × removes one) and checks it first, mirroring the server's refusals: a red line
blocks **Run** (no year, Market reaction without News, no entry indicator with the pattern trigger off, …),
amber lines warn (a base build is needed, re-entries with no cap), and a green line says when the exact
configuration was already run (Run then opens the saved result instantly). News experiments are never reused
by the server, so for those it offers the saved one instead. **Test each indicator separately** runs every
indicator alone (default settings) against the base and ranks them by return per rupee deployed.

**Result** opens after Run. At the top, a verdict: *Better than base*, *Worse than base*, *No clear difference*
(p > 0.1), *Base trade* or *No trades*, with the ₹ per trade change and a note when it still loses money at real
costs. Caveats follow (small sample, a mean carried by the 5 best trades, a median that loses, trial number). Then
six tiles with the base value and change under each, the full metric table, the cumulative-profit curve, how the
trades ended, the by-year table, and *How this was compared with the base* (kept vs dropped, paired change).
*Trade charts ↗* opens every trade on a candle chart (the BT32 report, Group by exit reason, pattern, month …);
*Edit these settings* puts the run back into Build.

**Saved runs** lists every run for the market. Each row names its indicators (entry in green, exit in blue), the
parameters that differ from the defaults, the base it ran on and any note. Click a row to open its result; star,
filter by kind or base, search, tick two or more to **Compare** (best value per row in bold, curves overlaid), or
delete. *Export runs (CSV)* in the header downloads them all. Every run is **saved** (`lab_runs/<id>/run.json`)
with the exact indicators and parameters, the base settings, the git commit, the metrics and the comparison with
the base.

Command line (same store): `pnpm bt:lab build --years 2024,2025`, `pnpm bt:lab apply --years 2026 --with above_vwap,vwap_exit`,
`pnpm bt:lab runs`, `pnpm bt:lab indicators`.

### News and Market reaction (two entry indicators, NSE and US)

Two checkboxes, so the filing effect and the tape's confirmation can be tested separately:

* **News** takes a trade only if the stock had a **material company filing** (NSE announcements or SEC EDGAR
  filings) in the look-back window (default 24 h, max 72 h). No price test. Owns *lookback* and *scope*.
* **Market reaction** additionally needs the price to have risen by at least *min move %* (default 2) since
  that filing, with RVOL ≥ *min* (default 3). It measures from the filing News finds, so it needs **News**
  ticked too (Apply refuses it alone). Owns *min move*, *min RVOL* and *volume confirmation*.
* Ticking both reproduces the old combined indicator exactly. Saved runs that used it
  (`news_reaction`) still load and re-run; it is just no longer offered in the list. With
  `post_news` volume, the volume test also decides which filing counts, so News + Market reaction
  differs slightly from News alone in that mode.

**News-bar trigger** (News → *Entry trigger* = `news_bar`). Drops the candlestick: the trade is the **first
bar, per stock per day, that passes every ticked entry indicator** (the filing and its lookback, Market
reaction's price and volume tests, anything else ticked) **within *News bar within* minutes of the filing**
(default 30; for a filing before the open or on an earlier day the clock starts at the day's open) **and with
price up at least *News bar: price up since filing* (default 0.5%) since the filing**, so News alone never buys
a stock that is falling on its news. Set the floor to 0 to allow flat bars. Saved news_bar runs from before
the floor existed keep their trades.csv but are not rebuilt. It is
bought at the next minute's open, with the structural stop and target as in No pattern. Choosing it
switches the base trigger to *No pattern* by itself (the server does too, and the saved run records it).
After a stop-out the same stock is not re-entered that day, even on a later bar or another filing. Why
it exists: the pattern trigger waits for a doji / spinning top, which a straight-line rally does not print, so
it entered AEGISLOG at 13:38 for a 12:32 filing; the news bar enters at 12:51. The base momentum screen
(day change, RVOL) still applies at the bar, so loosen it if the stock has not cleared it yet. A trade needs
a structural support 0.3–3% below and a resistance above, so some bars never become candidates. First
look (2026, day chg ≥ 1%, RVOL ≥ 1.5, 1-minute bars, Market reaction ≥ 1% / post-news RVOL ≥ 1.5, window 30,
tap-high ceiling + 2R target): 42 trades, −₹228 per trade (median −₹580), 29% win, 27 stopped out, exploratory
and on a window already tuned on.

**Pullback number (from / to)** and **News pullback number (from / to)** (entry indicators `pullback_ordinal` and
`news_pullback`; the first was "First / second pullback only"). *Pullback from* and *Pullback to* pick an exact
range: 2 to 2 is only the 2nd, 2 to 3 the 2nd and 3rd.
* *Pullback number* counts confirmed 5-minute swing highs after the day's first sharp advance (the engine's own
  count, 1 = first pullback; default 1 to 2, unchanged). It does not check that price is currently pulled back.
* *News pullback number* needs News ticked and keeps a bar only while it sits **inside** a chosen pullback of the
  news move, so a candlestick pattern must form during the pullback: use it with the **pattern** trigger (hybrid or
  nine patterns), not news_bar. Pullbacks are counted from the filing as confirmed swing highs (1 = the first
  pullback after the news spike; default 1 to 1). A bar is inside the pullback only while the latest confirmed swing
  high has **not been traded above since it printed**; once price makes a new high the stock is rallying again, not
  pulling back, until a new peak is confirmed. There is no percentage depth. *Swing highs on* picks the candle, 5m or
  1m: a swing high is confirmed 3 candles after it prints (15 minutes on 5m, 3 on 1m) and is read from the last closed
  candle of that timeframe; the "exceeded" test reads 1-minute highs up to the decision bar. A bare count once
  labelled a bar at a new high "pullback 1" (AEGISLOG 2026-05-29 12:37), hence the in-pullback phase.
First look (2026, hybrid pattern trigger on 1-minute candles, News 2 h, tap-high ceiling + 2R target): pattern + News
alone 52 trades, −₹384/trade; in news pullback 1 (5m) 16 trades, −₹262; 1 to 2 (5m) 23, −₹289; 1m candles 17, −₹398.
With the `news_bar` trigger the pullback range mostly picks the first bar inside it. Exploratory, one tuned window.

Everything below is as of the decision
bar: the reference price is the first 1-minute open at or after an in-session filing, including one on a prior
trading day. Off-session filings (pre-open, after the close, weekends) use the first 1-minute open after publication, so the overnight gap is *not* counted as a reaction (revision 4; earlier runs measured from the prior close). An in-session filing with no
observable post-publication minute on its day before the decision is ignored. The reaction
price is the decision bar's close. Exchange queries ("spurt in volume")
never count (the move caused them). Share offerings (including NSE securities issues, QIPs, preferential
and rights issues) are excluded even under `material+unclear`.

* **Volume confirmation `session` (default):** cumulative time-of-day RVOL, including volume before
  publication. The base RVOL floor still applies, so a lower plugin threshold cannot loosen the base.
* **Volume confirmation `post_news` (optional):** today's complete 1-minute bars after publication and
  before the decision, divided by the average volume for those same minutes in the preceding 20 sessions.
  At least 10 prior observations per minute are required. For an overnight/prior-day filing, the window
  starts at today's regular-session open. Missing tape/history rejects the candidate; it never falls back
  to session RVOL. Both price and volume must qualify for the **same filing**; among qualifying filings,
  the largest price move counts. This is a distinct exploratory setting, not a proven improvement.
* **Trigger `pattern`:** the frozen base candlestick entry. Saved `pullback` / `breakout` modes have no
  implementation in this checkout and are rejected explicitly, rather than silently ignored.

NSE classification uses the subject only. Generic "Press Release" / "Updates" subjects remain unclear;
the descriptive Ollama labels do not change the entry gate. This is a filing gate, not complete news coverage.
US uses **SEC acceptance time**, which can lag the original press release (see
[SEC Form 8-K](https://www.sec.gov/forms/form8-k.pdf)); it must not be presented as the original news timestamp.
US reaction prices and volume use the regular session on the New York clock. Both Apply and single-trade
What-if support US filings. A ticker without a historical CIK has **unknown coverage**, not "no filing",
and blocks Apply. A historical `cik` in `.cache_sec_news/<SYMBOL>.json` is retained on subsequent fetches;
set it to the verified issuer CIK and prefetch again to restore coverage.

Filings come from `.cache_nse_news/` (shared with *Check news*) or `.cache_sec_news/`. The first Apply that
needs uncached dates asks to fetch them; US fetching requires `SEC_USER_AGENT` with a contact.

**Revision 3 (2026-10-08):** fixes the candidate-loading failure in Apply/prefetch; excludes NSE share
offerings; adds optional post-news volume and trade-level audit fields (`news_filing_at`, `news_headline`,
`news_reference`, `news_move_pct`, `news_rvol`); supports US What-if; separates unknown SEC coverage; and
records a fingerprint of filing inputs in `run.json`. New runs have new keys. Historical metrics and CSVs
remain intact. A missing historical trade CSV is not silently rebuilt with the new indicator, and a current
run cannot be rebuilt if its filing inputs have changed: restore its original CSV or Apply a new experiment.

### CVD divergence and Delta divergence (order flow, two entry indicators)

Python ports of the web chart's own detectors (`btlab/orderflow.py` ← `apps/web/lib/tv/cvd.ts`,
`cvd-divergence.ts`, `footprint.ts`, `delta-divergence.ts`), so a run tests the marks a trader sees on the chart.
Checked once against the TypeScript on 120 real 2026 symbol-days (5m and 15m, pivots 1–3): 2,562 divergences and
1,174 delta setups, 0 mismatches. Long side only: **bullish** CVD divergences (regular and, with *Kinds = all*,
hidden) and **sellers-trapped** delta setups. CVD and delta are estimates from 1-minute bars, not trades.

* **CVD divergence**: the signal is the close of the candle *Pivot* candles after the second swing low, which is
  when the swing is first known. The decision bar must close within *Known within* minutes of it (0 = that very
  bar). *Near delta setup* also needs a sellers-trapped setup within 3 candles of the second low (the chart's
  `+ Δ` tag), counted only if it had closed and was not already missed by then.
* **Delta divergence**: the signal is the close of the confirming candle.
* Use them with the **No pattern (market buy)** trigger to buy the minute after the signal. Stamped at Apply time from
  the 1-minute cache (the trade day plus two sessions before it), so no base rebuild. The live chart can show a
  divergence one candle early because it passes the still-forming candle to the detector. The Lab never does.

First look (2026 to 25 Sep, 15-minute candles, market buy at the next minute, default stop/target, **exploratory**,
one window already tuned on). Net at real costs per trade; the base is every momentum bar (4,496 trades, −₹117,
median −₹63):

| Arm | Trades | Win % | Gross ₹/trade | Net ₹/trade | Median | Entry above 2nd low (median) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CVD div, pivot 1 | 164 | 32 | −33 | −125 | −75 | 0.69 % |
| CVD div, pivot 2 | 78 | 35 | −20 | −113 | −83 | 1.05 % |
| CVD div, pivot 3 | 55 | 38 | +16 | −75 | −40 | 3.26 % |
| pivot 1 / 2 / 3 + delta near | 12 / 5 / 8 | 8 / 40 / 25 | −282 / −145 / −91 | −372 / −227 / −181 | | |
| Delta divergence alone | 58 | 38 | −34 | −116 | −79 | |

Waiting (pivot 3) pays ~2.6 points more above the swing low yet does no worse, because the swings it waits out are
the ones that failed; pivot 1 vs 3 is not significant (Welch p = 0.31, 55 trades), and every arm loses ~₹90/trade
to costs on a gross near zero. Pivot 1 and pivot 3 share only 23 stock-days: an earlier pivot is a different,
noisier set of swings, not the same signal sooner. Adding the delta setup leaves 5–12 trades: no reading.

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
To file it under a category in the page, add its id to `CATEGORIES` at the top of `btlab/static/lab.js`
(otherwise it shows under *Other*). A rule that needs another indicator also goes in `recipeChecks()` there, so the
page blocks the run before the server does.

* **Entry** plugin: a function `(candidates_df, params) -> boolean Series` over values already stamped in
  `base.py` (`close5`, `vwap5`, `macd1`, `vol_ratio5`, …). A *new* input means adding a column in `base._scan_day`
  (computed from bars up to the decision bar) and bumping `LAB_VERSION`.
* **Exit** plugin: a function `(ExitCfg, params) -> None` that sets a switch; implement the switch in `sim.simulate`
  and add a test in `tests/test_sim.py` for its ordering.

## Not included (on purpose)

* **Add-on / pyramiding** – BT44 killed adding to winners both ways; it also changes capital deployed, which
  needs per-rupee accounting rather than ₹ per trade. Say so if you want it.
* Short side. Only US runs explicitly selecting the news plugin have a filing gate.
* The live daily guardrails (3 strikes, size ladder): a pool replay walks one stock at a time and has no coherent
  day-level P&L to apply them to – same limitation as bt17.

## Where things are

| | |
| --- | --- |
| `btlab/base.py` | the base rule (incl. `pattern_tf`, `entry_mode`), candidate builder, feature stamping |
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

## Fixed range volume profile (chart overlay)

Every trade chart (lab runs and dashboard runs; code in `bt32_assets/report.js`) has TradingView's **Fixed Range Volume
Profile** drawing tool: volume traded at each price over a time range, as a horizontal histogram on the price pane.

* **Draw**: click **▥ Volume profile**, click the first candle, move the mouse (the profile follows the cursor), click the
  last candle. Esc cancels. Draw as many as you like.
* **Edit**: click a profile's histogram to select it; drag either anchor (circle or edge line) to change the range, or drag
  the histogram to slide the whole range. Click empty chart to deselect; Delete / Backspace removes the selected one.
* **Settings** (row under the toolbar, for the selected profile; new ones copy the last settings): rows layout *Number of rows*
  (default 24) or *Ticks per row* (₹0.05 / $0.01 ticks), value area % (default 70), volume *Up / down* (blue / yellow) or
  *Total*, placement left / right, width (% of the range, default 70), POC and VAH / VAL lines on or off.
  **Quick range** sets *Open → entry (as-of)*, *Whole session* or *Entry → exit*.
* **Reads**: POC (row with the most volume, red, extended right), VAH / VAL (value area, blue dashed), each in % vs the buy,
  and whether the buy is inside, above or below the value area. The selected profile's prices are tagged on the price axis.
* **Look-ahead**: a profile that reaches the buy minute includes bars the entry could not see, and its info line says so.
  *Open → entry (as-of)* stops before the fill minute: use that one to judge an entry.
* Built from the raw 1-minute bars whichever candle size is shown; a bar's volume is spread over the rows its high-low range
  overlaps, counted as up volume when it closed above its open. The value area grows from the POC one row at a time, taking
  the heavier neighbour. Not built: TradingView's *Developing POC / VA* and *Delta* volume.
* Profiles are remembered per symbol + date in the browser (`localStorage`). Display only: they feed no entry or exit rule.

## History: earlier sessions on the trade chart

The **History** menu on every trade chart (lab and dashboard runs) adds the sessions before the trade: *+5 days*,
*+10 days*, *+1 month* (21 sessions) or *+3 months* (63). They are fetched on demand from the 1-minute cache
(`GET /api/lab/run/<id>/trade/<i>/history?days=N`, `bt32_strategy_report.history_1m`, about 0.1–0.3 s) and never
change the saved trade. Candle sizes **1m · 5m · 15m · 1h · D** all work across the days; EMAs and MACD run
continuously (so EMA200 exists from the first candle of the trade day), VWAP restarts each session, and a volume
profile can span several days. The default window on 1m–15m candles is the trade day and the two sessions before it;
zoom out or pan left for the rest. The choice is remembered in the browser and applies to the next chart opened.
Only sessions BEFORE the trade are loaded: nothing after the trade day is ever shown.
