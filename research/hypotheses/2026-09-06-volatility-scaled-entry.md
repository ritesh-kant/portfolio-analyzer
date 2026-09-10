# Volatility-scaled entry floor + indicator confirmation

**Registered:** 2026-09-06, BEFORE any code was written or any number computed.
**Status:** 🔴 KILLED 2026-09-06 — all three gates failed, single-shot run
**Backtest:** `research/backtests/bt23_vol_scaled_entry.py`
**Operator request (verbatim):**

> can you test is high momentum stocks or high volatile stock, for high momentum
> stocks we don't have to wait to 3-4% surge, we can trade as soon as we see good
> signal.
>
> also if indicator is not showing any signal then don't trade.

---

## 0. Multiple-testing disclosure — read this first

This is the **third look at the 2022–2023 window** and the **fifth entry-side
test overall**. The prior four entry-side tests all came back at or below noise:

| # | Test | Window | Verdict |
|---|------|--------|---------|
| 1 | entry-fill-latency | 2024 | KILL (+0.235 pp vs +0.30 locked) |
| 2 | pullback-ordinal | 2024 | KILL (anti p=0.469) |
| 3 | multi-entry-same-stock | 2022–23 | KILL (anti p=0.990) |
| 4 | quality-selectivity (F1/F2/F3) | 2022–23 | KILL (anti p=0.979) |
| 5 | **this one** | 2022–23 | — |

A prior this poor means the burden here is high, and the alpha is
Bonferroni-adjusted accordingly (§4). **2024 is spent. 2025 is sealed and is not
read by this test.**

### Why this is not simply more mining

The prior four tests all *sub-selected within* the existing 4–8% pool. This test
questions the **pool boundary itself** — a structural parameter that has never
been tested since it was set from Warrior Trading's US rules. There is also a
pre-existing directional hint from the 2026-09-06 loss decomposition: gross by
day-change bucket was monotone, and **4–5% was the best bucket (+0.003%) while
7–8% was the worst (−0.211%)**. Extrapolating that gradient downward is the
motivation for testing below 4%.

That hint came from **already-spent data**, so this test is partly confirmatory,
not independent. It is recorded here so the result is not read as a clean
out-of-sample discovery.

---

## 1. The two claims, stated so they can fail

**Claim 1 (operator).** The 4% day-change floor is too high for stocks that
habitually move. For a high-volatility name, a 2% move already carries the same
information a 4% move carries in a sleepy name, so waiting to 4% only means
entering later and worse.

**Claim 2 (statistical prior, the direct inverse).** Signal-to-noise runs the
other way. A stock with a 6% daily ATR moving 2% has done nothing; a stock with a
1.5% ATR moving 2% has done something. The floor should therefore *rise* with
volatility, not fall.

These two cannot both be right. Testing only Claim 1 would be an assumption, so
both directions are measured, with only Claim 1 gated (§4) because it is the one
the operator asked to act on.

**Claim 3 (operator).** Entries with no indicator confirmation should not be
taken at all.

---

## 2. Definitions (frozen)

* **`atr_pct`** — 20-day ATR(14) on the **daily** frame, as a percent of the
  prior close, computed from **sessions strictly before** the traded day. Reuses
  `indicators.atr`; no new constant.
* **high-vol / low-vol** — split at the **median `atr_pct` of the arm-B trade
  set**. A median split has no tunable threshold to fish over.
* **`macd_hist`** — MACD(12/26/9) histogram on the position's 5-minute frame at
  the trigger bar, warmed up from prior sessions exactly as the exit rules do.
  Reuses `exits.MACD_*`; no new constant.
* **"indicator shows a signal"** — `macd_hist > 0` at the trigger bar: the fast
  average is pulling ahead of the slow one, i.e. momentum is confirmed rather
  than merely present in the price change. This is the *entry-side* use of the
  same reading already frozen for exits.

**No new tunable numbers are introduced by this hypothesis.** The only new
free parameter is arm B's floor, fixed at **2.0%** before running because it is
`setups.POLE_MIN_PCT`, the move size the setups already treat as meaningful.

---

## 3. Arms

Two engine runs on the identical universe and window; three derived subsets.

| Arm | What | How |
|-----|------|-----|
| **A** | current rule, floor 4% | engine run |
| **B** | floor lowered to 2% | engine run |
| **C** | **Claim 1** — high-vol names enter from 2%, low-vol still need 4% | subset of B |
| **C′** | **Claim 2** — require `day_chg_pct ≥ 1.0 × atr_pct` | subset of B |
| **D** | **Claim 3** — require `macd_hist > 0` | subset of B |

