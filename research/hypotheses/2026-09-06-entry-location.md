# Entry location: round numbers and support/resistance

**Registered:** 2026-09-06, BEFORE any 2026 number was computed.
**Status:** 🔴 KILLED 2026-09-06 — G1 failed, G2 not testable. Single-shot run.
**Backtest:** `research/backtests/bt24_entry_location.py`
**Operator request (verbatim):**

> 1. Find the high moving stocks
> 2. check the chart quality by looking at the indicators, if chart and the
>    signals are aligned then only consider the stock
> 3. search duckduckgo for news catalyst if yes then consider for next phase
> 4. follow this before taking trades:
>    1. check if the current price is near a whole rupee or half rupee, because i
>       have seen i high sell on these levels.
>    2. check support and resistance.
>       1. if price is near resistance don't take the trade
>       2. if price broke the resistance then wait for first candle to close, if
>          it closes above resistance then take the trade.
>       3. if price is near the support then take the trade.
> 5. take the trade 10sec early then 1 min chart.
>
> all the above should be followed if indicators are in our favor.

---

## 0. Triage of the seven components

Only the new ones are tested. Re-testing a killed component on the same data
would be a process violation, and re-testing it on new data spends a window on a
question already answered.

| # | Component | Disposition |
|---|-----------|-------------|
| 1 | high-moving stocks | **already the pool** — day-change 4–8%, RVOL ≥ 3×. Unchanged; it is arm A. |
| 2 | chart quality + indicators aligned | **already KILLED.** `quality-selectivity` bundled exactly this (F1 EMA/VWAP/daily-trend + F2 chart shape): 2,408 → 250 trades, anti **p = 0.979**. MACD alone: lift **+0.0084 pp**, p = 0.135. Not re-tested as a standalone claim. The MACD leg is carried inside the stack below because the operator conditions everything on it. |
| 3 | DuckDuckGo news catalyst | **cannot be tested retroactively — see §1.** |
| 4.1 | near whole/half rupee → sellers | 🟢 **NEW. Tested.** |
| 4.2.1 | near resistance → skip | 🟢 **NEW. Tested.** |
| 4.2.2 | broke resistance → wait for the candle to close above | ⚙️ **ALREADY IMPLEMENTED.** `setups.flat_top_breakout` refuses unless `close > level` ("must CLOSE through, a wick is not a break"), and the fill is the *next* bar's open, so the position is only taken after a confirmed close above the ceiling. Nothing to test; verified in code, pinned by a test. |
| 4.2.3 | near support → take | 🟢 **NEW. Tested.** |
| 5 | enter 10 s early | **already KILLED.** `entry-fill-latency` measured the *zero-slippage ceiling* of exactly this at **+0.2352 pp**, 95% CI [+0.191, +0.280], against a locked +0.30 pp bar. The effect is real (t = 10.40) and still failed. Not re-tested. |

So this hypothesis tests **4.1, 4.2.1 and 4.2.3**, conditioned on the operator's
"indicators in our favor" (MACD > 0).

---

## 1. Why the DuckDuckGo step is not in this test

It is not a tooling problem, it is a look-ahead problem.

A search run **today** returns results ranked by what the web later decided was
important. A stock that ran 30% next week accumulates coverage, links and recency
signals that a stock which quietly faded never gets. Querying that index and
pretending it is what a scanner could have seen at 10:15 IST on some day in March
2026 would leak the outcome into the feature. A backtest built on it would look
good and would not survive contact with live trading — the same failure mode as
the news-trader's delayed-quote artifacts, which produced positive readings that
vanished on real bars.

Two secondary problems: there is no reliable intraday timestamp on search results
(so "was this known before the trigger?" is unanswerable), and the run would need
roughly 225 symbols × 170 sessions ≈ **38,000 queries** against a third-party
service that does not permit that volume.

**The catalyst gate is still the last untested component, and it has exactly two
honest paths:**

1. **A dated events file** — `bt17 --events symbol,date[,event_type]`, already
   wired and used by the PEAD work. Timestamps are known-at-the-time. Needs
   real results dates, e.g. a StratQ export.
