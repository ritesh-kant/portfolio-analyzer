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
  fields of `ArStrategyCfg` (`range` = `both`/`week`/`swing`, `rangeDays`, `legTrades`, `entry` = `pullback`/`close`/`lvn`,
  `entryWait`, `windowStart`, `windowEnd`, `flatAt`,
  `minRR`, `swingMult`, `confirmWithin`, `stopBuffer`, `allowWarnings`, `requireOutsideValue`, `requireStack`,
  `confirm` = `candles`/`orderflow`, `absorbVol`, `flipShare`, `valueRows`, `envRule` = `structure`/`efficiency`,
  `compress`, `sideways` = `warn`/`block`, `envWindow`); times as `HH:MM` New York.
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

## Check the trades

`node research/backtests/ar_validate.mjs` (same `--interval/--from/--to/--set` as `bt:ar`) re-checks every trade
from the raw candles with its own maths. **engine** lines test the engine against its own rules: real swing prices,
0.705/0.886, first touch, no close past 0.886, confirmation candle, fill, stop, target, exit, points. A FAIL there is
a bug. **course** lines test the trade against the video: 1h higher highs/lows, 09:30–11:00 New York, zone outside the
leg's value area. A NO there is a place where our defaults differ from the course.
First run (2026-11-26, 5 m, 09-03 → 11-24, the old swing rule): 6 trades, every engine check passed. All 6 were
confirmed after 11:00, and 4 of 6 had the zone inside the value area. In his 09:30–11:00 window: 0 trades.

## The week range (default since 2026-11-26)

His condensed course (`WEHgjZhg5bI`, notes: `research/specs/orderflow-course-ar-model.md` "Second source") draws the
Fib on the hourly "most active range". `range=week` codifies it:
- **The range** is the highest high and lowest low of the last `rangeDays` (7) days, drawn from the earlier one to
  the later one.
- **The trend** comes from the same range: high first = down (sell the premium), low first = up (buy the discount).
  A close past its 0.886 makes the trend unclear until a new high or low.
- **Re-arming:** while price sits in the zone, the setup is watched again after each confirmation window, up to
  `legTrades` (3) trades per range.

It reproduces his 20–28 Oct 2026 zone (29,781–29,985 against his 29,781–29,983). `range=swing` is the older rule
(latest swing of `swingMult` × ATR, with `trendRule`). The Range Fib Zone indicator draws the same week range by
default.

First look (exploratory, 5 m, 09-03 → 11-24, NQ × 1, `ar_runs/20261126-122039_5m_week_vs_swing/`):

| | week range (A) | swing (B) |
|---|---|---|
| Trades | 11 (0 targets / 8 stops / 3 flat at 16:00) | 6 (1 / 5 / 0) |
| Net after costs | −$5,936 | −$1,540 |
| Median per trade | −$862 | −$805 |
| Average / median R | +0.00R / −1.01R | −0.56R / −1.02R |

No trade is in both runs. Week-range targets are 500–2,600 pts away, so no target was hit inside a day: the two
winners (+6.25R, +2.31R) were closed at 16:00. In his 09:30–11:00 window the week range takes 1 trade (11-23, −0.31R
at 16:00). Every engine check in `ar_validate.mjs` passed on all 11 trades.

Known edge: the window rolls. When the week's high or low turns 8 days old, the range moves to the next extreme. On
28 Oct the 21 Oct high aged out during the day, and the range became a slightly lower one that had already been
closed through, so the trend read "unclear" a few hours before the real break at 16:00.

## Breakeven and trailing stop (default since 2026-11-26)

His live trades (4 h course 3:33–3:48, `research/specs/orderflow-course-ar-model.md` "Trade management"): "we're 25
points in profit, stop's going to break even", then "stop is going to actually trail behind this 5-minute candle",
or "right above … the POC of the last 15-minute candle". Settings:
- **`breakeven`** (25), **`breakevenUnit`** (`points`; `R` on NSE, default 0.6 there): once a completed candle has
  gone that far past the entry, the stop moves to the entry.
- **`trail`** = `candle` (default), `poc15` (POC of the last 15-minute block, from the footprint) or `off`. It starts
  after breakeven; with `breakeven=0` it starts right after the fill.

**No look-ahead:**
- Every move is decided at a candle's close and used from the next candle.
- The fill candle moves nothing.
- A stop only tightens and is never placed past the close.
- Exits at a moved stop are labelled `breakeven` or `trail`, and R is still measured against the initial stop.
- `ar_validate.mjs` replays the moves with its own code; the `poc15` trail is not re-checked there.

