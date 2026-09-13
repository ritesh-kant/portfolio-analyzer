# Candlestick detector v2: evaluation report (BT27 + BT28)

Run date: 12 September 2026. Detector under test: `candles-v2-20260911`
(see [candlestick-recognition-v2.md](candlestick-recognition-v2.md) for the rules
and their sources).

> **Update, later the same day:** a second source-by-source research pass
> produced `candles-v3-20260912` — six rule corrections (hammer-family position
> vs the prior candle, Three Methods containment on bodies, soldiers/crows Near
> and Far tests, spinning-top cap removed). The diff and its sources are in the
> spec's *v3 revision* section. On the 30 audit sessions below, v3 labels
> 470 formations instead of 488; the hammer family roughly halves. The BT28
> numbers below are the v2 run; the v3 re-run writes to `bt28_*_2026_v3.csv`.

Two separate questions were asked, because they have different answers:

1. **Is the detector labelling the right candles?** → BT27, a visual audit.
2. **Does a correct label predict anything?** → BT28, a statistical census.

The detector passes (1). It fails (2) — and so does every pattern in the
reference sheet, on this data, at a size that matters.

---

## 1. BT27 — visual audit tool

`research/backtests/bt27_pattern_visual_report.py` writes one self-contained
HTML file (no CDN, opens straight from `file://`):

```sh
apps/signal-engine/.venv/bin/python research/backtests/bt27_pattern_visual_report.py \
    --symbols ABSLAMC,ACC,APOLLOTYRE,BEML,BSOFT --year 2026 --days 6
```

Contents:

* a candlestick chart per session, each detected formation shaded and
  bracket-labelled **in the chart**, coloured by direction;
* zoom/pan per chart, and a detection list where clicking a row scrolls to that
  session and zooms the chart to that formation, drawing its confirmation and
  invalidation levels;
* hover on a marked formation for the exact geometry, prior trend, and what
  price did next;
* a **near-miss** layer (checkbox): shapes that pass the geometry helper but
  were *not* labelled, each annotated with the reason it was rejected.

The near-miss layer is the part that actually tests fragility. A detector that
says nothing is only trustworthy if you can see what it threw away.

### What the audit showed

On 5 symbols × 6 sessions (30 sessions, 2026-03-30 → 2026-08-28):

| | count |
| --- | ---: |
| labelled formations | 488 |
| near misses (geometry matched, no label) | 507 |

Near misses by reason:

| reason | n | share |
| --- | ---: | ---: |
| prior trend was sideways, rule needs up/down | 360 | 71% |
| prior trend was the opposite of what the rule needs | 113 | 22% |
| failed an adaptive size/ratio threshold | 34 | 7% |

**93% of all rejections are the trend gate, and most of those are "sideways".**
That is the single behavioural change from v1, and it is doing essentially all
of the filtering. Whether that is right is a judgement call, not a bug — but it
is now measurable instead of invisible. Spot checks of labelled formations
(e.g. ACC 2026-03-30 11:50 hammer) match what the eye would call.

---

## 2. BT28 — does a pattern predict anything?

`research/backtests/bt28_pattern_census.py`. Pre-registered before the run, one
design, no variants.

* **Universe** 216 symbols with data, all of 2026 (1 Jan → 18 Jun), 168
  trading days, ~27,000 symbol-days.
* **Detection** prefix scan — a formation only exists at its own closing bar.
* **Entry** the open of the *next* 5m bar, the first price actually obtainable.
* **Exit** the close 1 / 3 / 6 / 12 bars later (5 / 15 / 30 / 60 minutes), plus
  the session close.
* **Sign** `+ret` for bullish patterns, `−ret` for bearish, so a bearish pattern
  scores positive when price falls.
* **Control** every other eligible bar open in the same sessions
  (n = 1.65 million), so ordinary intraday drift is subtracted out.
* **Bar to clear** edge > one real round trip (0.21% MIS, measured in this repo)
  **and** |t| ≥ 3.49 (Bonferroni over the 105 pattern × horizon cells reported)
  **and** n ≥ 50.

### Result