2. **Forward capture** — the live scanner queries news in real time and stamps
   what it saw, then the gate is measured on trades it actually took.

Both are real options. Neither is a retroactive web search.

---

## 2. Definitions (frozen before the run)

New module `src/momentum_trader/location.py`. All three metrics read only closed
bars up to and including the trigger bar.

* **`dist_to_round_pct`** — percent from the trigger to the **nearest** ₹0.50
  mark, in either direction. **This is the gated metric.** Ranges from 0.0 (the
  trigger sits exactly on a mark) to half an interval (the midpoint).
* **`round_head_pct`** — percent from the trigger *up* to the next mark.
  Directional; kept as a second reading and reported exploratorily only.

The ₹0.50 grid is the operator's stated spec ("whole rupee or half rupee"), not a
fitted value; it is never searched over.

* **`resist_head_pct`** — percent up to the nearest resistance from
  `levels.derive_levels` (swing-pivot clusters + prior-day H/L/C + the round
  grid). Same levels the exit rules already use; no new definition.
* **`support_drop_pct`** — percent down to the nearest support, same source.

> **Pre-run amendment, 2026-09-06.** This section originally gated on
> `round_head_pct` with an "upper half of the interval" split. That was a
> misreading of the claim: a stock trading at exactly ₹250.00 is *inside* the
> round-number congestion the operator described, yet the directional metric
> scores it 0.2% *below* the next mark and would have waved it through. The gated
> metric is therefore distance to the nearest mark. **Amended before the engine
> run and before any 2026 number was computed** — the fetch was still in
> progress and no result had been seen. Recorded here rather than silently
> corrected.

### The gate rules, expressed without tunable thresholds

* **near a round number** — `dist_to_round_pct` is **below its median across the
  run's own trades**, i.e. the trigger is in the half of trades sitting closest
  to a ₹0.50 mark. A median split has no threshold to fish over; a "within X
  paisa" band would.
* **near resistance / near support** — within `levels.NEAR_PCT` (**0.35%**),
  the band already frozen in the codebase and used by
  `exits.RESIST_NEAR_PCT`. Not a new number.

**No new tunable parameters are introduced.** `ROUND_STEP = 0.50` is a spec, and
the two proximity tests reuse frozen constants.

---

## 3. Arms

**One engine run** on the fresh window; every arm is a derived **exact subset**,
so the anti-test is valid and no second data read is needed.

