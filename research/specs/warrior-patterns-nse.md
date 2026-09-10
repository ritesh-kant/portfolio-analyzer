# Warrior Trading patterns → NSE momentum trader: frozen spec v1

**Status:** frozen 2026-09-05. Thresholds here are the ones in
`apps/signal-engine/src/momentum_trader/setups.py`. Once forward capture starts
under `2026-09-05-momentum-catalyst-upstox-v2.md`, this file and those constants
change only via a new spec version + a new hypothesis file. Tuning a threshold
after seeing results is the p-hacking pattern this repo exists to prevent.

**Sources baked in** (all Warrior Trading, Ross Cameron):
1. *My Stock Selection Process & Criteria* — the 5-criterion scanner, the two
   entry rules, the 2:1 risk rule.
2. *Chart Pattern Study Guide* (112 slides) — ~15 intraday setups.
3. *Candlestick Pattern Reference* — 24 single/double/triple-bar shapes.

Nothing here reads chart images. Every pattern is a rule on open/high/low/close/
volume bars. The LLM is used for exactly one thing, the catalyst tag, and it is
not in the entry path.

---

## 1. Stock selection (the scanner's universe filter)

| # | Warrior (US) | NSE version (frozen) | Why it changed |
|---|---|---|---|
| 1 | 5× relative volume vs 30-day avg | **RVOL ≥ 3× time-of-day** (cum. volume so far ÷ 20-day avg cum. volume at same clock time) | Full-day-average RVOL understates a 10:30 spike ~4×; 3× on the honest measure ≈ 5× on the naive one. `indicators.relative_volume_by_time` |
| 2 | Already up 10% on the day | **Up +4% to +8%** vs prev close, and price still inside the +2%..+10% band the hypothesis locked | Most NSE names sit in a 10% or 20% circuit band; +10% is locked or next to it. BT15: up-gappers in NIFTY 500 fade −0.35%/trade |
| 3 | A news catalyst | **Hard event in prior 24h** (results / order win / regulatory approval / board corporate action). Ambiguous → 0. **No sentiment.** | Frozen definition from the v1 hypothesis §4. Sentiment is the step that failed in BT7–BT12 |
| 4 | $1–$20 price | **₹60–₹2,000** | Same retail-affordability idea |
| 5 | Float < 10M shares | **Free-float mcap ₹500–5,000 cr, promoter holding ≥ 50%, 20-day avg turnover ₹3–50 cr** | Share counts don't transfer across markets (face values differ). Live sample 2026-09-05: 0/120 NIFTY 500 names have <10M shares in the ₹60–2,000 band. Promoter holding is India's actual "low float" lever |
| + | (halts) | **Circuit band 10% or 20% only; not ASM/GSM/T2T** | Band-locked = zero sellers = no fill. Gate −1 finding, 2026-07-03 |

Warrior's stated exception — "a stock that made a big move yesterday and is
holding it" (continuation setup) — is captured as a flag `prev_day_gainer` on
the row, not as a separate entry path.

## 2. Setups (entry triggers) — LONG ONLY

All run on **5-minute** bars except micro-pullback (1-minute). "Trigger" is the
price that confirms the pattern; the paper position is filled at the **open of
the next bar after the trigger bar closes** (never at the trigger quote — that
was the BT9 look-ahead artifact). Stop = the pattern's invalidation low.
Target = entry + 2 × (entry − stop) (§4).

| Setup | Guide slide | Rule (frozen) | Trigger | Stop |
|---|---|---|---|---|
| `bull_flag` | Bull Flag Breakout, ABCD Flag | Pole: ≥ 2% run within ≤ 6 bars, pole closes in top 40% of its range. Flag: 2–5 bars, retraces ≤ 50% of pole, makes no new high, avg volume ≤ 80% of pole's. Then first bar whose high > previous bar's high. | prev bar high | flag low |
| `flat_top_breakout` | Flat Top Breakout; Double Top … Third Time It Breaks | ≥ 3 highs within 0.3% of the max over the last 12 bars form a ceiling; bar **closes** above it (a wick is not a break). | ceiling | low of the bars that tested the ceiling |
| `ma9_pullback` | Moving Average Pullback (1st / 2nd 5-min pullback) | EMA9 > EMA20 at the pullback; one of the prior 3 bars has low within 0.2% of EMA9; none closed below EMA20; then first bar with high > previous high. | prev bar high | pullback low |
| `vwap_reclaim` | First Pullback after Break of VWAP; VWAP Breakout | Price closed below session VWAP, then reclaimed it, then ≥1 pullback bar held above VWAP (low ≥ VWAP − 0.2%); then first new high. | prev bar high | pullback low |
| `orb15` | Break of Pre-Market Highs; Break of Pre-Market Pivot; 1-min Opening Range Breakout | NSE has no continuous pre-market, so all three collapse to: level = high of 09:15–09:30; first bar after 09:30 that **closes** above it. Only the *first* break counts. | ORB high | ORB low |
| `red_to_green` | Red to Green Move; Gap and Go Red-to-Green | Opened below prev close; a bar closes above prev close after the previous bar closed at/below it. | prev close | day low |
| `micro_pullback` (1-min) | Micro Pullbacks (Entry I/II/III); First Pullback 1-min; Whole-Dollar Break Micro Pullback | ≥ 2 green bars, one red/doji pause bar, then a bar whose high > pause bar high. | pause bar high | pause bar low |

**Chase guard (all setups):** if the trigger bar closes > 1% above the trigger
level, skip. Warrior enters at the break, not after the vertical bar; the
news-trader's `entry-chase-cap` found chased entries were the worst trades.