Not modelled: his DOM references (trailing above "large sellers"), and trailing tighter in positive gamma or where buyers
already defended once.

First look (exploratory, 5 m, 09-03 → 11-24, week range, `ar_runs/20261126-131905_5m_manage_vs_none/`):

| | breakeven 25 + candle trail (A) | none (B) |
|---|---|---|
| Trades | 14 (7 stop / 2 breakeven / 4 trail / 1 end of day) | 11 (8 stop / 3 end of day) |
| Win rate | 29 % | 18 % |
| Net after costs | −$6,910 | −$5,936 |
| Median per trade | −$496 | −$862 |
| Average / median R | −0.29R / −0.66R | +0.00R / −1.01R |

On the 11 trades both took:
- **Rescued:** two losers (10-01 −$1,206 → −$14; his 10-27 short −$862 → +$701).
- **Cut:** the only two big winners (09-30 +$2,966 → +$151; 10-21 +$1,368 → +$114).
- **Re-entries:** the 3 extra trades it allowed add +$339.

Net −$974. The same pattern this repo has seen before: on a signal with no measured edge, management that raises the
win rate and the median tends to cut the few large winners (`project_pv_overlay_falsebreak_coststop`, BT46). n = 14:
nothing is settled; a believable test needs a BT number.

## Environment from 4h structure (default since 2026-11-26)

The older read (`envRule: 'efficiency'`) called a market trending when net move ÷ distance travelled ≥ 0.35 over the
last 12 one-hour and 6 four-hour candles. That fails in three ways:
- a staircase trend (rally, pause, rally) travels far back and forth, so it reads sideways;
- 12 hourly candles are half a day on NQ but 2 sessions on NSE;
- longer windows read sideways almost everywhere (NIFTY 0.08, NQ 0.10 over 5 sessions).

The new read (`envRule: 'structure'`, `structureReads` / `dayRangeRatio` in `ar.ts`) follows his steps 1a/1b:
- **Trending:** on 4-hour candles, higher highs and higher lows (or a close more than 0.25 × ATR past the last swing
  high) = up; the mirror = down. A zone against it fails.
- **Sideways:** the swings overlap (a higher high with a lower low, or the reverse). This warns: his condensed
  course says to fade the range's extremes there, not to stand aside. `sideways: 'block'` refuses it.
- **Choppy:** the last trading day's range (counted in 1-hour candles, so a day means the same on NSE and NQ) is
  under `compress` (0.6) × a normal day's. No trade.
- **The 1h** is shown but does not decide. During a pull-back into the zone it usually points the other way.

Labelled moments (`envRule` old → new):

| Moment | Old | New |
|---|---|---|
| NIFTY 1h, 11-23 13:15 (you: up) | fail, 1h + 4h sideways | pass, 4h up: higher highs and higher lows |
| NQ 5m, 11-24 21:45 IST (you: down) | pass | pass, 4h down: lower highs and lower lows |
| His MNQ short, 27 Oct 10:00 ET | fail, sideways | warn, 4h sideways (he took it) |
| His MNQ short, 28 Oct 10:00 ET | fail, choppy | warn, 4h sideways |

NQ 5m, 2026-09-03 → 11-24 (`pnpm bt:ar --vs envRule=efficiency --vs sideways=block`, run
`20261126-154307_5m_env_structure_vs_efficiency`):

| | Structure (new) | Efficiency + block (old) |
|---|---|---|
| Trades | 15 | 14 |
| Win rate | 20% | 29% |
| Net after costs | −$6,524 | −$6,910 |
| Average / median per trade | −$435 / −$15 | −$494 / −$496 |

10 trades are the same in both. The change is about reading the market correctly; with 15 trades it says nothing
about edge.

## Location: week range + latest intraday swing (default since 2026-11-26)

The week range alone misses the pull-back you'd trade inside a trend. Example, NQ 24 Nov (IST):
- the leg was 26,509.75 (20:30) → 25,998 (21:40), with its premium zone at 26,359–26,451;
- price touched the zone at 23:00, peaked at 26,401.50, then fell to 25,027.50 the next day;
- the week range's zone (26,994–27,512) was 960 points away.

`range: 'both'` watches two zones, each followed every candle:
- **Week range:** as before, his zone.
- **Latest intraday swing:** the latest swing leg on the chart's own candles (`swingMult` × ATR, 2 by default) in
  the 4h trend's direction. There is none while the 4h is sideways.

Whichever zone price reaches first is traded (the week range if both). Otherwise the checklist shows the zone nearer
to price and names the other ("also watching …"). `range: 'week'` and `range: 'swing'` keep one of them only.