`atr_pct` and `macd_hist` are **recorded only**; the engine gates on neither.
This is deliberate: it keeps B's trade set fixed so C, C′ and D are *exact
subsets* of B, which is what makes the anti-test valid.

**Disclosed consequence.** Because the filters are applied after the fact rather
than inside the engine, a refused setup does not free the day for a later one.
Real gating would allow that substitution. BT22 showed substitution actively
contaminated the comparison (30 of 250 filtered trades were not in the base
set), so the subset form is the cleaner measurement of *whether the indicator
carries information*. It is not the same thing as the live rule, and shipping
would require re-measuring with the gate inside the engine.

**A is not a superset of B.** With a 2% floor the first qualifying setup of a day
can fire earlier, and `one_trade_per_day` then blocks the 4%+ setup A would have
taken. A and B are therefore compared as independent arms, never as subsets.

### Universe restriction (disclosed)

225 symbols with 1-minute cache for **both** 2022 and 2023. These were cached
because they reached ≥4% on at least one day in the window under the price and
turnover bands — a **symbol-level** filter over two years, not a day-level one,
so nearly every liquid mover survives it. It is applied **identically to every
arm**. Widening the universe would require a multi-hour refetch and would not
change the A-vs-B comparison, which is what the claims turn on.

---

## 4. LOCKED falsification criteria

All arms carry the corrected exits (breakeven-lock bug fixed 2026-09-06),
`fixed_2r`, `next_open` fill, `one_trade_per_day=True`.

### Viability bar: gross ≥ +0.35%/trade

Real intraday MIS costs were measured at **0.206%** round trip on 2026-09-06,
independently of any arm's returns. **+0.35% gross ⇒ ≈ +0.14% net**, about ₹70
per ₹50k trade.

> **This is lower than the +0.60% bar used in BT22, and that is a relaxation.**
> It is recorded here as such. The justification is that +0.60% was set when the
> working cost figure was ~1.0% — a slippage *stress*, not a measurement. The
> cost measurement that replaced it was made while decomposing losses, before
> this hypothesis existed and with no knowledge of any arm's returns here. The
> bar is being re-derived from a corrected input, not moved to let a result
> through. It is now frozen and will not move again in this test.

### Gated tests (all must pass, any failure = KILL)

**G1 — BAND.** Does the 2–4% band pay for itself at all?
* gross%/trade of B-trades with `day_chg_pct < 4.0` ≥ **+0.35%**
* 95% CI lower bound on that mean > **0**
* n ≥ **300**