**424,296 detections. 0 of 105 cells cleared the bar.**

Unconditional control drift, for scale: +0.0024% over 30 minutes
(sd 0.51%), +0.0151% to the close (sd 1.21%).

Best well-powered positives:

| pattern | horizon | n | edge vs control | t | vs 0.21% cost |
| --- | --- | ---: | ---: | ---: | --- |
| inverted hammer | +5m | 12,146 | **+0.0067%** | 3.68 | 31× too small |
| inverted hammer | +15m | 11,903 | +0.0083% | 2.67 | 25× too small |
| inverted hammer | +30m | 11,506 | +0.0095% | 2.08 | 22× too small |

Inverted hammer is the one honest positive in the set: at n = 12,146 its 5-minute
edge is statistically real (t = 3.68 clears the corrected threshold) and
economically irrelevant — you need 31 times that to pay for the trade.

The **strongest** statistical findings in the census point the wrong way:

| pattern | horizon | n | edge vs control | t |
| --- | --- | ---: | ---: | ---: |
| shooting star (bearish) | EOD | 8,413 | −0.0708% | −5.20 |
| shooting star (bearish) | +60m | 6,908 | −0.0368% | −3.99 |
| hammer (bullish) | EOD | 12,064 | −0.0404% | −3.69 |
| bullish engulfing | +30m | 7,671 | −0.0197% | −3.40 |
| tweezer bottom | +30m | 5,103 | −0.0218% | −3.17 |

After a shooting star, price rises relative to control; after a hammer, it falls.
These are mild contrarian signals, not tradable in either direction — at
0.03–0.07% they are still 3–7× below the cost floor. Do **not** fade them.

### Two structural findings

**82% of all detections are indecision.** doji 191,432 (45%), spinning tops
156,423 (37%). Only 18% of what the detector emits makes any directional claim
at all. If the UI shows all tags equally, the signal is buried by design.

**The multi-candle patterns essentially never fire intraday.** Across ~27,000
symbol-days:

| pattern | n | frequency |
| --- | ---: | --- |
| three black crows | 89 | 1 per 304 symbol-days |
| three white soldiers | 72 | 1 per 376 symbol-days |
| morning doji star | 32 | 1 per 845 symbol-days |
| morning star | 30 | 1 per 901 symbol-days |
| evening doji star | 24 | 1 per 1,127 symbol-days |
| evening star | 11 | 1 per 2,458 symbol-days |
| rising three | 5 | 1 per 5,409 symbol-days |
| falling three | 2 | 1 per 13,522 symbol-days |