On 24 Nov the leg is confirmed as a swing at 22:35 IST, before the 23:00 touch, so there is no look-ahead. The
checklist passes Environment and Location there. It did **not** trade: the candle confirmation (a red candle closing
below the extreme candle's low within 6 candles) never fired, because price drifted down instead.

NQ 5m, 2026-09-03 → 11-24 (`pnpm bt:ar --set range=both --vs range=week`, run `20261126-155110_5m_loc_both_vs_week`):

| | Both (new) | Week only |
|---|---|---|
| Trades | 29 | 15 |
| Win rate | 31% | 20% |
| Net after costs | −$6,656 | −$6,524 |
| Average / median per trade | −$230 / −$15 | −$435 / −$15 |
| Net without the best 3 | −$10,255 | −$8,312 |

The 15 week-range trades are the same in both. The 14 intraday-leg trades together lose $132 after costs: no edge
either way, n tiny. NIFTY 1h (3 months of bars) has only 1 trade in either mode.

## Confirmation: why the 24 Nov setup did not trade (2026-11-26)

The candle rule (`candleRule: 'beyond'`, default) needs a candle in the trade's direction to close past the far end
of the candle that made the extreme, within `confirmWithin` (6) candles of the touch. On 24 Nov (IST):
- **Touch and extreme:** the touch was at 23:00; the extreme was the 23:15 candle (high 26,401.50, low 26,291.75).
- **Too late:** a red close below 26,291.75 came only at 23:45 (26,290.00), 9 candles after the touch.
- **Near miss:** the 23:15 close was 0.75 points short of the 23:10 candle's low.
- **His order flow** (estimated from 1-minute bars): delta flipped (+1,254, then −1,025 and −940), but the 23:15
  candle was not absorption: 0.9× the usual volume (the rule needs 1.5×), POC near its low. `confirm=orderflow`
  refuses it too.
- **Looser rule** (`candleRule: 'midpoint'`, a close past the extreme candle's middle, his "closes away from the
  level"): it enters at 23:10 (26,333.63) and is stopped at 23:15 (stop 26,391.84) by the spike to 26,401.50. The
  intraday leg is then used up.

NQ 5m, 2026-09-03 → 11-24 (the saved cache ends before the 24 Nov setup):

| | Default | `confirmWithin=12` | `candleRule=midpoint` |
|---|---|---|---|
| Trades | 29 | 35 | 39 |
| Win rate | 31% | 40% | 38% |
| Net after costs | −$6,656 | −$5,400 | −$1,470 |
| Average / median per trade | −$230 / −$15 | −$154 / −$15 | −$38 / −$15 |
| Net without the best 3 | −$10,255 | −$9,223 | −$6,656 |

- **What `midpoint` changes:** it adds 19 trades (+$5,324, median +$194, 11 wins) and drops 9 (+$543).
- **Why this is weak evidence:** it is the same 69-day window that the environment and location rules were reworked
  on today, so it is in-sample and exploratory. A believable result needs a pre-registered BT number and a window
  not used for tuning.
- The default stays `beyond`.

## Order flow by his words only (`flowRule: 'video'`, the order-flow default since 2026-11-26)

Our order-flow thresholds (absorption volume ≥ 1.5× the median of 12 candles, POC in the outer third of the
candle, flip ≥ 10% of volume) are not in either video. `flowRule: 'video'` keeps only what he says:
- **Absorption:** the candle holding the extreme has its POC in the wick at the rejection end (beyond its body)
  and closed against the push ("closed bearish" in his example). No volume threshold: he says "heavy volume" but
  gives no number.
- **Flip:** a later candle whose delta is on the trade's side at all, and that closes in the trade's direction. It
  cannot have made a new extreme ("next candle fails to continue").

On 24 Nov both versions refuse the setup. The 23:15 candle's POC is 26,302.50 (estimated), in the lower wick,
below its body (26,332.25–26,368.50). Your footprint shows the same row (912 × 553 near 26,305).

NQ 5m, 2026-10-14 → 11-24 (the only stretch with a footprint, estimated from 1-minute bars):

| | Candles (default) | Order flow, ours | Order flow, video | Ours, no volume test | Video + LVN entry |
|---|---|---|---|---|---|
| Trades | 14 | 0 | 3 | 1 | 5 |
| Net after costs | −$1,729 | $0 | +$813 | −$396 | −$422 |

46 setups failed the video rule, nearly all on the POC-in-the-wick condition. Three trades say nothing about
edge. The footprint for NQ is an estimate (each minute's volume spread over that minute's range), so the POC
position is approximate. Only recorded ticks (NSE) give a measured one.

## Order flow is the default confirmation (since 2026-11-26)

`confirm: 'orderflow'` with `flowRule: 'video'` is now the default, his step 4 in his words. The candle rule
(`confirm: 'candles'`) is our stand-in and stays available.
- **Needs a footprint.** The chart builds one whenever the AR Strategy is on: from recorded ticks where they exist
  (NSE), else estimated from 1-minute bars, which Yahoo keeps for only about 4 weeks. Older setups skip with
  "no footprint data for the order-flow check".
- **NQ 5m, all saved candles** (`pnpm bt:ar --vs confirm=candles`, run
  `20261126-165455_5m_default_orderflow_vs_candles`):

| | Order flow, video (new default) | Candles |
|---|---|---|
| Trades | 5 | 29 |
| Win rate | 60% | 31% |
| Net after costs | +$61 | −$6,656 |
| Median per trade | +$347 | −$15 |
| Net without the best 3 | −$1,494 | −$10,255 |

  56 setups before 2026-10-14 could not be checked: no footprint. Five trades say nothing about edge.

## Which swing is the intraday leg (`legRule`, 2026-11-27)

**The problem.** The intraday leg used to be the *latest* swing leg (2 × ATR zigzag). A pull-back inside a leg
produces a smaller swing, and that replaced the leg the trader is watching. NIFTY futures, 26 Nov:
- **Your leg:** 25,834.40 (09:45) → 26,093.00 (10:55), zone 25,863.88–25,910.69.
- **12:50:** an inner swing confirmed, 25,981.00 → 26,069.90 (a lower high). It replaced your leg.
- **13:10:** that inner swing's zone was cancelled, so nothing was watched when price reached your zone (13:50).

Rules tried on your two examples (NIFTY 26 Nov 13:50, NQ 24 Nov 23:00 IST):

| Rule | NIFTY | NQ |
|---|---|---|
| A latest swing, 2 × ATR (old) | 25,981 → 26,069.90 ✗ | 26,509.75 → 25,998 ✓ |
| A latest swing, 3 × ATR | ✓ | ✓ (a tuned number, fragile) |
| B structure leg, any day | 25,480 → 26,114.10 (2-day leg) ✗ | ✓ |
| C largest unbroken leg, last 6 h | ✓ | ✓ (6 h is a tuned number; misses staircases) |
| **D structure leg within today's session** | **✓** | **✓** |

**D, `legRule: 'structure'` (default),** `structureLeg` in `ar.ts`:
- A swing beyond the leg's end (a higher high for an up leg) starts or extends the leg, from the lowest low since
  the previous leg end.
- A swing that stays inside the leg (a lower high) is an internal pull-back and changes nothing.
- A low under the leg's origin ends it.
- Only today's swings count (a gap over an hour starts a session). Before today has a leg, yesterday's is used.
  Longer legs are the week range's job.

It adds no new number and follows his "latest clear leg" and "higher highs and higher lows".

NQ 5m, all data: with order-flow confirmation the same 5 trades either way (confirmation decides); with candle
confirmation 25 trades, −$5,728, vs 29 trades, −$6,656 with `latest`.

**26 Nov after the fix.** Your zone is watched and the touch at 13:50 registers. Order flow then refused it because
of three rules that are ours, not his:
- **Absorption only on the extreme candle.** 13:55 (the low) is not absorption: POC in the body, closed red. 14:15
  is: POC in the lower wick, closed green. `absorbAt: 'zone'` reads any candle at the level that price has not gone
  past since.
- **Confirmation within 6 candles.** The flip came at 14:25 (+4,912), 7 candles after the touch.
- **Reward ÷ risk ≥ 1.5.** With both the above relaxed it confirms, but RR is 1.35. The stop is under the 13:55 low.

NQ 5m, all data (exploratory, n tiny):

| | Default | `absorbAt=zone` | + `confirmWithin=12` | + `minRR=0` |
|---|---|---|---|---|
| Trades | 5 | 7 | 7 | 12 |
| Net after costs | +$61 | −$699 | +$190 | −$4,620 |
| Median per trade | +$347 | −$351 | +$347 | −$183 |

With all three relaxed, 26 Nov trades: long 25,969 at 14:30, trailed out at 26,022.61 (14:50).

## Order flow by his decision tree (`flowRule: 'tree'`, default since 2026-11-27)

The one-candle rule (`flowRule: 'video'`) made two of his signs mandatory on a single candle: POC in the wick and a
close against the push. He lists the signs of absorption (heavy volume, strong delta, the next candle fails to
continue, POC at the rejection end) but never says one candle must show them all. His decision tree is
"aggression failed to produce a result? pressure flipped?" `[S+V 3:09–3:10]`. `flowRule: 'tree'` codes that, with
no numbers of ours (`failedAggression` in `ar-orderflow.ts`):
1. **Aggression at the level:** a candle since the touch that traded into the zone, with delta on the pushing side
   (sellers for a long). Of those, the one with the most pushing delta.
2. **Failed:** no candle after it has traded past its low (high for a short).
3. **Pressure flipped:** a later candle whose delta is on our side and that closes our way, still without a new low.

POC in the wick is shown in the checklist when present, never required.

**NIFTY futures, 26 Nov** (estimated footprint, as on your chart):
- Selling at 13:55 (delta −27,245 on 53,105) failed: 14:00 made no lower low.
- Delta flipped +20,051 at 14:05.
- Long 25,925.60 at 14:10, stop 25,877.62, trailed out at 26,022.61 (14:50), +97.0 points.

**NQ, 24 Nov:** buying at 23:00 failed and delta flipped at 23:05, so it shorted at 26,333.63 (23:10). The 23:15
spike to 26,401.50 stopped it.

**NQ 5m, all data** (footprint only from 2026-10-14):

| | Decision tree (new) | One candle | Candles |
|---|---|---|---|
| Trades | 12 | 5 | 25 |
| Win rate | 58% | 60% | 40% |
| Net after costs | +$2,439 | +$61 | −$5,728 |
| Median per trade | +$334 | +$347 | −$15 |
| Net without the best 3 | −$161 | −$1,494 | −$7,878 |

**NIFTY futures 5m, 2026-09-30 → 11-27** (footprint from 10-27; one lot of 65; costs from the Strategy Tester's NSE
futures model, ~₹975 a round trip; script in the session scratchpad, not the repo):

| | Decision tree (new) | One candle | Candles |
|---|---|---|---|
| Trades | 5 | 1 | 5 |
| Winners | 1 | 1 | 2 |
| Gross points | −24.4 | +35.3 | +104.1 |
| Net after costs | −₹6,550 | +₹1,296 | +₹1,786 |
| Median per trade | −₹2,150 | +₹1,296 | −₹988 |

On NIFTY the decision tree's only winner is 26 Nov; the other four were stopped. 12 + 5 trades are far too few to
tell the rules apart; the decision tree is the default because it is his rule, not because of these numbers.

## A session that runs from the open: the leg starts at the opening low (NSE, 2026-11-27)

**The miss.** NIFTY futures 25 Nov: gap up to 25,730 at 09:15, straight rally to 26,114.10 at 11:45, pull-back
into the 0.705–0.886 zone (25,773.79–25,843.31) at 12:55–13:15. The strategy watched only the week range. The
swing finder (ZigZag of 2 × ATR) had no swing low on 25 Nov: it carried the 23 Nov 15:20 low (25,480) across
the overnight gap, so the morning's low was never a swing. The session's only swing was the 11:45 high, and
`structureLeg` needs a swing low before it. The fallback (23 Nov's leg) was also gone: 25,480 broke that leg's
origin.

**The fix (OUR rule, NSE only via `sessionLegs`).** `withSessionOpen` puts the session's opening extreme in front
of the session's swings: the lowest low before a first swing high (the highest high before a first swing low),
counted from when that swing is confirmed. It applies only where the market closes overnight; NQ is unchanged.

**25 Nov now.** The zone is touched at 12:55. Selling at 13:15 (delta −13,700 on 100,165, POC in the wick) fails,
and delta flips +11,039 at 13:20. Long 25,825.20 at 13:25, stop 25,763.58, trailed out at 25,863.34 at 13:35:
+38.1 points.

**NIFTY futures 5m, 30 Sep → 27 Nov** (footprint from 27 Oct; 1 lot of 65 assumed; Strategy Tester NSE costs):

| | Decision tree | One absorption candle | Candle rule |
|---|---|---|---|
| Before | 5 trades, 1 win, −₹6,550, median −₹2,150 | 1 trade, +₹1,296 | 5 trades, +₹1,786, median −₹988 |
| After | 6 trades, 3 wins, +₹5,219, median −₹259, without best 3 −₹9,696 | 3 trades, 3 wins, +₹10,915, median +₹1,475 | 8 trades, 4 wins, +₹2,577, median −₹340 |

The decision tree gains 25 Nov (+₹1,475) and 4 Nov (+₹8,145, target); 11 Nov drops out. Six trades: exploratory.

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