| Arm | Rule |
|-----|------|
| **A** | base — the pool exactly as it runs today (reference) |
| **V** | **veto stack**: MACD > 0 **and** not near resistance **and** not near a ₹0.50 mark |
| **V+** | V **and** near support (the operator's 4.2.3 as an inclusion) |

Split this way because the operator's rules are of two kinds: 4.1 and 4.2.1 are
*exclusions* ("don't take the trade"), while 4.2.3 is an *inclusion* ("then take
the trade"). Bundling an inclusion into a veto stack would misstate the request.

Exploratory singles (**not gated**): round-number veto alone, resistance veto
alone, support proximity alone, MACD alone; plus the full gross gradient by
decile of each metric.

### Window — and a correction to the record

**2026-01-01 → 2026-09-04. This window has never been read by anything** (no
cache existed for it before this test), so it is a genuinely fresh out-of-sample
period, ~8 months.

> **Correction.** My working note said "2025 is the sealed hold-out." That was
> wrong, and I am recording it rather than quietly relying on it. BT17's original
> run on 2026-09-05 covered **2024-01-01 → 2025-12-31** and reported per-year
> results (2025: −0.96% stressed net). 2025 was therefore already read at the
> pool level, though no filter or variation has ever been *selected* on it.
> 2026 is the only untouched window, which is why this test uses it.

**Universe — deviation from plan, disclosed.** This was intended to reuse the
225 symbols pinned in BT23. It did not: the scratchpad file holding that list was
deleted by the environment mid-session, so `--symbols` received an empty string
and `bt17` fell back to its default, the **full NIFTY 500 (504 instruments)**
with the standard daily pre-filter (price ₹60–2,000, 20-day turnover ₹3–50 cr,
reached the day-change floor at some point in the window). Roughly 220 names pass
it, versus BT23's 224.

I am **keeping this run rather than redoing it**, because the accident produced
the better universe: the pinned 225 carried a "reached ≥4% at some point during
2022–2023" selection, and the NIFTY 500 default does not. Consequences, stated
plainly:

* **G1 and G2 are unaffected.** Every arm is a derived subset of this single run,
  so the gates compare like with like.
* **Cross-window comparison with BT23 is weakened** — the 2026 base rate sits on
  a slightly different universe, so it is not a clean like-for-like against the
  2022–23 base rate. Read the 2026 base as its own number.

**After this run, 2026 is a used window.**

---

## 4. LOCKED falsification criteria

`fixed_2r` exits (post-bugfix), `next_open` fill, `one_trade_per_day=True`.

**Viability bar: gross ≥ +0.35%/trade** — unchanged from BT23, derived from the
measured 0.206% real MIS round trip. Frozen; it does not move in this test.

**G1 — VETO STACK (components 4.1 + 4.2.1 + indicator condition)**
* gross%/trade(V) ≥ **+0.35%**
* gross%/trade(V) − gross%/trade(A) ≥ **+0.20 pp**
* anti: random subsets of A of size n(V), 5,000 draws, p < **0.05**
* n(V) ≥ **150**

**G2 — SUPPORT INCLUSION (component 4.2.3)**
* gross%/trade(V+) ≥ **+0.35%**
* gross%/trade(V+) − gross%/trade(A) ≥ **+0.20 pp**
* anti p < **0.05**
* n(V+) ≥ **150**

Alpha **0.10 / 2 = 0.05** (Bonferroni over the two gated tests).

**If an arm has n < 150 it is declared NOT TESTABLE on this window.** It may not
be rescued by widening the window, loosening a proximity band, or dropping one
leg of the stack — that would be selecting the rule by trade count.

### Disclosed limitation

The vetoes are applied as a post-hoc subset, so a refused setup does not free the
day for a later one; real gating would allow that substitution. BT22 showed
substitution contaminating the comparison (30 of 250 filtered trades were absent
from the base set), so the subset form is the cleaner test of whether entry
*location* carries information. Shipping would require re-measuring with the gate
inside the engine.

---

## 5. Pre-committed stop

If G1 and G2 both fail, **no entry-location gate is added** — no round-number
veto, no resistance veto, no support requirement. `EngineConfig` is unchanged and
the recorded fields stay recorded-only. No second look at 2026 for an
entry-location variation, and no "the best decile was …" rescue.

If a gate passes, the next step is **not** to ship: it is to re-measure with the
gate inside the engine (to capture substitution) and then confirm on a window
that this test did not touch.

---

## 6. Results

Single-shot run, 2026-01-01 → 2026-09-04, 9 months, NIFTY 500 (504 instruments,
~220 pass the daily pre-filter), **689 trades**. Full output:
`research/backtests/bt24_analysis.log`.

### ⭐ The base rate replicated out of sample

| Window | n | gross%/trade | Status |
|---|---|---|---|
| 2022–2023 (BT23 arm A) | 2,310 | **+0.020%** | dev, 3 looks |
| **2026 (this run)** | **689** | **+0.020%** | **fresh, never read** |

This is the **first genuine out-of-sample confirmation** in this line of work.
The corrected `fixed_2r` strategy reproduces its dev-window gross to three
decimal places on data nothing was ever tuned on. The pool has no drift, and
that fact is now established rather than assumed. Net at real MIS costs
**−0.186%**, also matching.

Monthly, there is no regime break: 2026-06 was the best month
(**+0.210% gross, +0.004% net at real costs** — the first net-positive month
recorded anywhere in this work), 2026-02 and 2026-08 the worst (−0.139%,
−0.137%). Ordinary variance around zero.

### Arms

| Arm | n | gross%/tr | net @ real | win% |
|---|---|---|---|---|
| **A** base | 689 | **+0.020** | −0.186 | 20.9 |
| **V** veto stack | 229 | **−0.012** | −0.218 | 22.7 |
| **V+** veto + near support | 30 | −0.106 | −0.312 | 20.0 |

### G1 — VETO STACK: **FAIL**
* gross −0.0115% vs +0.35 required; 95% CI [−0.170, +0.147]
* lift vs A **−0.0319 pp** — wrong sign
* anti **p = 0.6848**
* n = 229 ✓

### G2 — SUPPORT INCLUSION: **NOT TESTABLE**
n = 30 < 150. The full stack plus a near-support requirement keeps **4.4%** of
trades — about 3 a month. Per §4 this may not be rescued by loosening the band
or dropping a leg.

### Component-by-component (exploratory, not gates)

| Rule | n | gross | vs A | win% |
|---|---|---|---|---|
| round-number veto (4.1) | 345 | +0.043 | **+0.022 pp** | 21.4 |
| …the vetoed near-round half | 344 | −0.002 | −0.022 pp | 20.3 |
| resistance veto (4.2.1) | 553 | +0.021 | +0.001 pp | 22.6 |
| …the vetoed near-resistance set | 136 | +0.017 | −0.003 pp | **14.0** |
| near support (4.2.3) | 114 | **−0.097** | **−0.118 pp** | 14.9 |
| MACD > 0 | 544 | +0.015 | −0.005 pp | 22.6 |

**4.1 round numbers — correct sign, ~0 magnitude.** The half of trades sitting
closest to a ₹0.50 mark really is the worse half (−0.002% vs +0.043%), and the
closest decile is −0.032%. The operator's observation is directionally real. It
is worth **+0.022 pp**, roughly 9× too small to matter, and it does not survive
being stacked with anything.

**4.2.1 resistance — no effect on gross, but a real effect on win rate.**
Vetoing near-resistance entries moves gross by +0.001 pp, i.e. nothing. But
near-resistance trades win **14.0%** of the time against 22.6% for the rest,
while returning the same average — they win less often and bigger. The closest
resistance decile has the lowest win rate in the whole table (**8.96%**). So the
mechanism the operator described is visible in the hit rate and cancels out in
expectation.

**4.2.3 near support — backwards.** Entries near support are **worse**
(−0.097% vs +0.020%), and the decile gradient is consistent rather than noisy:
the three closest-to-support deciles are all negative (−0.071, −0.174, −0.078)
while entries with 0.7–2.0% of room below are positive (+0.181, +0.185, +0.080).
Reading: in a momentum breakout, sitting on support does not mean "supported",
it means the move has already given most of it back.

By setup: micro_pullback +0.072 (n=360) · bull_flag +0.098 (n=64) ·
ma9_pullback +0.014 (n=120) · flat_top_breakout −0.062 (n=89) ·
vwap_reclaim −0.174 (n=50) · orb15 −0.967 (n=6).

## 7. Verdict

🔴 **KILLED.** G1 failed on all three criteria; G2 was not testable. Per §5 **no
entry-location gate is added** — no round-number veto, no resistance veto, no
support requirement. `EngineConfig` is unchanged; `dist_to_round_pct`,
`round_head_pct`, `resist_head_pct` and `support_drop_pct` stay recorded-only,
pinned by tests.

Three things this settled:

1. **The base rate is real and stable.** +0.020% gross on a window nothing was
   tuned on, matching the dev window exactly. Every "the strategy is
   drift-free, costs are the whole loss" claim in this repo is now
   out-of-sample confirmed rather than inferred.
2. **Entry location does not carry tradeable information.** Two of the three
   rules pointed the right way — a first — and both are ~0.02 pp. One pointed
   backwards.
3. **Being near support is a negative, not a positive**, and the gradient says
   so consistently. Do not re-propose it.

**Window status: 2022–23 (3 looks), 2024 (4 looks) and 2026 (1 look) are all
used for entry-side work. 2025 was read at pool level by BT17 on 2026-09-05 but
has never had a variation selected on it.** The remaining honest tests are
forward-only: the catalyst gate via a dated events file or live capture (§1).