This is the mandatory real-body gap (spec disagreement #1) meeting the fact that
intraday 5-minute bars rarely gap. The rules are not wrong; they were written for
daily candles. If stars are wanted intraday at all they need a separately named
relaxed variant (`morning_star_nogap`) — but BT28 gives no profit reason to build
one, only a display-parity reason.

---

### 2.1 Replication on 2023 (independent window)

254 symbols, 696,940 detections, control n = 2.94 million. Run only to check the
null reproduced — not to hunt for a survivor.

**0 of 100 cells cleared the bar again.** Largest |edge| anywhere with n ≥ 1,000:
0.089%, still under the 0.21% cost floor.

But the replication says something the 2026 run alone could not. Of 65
well-powered cells present in both years, **82% agree on sign**, and the
individual patterns are strikingly stable:

| pattern | 2026 edge / t (+30m) | 2023 edge / t (+30m) | stable? |
| --- | ---: | ---: | --- |
| inverted hammer | +0.0095% / 2.08 | +0.0190% / 5.59 | yes, correct-signed |
| hammer | −0.0134% / −2.84 | −0.0192% / −5.19 | yes, **inverted** |
| bullish engulfing | −0.0197% / −3.40 | −0.0131% / −3.19 | yes, **inverted** |
| shooting star | −0.0262% / −4.10 | −0.0241% / −4.75 | yes, **inverted** |
| gravestone doji | −0.0187% / −2.11 | −0.0141% / −2.29 | yes, **inverted** |
| hanging man | −0.0082% / −1.59 | +0.0142% / +3.69 | no, flips |
| tweezer bottom | −0.0218% / −3.17 | −0.0023% / −0.50 | no, 2023 is flat |

2023 has larger samples and larger t-statistics (hammer at +5m: t = −9.76;
inverted hammer at +5m: t = +14.31). A detector emitting arbitrary labels would
not produce 82% cross-year sign agreement at those t-values. **The labels are
measuring something real.** That is a genuine positive result for the detector —
it is just a real effect worth ~0.02%, against a 0.21% toll.

Read plainly: on NSE 5-minute bars, **inverted hammer is the only classic
reversal pattern whose textbook direction holds**, and hammer, bullish engulfing,
shooting star and gravestone doji are consistently *backwards*.

**Important confound, stated rather than resolved:** the control is every
eligible bar, not bars matched on prior trend. Hammers only form after a decline
by construction, so "price falls after a hammer" may be short-horizon momentum
continuation rather than a property of the shape. Separating the two needs a
trend-stratified control. It was not run, because every candidate is below the
cost floor under either reading, so the answer cannot change a decision.

---

### 2.2 Re-run with the v3 detector (same 2026 window)

219 symbols, **404,531 detections** (v2: 424,296). **0 of 105 cells cleared the
bar again.** What changed is *which* candles carry the hammer-family names:

| pattern | n v2 → v3 | EOD edge v2 → v3 | t v2 → v3 |
| --- | ---: | ---: | ---: |
| hammer | 12,064 → 4,517 | −0.040% → −0.015% | −3.69 → −0.78 |
| hanging man | 10,717 → 3,645 | −0.017% → **+0.025%** | −1.51 → +1.40 |
| inverted hammer | 12,146 → 8,339 | +0.012% → +0.012% | 1.12 → 0.98 |
| shooting star | 8,413 → 4,686 | −0.071% → −0.070% | −5.20 → −3.76 |
| rising / falling three | 5 / 2 → 14 / 11 | (n too small) | |
| soldiers / crows | 72 / 89 → 72 / 87 | ≈ unchanged | |

The position rule (body at the prior candle's extreme) removed about two thirds
of hammer/hanging-man labels, and the ones it removed were the *most*
wrong-signed: hammer's inverted EOD effect shrinks from a corrected-significant
−0.040% to noise, and hanging man flips to the textbook sign. That is evidence
the v3 labels are closer to what the names mean. Shooting star remains
consistently inverted. Body-containment lets a few more Three Methods form
(still ~1 per 2,000 symbol-days). None of this moves anything within a factor
of five of the 0.21% cost floor; the trading conclusion is unchanged.

### Multiple-testing status

2026 and 2023 are now both used for this census — 205 cells reported and
corrected. Nothing survived in either. Any future candlestick hypothesis needs a
window not used here.

---

## 3. BT29 — does a pattern improve the momentum system's *own* trades?

BT28 asked whether a pattern predicts anything on its own. It does not. But the
user does not trade patterns on their own: entries come from the momentum
criteria (gainer, RVOL, setup, trigger), and a candlestick could still earn its
place as an **extra filter inside that already-selected pool**. A signal can be
worthless unconditionally and still be informative conditionally, so this is a
separate, legitimate question — pre-registered in
`research/hypotheses/2026-09-12-pattern-confirmation-on-past-trades.md`.

**Population.** (Superseded — see §3.1 for the corrected, cleaner population;
the verdict is unchanged.) Every trade this repo has ever simulated with the
momentum engine: the union of `research/backtests/bt17_trades*.csv`, de-duplicated on
(date, symbol, setup, entry_time, entry) — **11,868 trades, 315 symbols,
2022–2026**, of which 11,813 had cached bars to re-scan. All are long.

**Exposure.** For each trade the v3 detector was re-run on that session's 5m
bars with `closed_through` set to the fill timestamp and to the three 5m bars
before it, so a formation only counts if it had fully closed before the fill.

### What v3 actually saw at the fill

| what v3 saw | trades | share | gross | win rate | ₹ / trade at real costs |
| --- | ---: | ---: | ---: | ---: | ---: |
| bullish formation | 379 | 3.2% | −0.073% | 23.7% | −₹131 |
| bearish formation | 574 | 4.9% | +0.023% | 31.5% | −₹90 |
| indecision only (doji / spinning top) | 4,091 | 34.6% | −0.040% | 26.3% | −₹113 |
| nothing | 6,768 | 57.3% | +0.002% | 30.2% | −₹96 |

The first number worth noticing is **3.2%**. v3 almost never calls a bullish
pattern at a momentum entry, and that is the context gate working exactly as
designed: a bullish reversal requires a prior *downtrend*, and these entries are
by construction buys into strength. The same candle in an uptrend is a hanging
man, not a hammer. The pre-v3 detector tagged a bullish pattern on **55%** of the
same fills — it was labelling shapes without asking where they sat.

### Result

| split | n with | gross with | gross without | spread | t |
| --- | ---: | ---: | ---: | ---: | ---: |
| v3 bullish at entry | 380 | −0.074% | −0.012% | **−0.062 pp** | −1.45 |
| any v3 pattern at entry | 5,045 | −0.035% | +0.002% | −0.037 pp | −1.97 |
| v3 bearish only (anti-test) | 574 | +0.023% | −0.016% | +0.039 pp | +0.84 |
| pre-v3 logged tags | 7,583 | −0.012% | −0.018% | +0.006 pp | +0.30 |

Per window, on the canonical `fixed_2r` baseline runs (one exit rule, so the
outcome is clean):

| window | trades | confirmed | gross confirmed | gross rest | spread | t |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2022-23 | 2,397 | 59 | −0.224% | +0.032% | −0.255 pp | −2.50 |
| 2024 | 985 | 23 | −0.183% | −0.075% | −0.108 pp | −0.70 |
| 2025 | 459 | 10 | +0.121% | −0.047% | +0.168 pp | +1.16 |
| 2026 (out of sample) | 684 | 20 | −0.061% | +0.025% | −0.086 pp | −0.35 |

Per pattern, 13 cells reported, Bonferroni |t| threshold 2.89: **nothing
survives**. The largest reading is inverted hammer at −0.149 pp (t = −2.36) —
below the corrected threshold, and wrong-signed for the hypothesis anyway.

**Decision-rule scorecard** (all five were required):

| criterion | required | actual | |
| --- | --- | --- | --- |
| gross spread | ≥ +0.30 pp | −0.062 pp | ✗ |
| t-stat | ≥ 2.0 | −1.45 | ✗ |
| anti-test | bearish not better | bearish **is** better (+0.039 pp) | ✗ |
| window stability | 3 of 4 positive | 1 of 4 positive | ✗ |
| cost stress | confirmed subset net-positive at 0.21% | −0.28% / trade | ✗ |

Nought for five. **The v3 pattern is not being added to the entry rules.**

### Why it fails, mechanically

This is not "the filter is noise". It leans slightly the *wrong* way, and there
is a reason. v3 will only call a bullish reversal after a measured downtrend, so
"bullish pattern at a momentum entry" translates to "the stock had been falling
into the moment you bought it". That selects the entries where the momentum had
already broken, which is why the confirmed subset is worse in three windows out
of four, significantly so in the largest one. The old detector did not have this
problem only because it did not look at context at all — and it sorted nothing
either (+0.004 pp, t = +0.18).

### Money, since that was the question

At the real MIS round trip of 0.21%, the 11,813 trades are **−₹1,214,089** in
total, about −₹103 per trade. Taking only the pattern-confirmed trades would
have made −₹49,920 over 380 trades, about **−₹131 per trade** — worse per trade
than the pool, not better. There is no subset of this filter that turns the
strategy's P&L positive.

(De-duplication prefers a trade's *clean-run* copy where one exists, so these
pooled figures moved slightly after §3.1 was written. §3.1 is the population of
record; nothing in either verdict changes.)

### 3.1 Correction — the first population was too loose

Reviewing the charts, the operator objected that the sampled trades were 3rd,
4th and 5th re-entries on the same stock, which their own rules forbid. That was
correct and it exposed two defects in the population above:

1. **49% of the 11,868 trades come from `bt17_trades_me_multi.csv`** — the
   multi-entry arm that this repo already killed (no cap on trades per day; it
   lost 2.5× as much as the one-trade-per-day baseline). Those re-entries exist
   in no other run, so pooling every variant file quietly made the killed arm
   half the sample.
2. **Several older files carry the breakeven-lock defect** fixed on 2026-09-06
   (the lock leaking into `fixed_2r`). Their `trail_stop` exits are 17–28% of
   trades and ~19–25% are exact-breakeven scratches. Sampling a scratch of 1,108
   in the multi-entry file and replaying it under the spec'd rules: **40% would
   have reached the 2R target** (mean +1.18% gross), 49% would have hit the
   original stop, 11% would have ridden to the close. The defect did not merely
   add scratches, it converted real winners into flat exits.

**Clean population.** One trade per symbol-day, and only runs written after the
fix: `bt17_trades.csv`, `_vs_a`, `_vs_b`, `_qs_base_fixed`, `_qs_sel_fixed`,
`_loc26`, `_smoke23`, `_mm25_strict`. De-duplicated: **7,447 trades, 311
symbols, 2022–2026**, mean gross **+0.001%** — the familiar no-drift reading —
and −₹95/trade after the real 0.21% round trip.

Re-running the same pre-registered test there makes the answer *stronger*, not
weaker:

| split | n with | gross with | gross without | spread | t |
| --- | ---: | ---: | ---: | ---: | ---: |
| v3 bullish at entry | 165 | −0.110% | +0.005% | **−0.115 pp** | −1.57 |
| any v3 pattern | 2,700 | −0.017% | +0.013% | −0.030 pp | −1.12 |
| v3 bearish only (anti) | 347 | +0.040% | +0.001% | +0.040 pp | +0.63 |

| window | trades | confirmed | spread | t |
| --- | ---: | ---: | ---: | ---: |
| 2022 | 2,251 | 45 | −0.150 pp | −0.87 |
| 2023 | 2,649 | 59 | −0.090 pp | −0.88 |
| 2024 | 986 | 23 | −0.095 pp | −0.55 |
| 2025 | 877 | 18 | −0.179 pp | −0.91 |
| 2026 | 684 | 20 | −0.086 pp | −0.35 |

**Negative in all five years.** The verdict does not change; the cleaner
population just removes the doubt about which trades it was measured on.

### 3.2 Two related things the operator asked about

**Pullback ordinal.** Their rule is to skip the 3rd pullback onward. Computed
with `pullback.pullback_ordinal` on the non-multi-entry trades: ordinal 1-2 is
+0.013% gross, ordinal 3+ is +0.035%, spread −0.022 pp (t = −0.63) — i.e. the
pullbacks they avoid were marginally *better* per trade. This independently
reproduces the earlier pullback-ordinal kill (2026-09-06), including the odd
detail that ordinal 5 is the best bucket (+0.167%, n=208). The rule is still
worth keeping, but for a different reason than selectivity: gross is ~0, so
every avoided trade is a saved 0.21% round trip. Fewer trades is the P&L lever;
which pullback it is, is not.

**Formation size.** The single-candle rules (`is_doji`, `is_hammer`,
`is_spinning_top`) are pure ratio tests on the candle's *own* range, so a candle
spanning ₹0.70 on a ₹615 stock satisfies them exactly as well as one spanning
₹7. Measured over the 5,045 trades that had a formation before the fill:

| formation range | share |
| --- | ---: |
| < 0.5× the 10-bar average range | 23.9% |
| < 1.0× the 10-bar average range | 74.7% |
| spans < 0.21% of price (one round trip) | 12.9% |

Median formation is 0.72× the recent average range and 0.42% of price; doji and
spinning tops are the small ones (median 0.65–0.73×), while engulfings, tweezers
and shooting stars are ≥ 1.0×. The labels are correct — TA-Lib has no size floor
either — but "correct" and "significant" are different questions, and nothing in
the published definitions asks the second one. Splitting the clean pool at 1×
gives +0.026 pp (t = +0.50) in favour of the larger formations: real in
direction, far too small to trade. **Built 13 Sep 2026: `PatternMatch.strength`**
(range of the confirming candle ÷ the ten-candle average range) with
`STRENGTH_WEAK_BELOW = 0.75`. It gates nothing — detection is byte-identical and
`PATTERN_RULES_VERSION` is unchanged — but the momentum trade chart and both
audit reports now draw sub-threshold formations faint and print the multiple in
the label. About 51% of the formations on the BT29 report are flagged. This
improves the annotation. It is not an edge and is not sold as one. See the
"Strength" section of `candlestick-recognition-v2.md`.

**Regenerating all of this:** `research/backtests/README-bt29.md` — two
commands, and the clean-population filter is applied in code (and printed) so it
cannot drift from what is written here.

**Visual report:** `research/backtests/bt29_trade_report.html` — 238 charted
trades from the clean population, each card showing the pullback ordinal and the
nearest formation's size, with BUY/STOP/EXIT levels, the holding period shaded,
every formation marked in the chart, and a clickable trade table filterable by
ordinal and formation size.

---

## 4. Verdict

* The v3 detector is **correct and no longer fragile**: labels match the written
  rules, the rules match published definitions, rejections are explainable, 323
  unit tests pass, and near misses are now visible rather than silent. The
  cross-year replication is independent evidence of this: 82% of well-powered
  cells keep their sign between 2023 and 2026, at t-values up to 14. The labels
  track something real.
* Candlestick patterns on NSE 5-minute bars **carry no tradable directional
  information** — all 205 measured cells across two independent years are at
  least an order of magnitude below the cost of the trade, and the strongest,
  most reliably replicating effects are wrong-signed relative to the textbook.
* This is the same shape of answer as BT9, BT14 and BT17 in this repo: the signal
  has no drift, and costs are the entire loss. Improving *detection* of a signal
  with no drift changes nothing about P&L.
* Patterns also **fail as a filter on the system's own trades** (BT29). Across
  11,813 past momentum trades, a bullish v3 formation at the fill was worth
  −0.038 pp of gross return, 0 of 5 pre-registered criteria met, and negative in
  three of four windows. The conditional question is now answered as well as the
  unconditional one.
* v3 is nonetheless a real improvement in *labelling*: it calls a bullish pattern
  at 3.2% of momentum entries where the old detector called one at 55%. The 52
  points of difference were shapes in the wrong context. Better labels, same
  (zero) edge — which is the expected outcome when the underlying signal has no
  drift.

**Recommendation:** keep the v3 detector as chart annotation and as context
evidence on trades. Do not promote any candlestick pattern to an entry trigger
or to an entry filter, and do not spend further effort tuning thresholds for
profitability — the census says the ceiling is ~0.01%, not the thresholds.
Per the BT29 pre-registration there is no variant #2 of the pattern-as-filter
idea on this data.

---

## 5. Would an AI model detect patterns better?

For recognising named patterns: **no, and it would make things worse.**

* A hammer *is* its definition — body ≤ 30% of range, lower shadow ≥ 2 bodies,
  upper shadow ≤ 10% of range. There is no hidden function to learn. The rule
  engine is exact, deterministic, microseconds per bar, free, unit-testable, and
  can explain every rejection. A model would be slower, cost money per bar,
  give different answers on identical inputs, and could not tell you *why* it
  said hammer.
* The fragility was never in shape recognition. It was in **context** (what
  counts as a prior downtrend) and **data hygiene** (5m buckets missing minutes).
  An AI model inherits both problems and hides them.
* Most importantly: BT28 says the labels do not predict returns. A more accurate
  detector of a signal with no edge is a more accurate detector of noise.

Where ML would be legitimate is a *different* project: learning a return-predictive
function directly from bar features, with purged cross-validation, deflated Sharpe,
and an anti-strategy check — not "detect pattern, then trade pattern". Given this
repo's record on entry-side mining, that is a low-prior, high-effort path.

The one genuinely good use of an LLM here is offline and outside the hot path:
generating adversarial test fixtures, and cross-checking rule text against
vendor documentation — which is what produced the v2 spec.