**G2 — RULE (Claim 1, the operator's rule).**
* gross%/trade(C) ≥ **+0.35%**
* gross%/trade(C) − gross%/trade(A) ≥ **+0.20 pp**
* anti: shuffle the high/low-vol labels within B, 5,000 draws;
  p(shuffled lift ≥ observed) < **0.033**
* n(C) ≥ **300**

**G3 — INDICATOR (Claim 3).**
* gross%/trade(D) ≥ **+0.35%**
* gross%/trade(D) − gross%/trade(B) ≥ **+0.20 pp**
* anti: random subsets of B of size n(D), 5,000 draws; p < **0.033**
* n(D) ≥ **300**

Alpha is **0.10 / 3 = 0.033** (Bonferroni over the three gated tests), on top of
the poor prior in §0.

### Exploratory, explicitly NOT gated

* **C′ (Claim 2)** is reported for mechanism only. **A pass on C′ does not ship
  and does not rescue this hypothesis.** It is the inverse of what the operator
  proposed, so a positive reading there would be a new hypothesis needing a fresh
  window — and 2024 is spent, so that window would have to be 2025, which is
  sealed and needs explicit operator consent.
* Gross by `atr_pct` decile and by `day_chg_pct` decile — the literal
  "momentum or volatility?" sort. Descriptive.

---

## 5. Pre-committed stop

If G1, G2 and G3 do not all pass, **the entry floor stays at 4.0% and no
indicator entry gate is added.** No fourth look at 2022–23 for an entry-side
variation, no threshold adjustment, no dropping one gate to save another, no
"the best decile was …" rescue. The pool boundary will have been tested and the
answer recorded.

---

## 6. Results

Single-shot run, 2022-2023, 225 symbols, 24 months. Full output:
`research/backtests/bt23_run.log`.

| Arm | n | gross%/tr | net @ real cost | gross win% | target% |
|---|---|---|---|---|---|
| **A** base (4%) | 2,310 | **+0.020** | −0.186 | 33.7 | 24.6 |
| **B** wide (2%) | 3,966 | **−0.011** | −0.217 | — | 23.9 |
| **C** operator's rule | 2,548 | **−0.018** | −0.224 | — | 25.0 |
| **D** macd_hist > 0 | 3,315 | **−0.002** | −0.208 | — | 23.7 |
| C′ inverse *(exploratory)* | 2,115 | +0.026 | −0.180 | — | 25.3 |
| 2–4% band | 2,472 | **−0.029** | −0.235 | — | 22.0 |

### G1 — BAND: **FAIL**
* gross −0.0289% vs +0.35 required
* 95% CI [−0.0713, +0.0134] — **straddles zero**, so the band is not even
  reliably different from flat, let alone profitable
* n = 2,472 ✓

The 2–4% band does not pay for itself. Widening the floor **lowered** pool gross
from +0.020% to −0.011%.

### G2 — RULE (Claim 1, the operator's): **FAIL on all three**
* gross(C) −0.0176% vs +0.35 required
* lift vs A **−0.0372 pp** — *wrong sign*: the rule is worse than the rule it
  replaces
* anti **p = 0.9334** — a random split of the same size beat the volatility
  split in **93 of every 100 draws**
* n = 2,548 ✓

### G3 — INDICATOR (Claim 3): **FAIL**
* gross(D) −0.0024% vs +0.35 required
* lift vs B **+0.0084 pp** vs +0.20 required — correct sign, ~24× too small
* anti **p = 0.1354** — fails at the adjusted 0.033 and at an unadjusted 0.10
* n = 3,315 ✓

**Note, stated because it is the one thing here that did not point the wrong
way.** The MACD-strength deciles are humped, not flat: the two weakest deciles
are the worst rows in the table (−0.053% and −0.076%, win 9.2% and 13.3%), and
the strongest decile is also poor (−0.097%). "No signal → don't trade" is
therefore *directionally* right — the first entry filter in five tests that is
not worse than random. It is also far too weak to trade on: cutting the bottom
16% of trades moves the pool by less than one hundredth of a percentage point,
and p = 0.135 does not clear any pre-set bar. Recorded as an observation. **Per
§5 it is not shipped and must not be re-mined on this window.**

### Exploratory (NOT a gate, cannot ship)

**C′, the statistical inverse of the operator's claim, also does nothing.**
gross +0.0262% — a lift of **+0.0066 pp over A**, i.e. indistinguishable from
just keeping the 4% floor. So it is not that the operator had the sign backwards;
**neither direction of volatility scaling carries information.**

The literal "momentum or volatility?" sort settles the framing question:

* **Volatility is the wrong axis, and it leans negative.** Highest-ATR decile
  **−0.139%**, second-highest −0.074%, lowest-ATR decile +0.003%. High
  volatility is mildly *harmful*, the opposite of the premise.
* **Day-change has the only real structure, and it peaks where the floor
  already is.** By whole-percent bucket: (2,3] −0.010 · (3,4] −0.061 ·
  **(4,5] +0.110** · (5,6] −0.004 · (6,7] −0.001 · (7,8] −0.211.

## 7. Verdict

🔴 **KILLED.** G1, G2 and G3 all failed. Per §5 the day-change floor **stays at
4.0%** and **no indicator entry gate is added**. `EngineConfig.day_chg_min`
remains 4.0 and is pinned by a test.

Three things are now settled that were not before:

1. **The 4% floor is not arbitrary — it is close to right.** Lowering it to 2%
   makes the pool worse, and the 4–5% bucket (+0.110%) is the only clearly
   positive band in the whole range. The operator's instinct that we were
   waiting too long is not supported: below 4% the moves are noise.
2. **Volatility is not the sorting variable.** It is the wrong axis and, if
   anything, points the wrong way. Both directions of scaling failed.
3. **The indicator carries a trace of information, not a tradeable amount.**
   First filter that beat random at all; nowhere near the bar.

What this does not resolve: the (4,5] bucket at +0.110% gross is still −0.096%
at real costs, so even the best band does not stand alone. Combining it with the
measured resting-order entry gain (+0.235 pp) would put it at roughly +0.35%
gross / +0.14% net — but **that combines two results from spent data and is a
hypothesis, not a finding.** Testing it means the sealed 2025 hold-out, which
needs explicit operator consent.

**Multiple-testing status after this run: 2022–2023 has now had three looks and
2024 four. Both dev windows are exhausted for entry-side work. 2025 remains
sealed and untouched.**