**Round-rupee levels** ("whole dollar / half dollar"): `indicators.round_levels_above`
returns the next minor/major round level (₹5/₹10 under ₹100; ₹10/₹50 under
₹1,000; ₹50/₹100 above). Recorded on every row as `next_round_level`; the
scanner does **not** trade off it in v1. Slides 18a–18f show as many fake-outs
as breaks.

## 3. Candlestick tags (observational only)

`candles.candle_tags(bars)` records which of these fire on the trigger bar:
doji, dragonfly_doji, gravestone_doji, hammer, inverted_hammer, spinning_top,
bullish_engulfing, bearish_engulfing, tweezer_bottom, tweezer_top,
morning_star, evening_star, three_white_soldiers, three_black_crows.
`morning_doji_star` and `rising_three` are also captured as **strict,
five-minute observational evidence**. Rising Three is a five-candle
continuation formation, not a three-candle pattern.

They are **tags, not gates.** Single-bar shapes have no documented stand-alone
edge, and Warrior himself uses them as confirmation inside a setup, not as the
setup. The forward log will show whether any tag shifts the outcome; if one
does, that becomes a *new* hypothesis, not a silent filter. Rising Three is
kept distinct from a bull flag in the audit log: the two overlap conceptually
but the bull-flag entry rule has additional volume and retracement gates.

## 4. Risk (from *Stock Selection*, "Risk Management")

- **2:1 reward-to-risk, always.** Target = entry + 2 × (entry − stop). Warrior's
  table: 2:1 breaks even at a 33% win rate; 1:1 needs 50%.
- **Risk-based sizing.** qty = risk_₹ ÷ (entry − stop), capped by max notional.
  Paper defaults: risk ₹500/trade, notional cap ₹50,000. `risk.plan_trade`.
- **Stop sanity band:** 0.3% ≤ stop distance ≤ 3%. Tighter is NSE tick noise;
  wider is not a "low risk entry".
- **Degenerate-stop guard** (added 2026-09-05, *not* a threshold change): a
  detector returns `None` when its stop is not strictly below its trigger. Every
  setup takes the trigger from a bar high and the stop from a bar low, so a
  zero-range bar (high == low) produces trigger == stop. The sanity band above
  does not catch this — `plan_trade` judges the band against the *fill*, and
  under `next_open` the trigger→fill drift manufactures a stop distance from
  nothing (KAJARIACER 2024-11-25 11:35: trigger == stop == 1217.35, filled
  1221.95, so a 0.38% "stop" on a level unrelated to the pattern). The 0.3%
  floor is deliberately **not** moved to the setup level: on the BT17 candidate
  log it would drop 1,011 of 2,948 candidates (34%) and 359 of 1,876 trades
  (19%), which is a v1 rule change, not a guard. `setups._has_stop_room`.
- **Exits, in priority:** (1) `false_break` — trigger bar broke a level and the
  next bar closed back below it → exit immediately (slides "Bull Flag Trap",
  "False Breakout Trap", "Bull Trap"); (2) stop; (3) target; (4) 15:15 IST EOD
  close (existing MIS logic). No time-stop in v1 — Warrior holds while the
  pattern is intact; the false-break exit is the pattern-broken signal.

## 5. Excluded from v1 (with reasons)

| Warrior setup | Why excluded |
|---|---|
| Bear flag, VWAP fade, MA pop, flat-bottom breakdown, trend-shift short, halt short | Short side. BT7 killed NSE intraday shorts (n=41); hypothesis is long-only. Could be its own file later |
| Halt resumption (long or short) | NSE has circuit bands, not halts. Band-locked names are excluded upstream |
| 5-min / 1-min "first candle to make a new high after N consecutive red" (reversal) | A dip-buy / reversal setup, opposite sign to momentum. BT14 measured intraday long-losers at −0.18% gross. Different hypothesis |
| Gap-down reversal, panic sell-off dip | Same: reversal family |
| Recent IPO breakout, multi-day parabolic, short squeeze | Daily-timeframe setups; not intraday capture |
| Pre-market chart quality (good/bad) | No continuous pre-market on NSE; the 09:00–09:08 pre-open auction is one print |
| "Buyout headline that ends up not being real" | This is the catalyst-quality problem; handled by the frozen hard-event definition, not a chart rule |

## 6. What the capture row records (schema for the v2 log)

```
date, symbol, setup, trigger_time, trigger_px, fill_px, stop_px, target_px,
day_chg_pct_at_trigger, rvol, catalyst, candle_tags, next_round_level,
prev_day_gainer, exit_time, exit_px, exit_reason, gross_inr, net_inr
```

`setup` is one of §2. A row per *fired setup*, one position per symbol per day
(first setup wins, in the §2 order). `catalyst` ∈ {0,1}, frozen definition.

## 7. Code

- `apps/signal-engine/src/momentum_trader/indicators.py` — EMA, session VWAP,
  time-of-day RVOL, round levels.
- `apps/signal-engine/src/momentum_trader/setups.py` — the seven setups,
  `false_break`, `scan_setups`.
- `apps/signal-engine/src/momentum_trader/candles.py` — the tag set.
- `apps/signal-engine/src/momentum_trader/risk.py` — 2:1 plan + risk sizing.
- Tests: `apps/signal-engine/tests/momentum_trader/test_patterns.py`.

Not yet built: the Upstox feed client, bar builder, and the Fargate scanner
loop that calls `scan_setups` each closed bar. See the v2 hypothesis §6.
