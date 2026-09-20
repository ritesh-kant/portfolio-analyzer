# Lift the breakeven stop at 1.5R instead of 1.0R

**Registered:** 2026-09-20 · **Status:** 🔴 KILLED 2026-09-20 (and found a look-ahead bug)
**Arm:** `attention_1m_merged` — attention entries + resistance-breakout +
`future_trigger` fills + `trend_resistance_state` exits.
**Operator ask:** "instead of lifting stop at 1R make it to 1.5R, also lift the
stop to normal" — i.e. keep the ordinary breakeven (stop → entry price), only
make the trade earn more before it gets that protection.

## 0. What changes, exactly

One number. `BREAKEVEN_AT_R` 1.0 → 1.5, applied through a new
`EngineConfig.breakeven_at_r` override (`--breakeven-at-r 1.5`). The stop is
still lifted to the **entry price**, not to a cost-covering price — the
cost-aware variant was killed by BT43 and is explicitly excluded here (the two
flags are mutually exclusive in code, because the cost stop arms off its own
literal 1R test).

Everything else is frozen and unchanged:
* `ARM_AT_R` stays **0.5** — trend exits arm when they always did.
* The hard stop, position sizing, entries, fills and all five trend rules are untouched.
* No new tunable beyond the one number under test. 1.5 was named by the
  operator, not selected from a sweep; no other value will be tried on this data.

**Mechanically this is a two-sided trade, not a free improvement:**
* *Gains* — a trade that pokes just past 1R and then breathes is no longer
  scratched at entry. Today `trail_stop` is **21.95% of all exits (689 of
  3,139)** with a **1.31% gross win rate** and **−0.32% mean gross**.
* *Loses* — a trade that peaks between 1.0R and 1.5R and then reverses now has
  **no protection at all** and rides the original hard stop to a full −1R.

The registered question is which of those two is bigger.

## 1. Windows

2023 and 2024, cached 1-minute bars, identical flags in both arms.

⚠️ **Both windows are SPENT** on this strategy. Per
`feedback_dirty_window_can_kill`, this test **can KILL but cannot BLESS**. A
result that passes every criterion below means *"not killed"* — it is NOT a
ship, and would need an unspent/forward window before going anywhere near live.
**2025 stays sealed and is not touched.**

## 2. Pairing

The arms are paired trade-for-trade on `(date, symbol, entry_time)`.

⚠️ Not a pure exit-only change: a `false_break` exit arms the reclaim re-entry
path, so a changed exit can create or destroy a *later* entry that day (the BT42
effect — 52 manufactured entries there). Unpaired trades on each side are
counted and reported, never silently dropped.

Costs: judged at **real** Zerodha MIS round-trip (~0.21%), with bt17's
`STRESS_SLIP` stripped per tranche, the same way BT43 did it.

## 3. Locked criteria — ALL FOUR must pass, else KILL

| # | Criterion | Bar |
| --- | --- | --- |
| **C1** | **Primary — profit.** Paired mean delta in real-cost net ₹/trade (1.5R − 1.0R) | **> 0 and paired-t p < 0.05** |
| **C2** | Consistency | same sign in 2023 and in 2024 **separately** |
| **C3** | Sample | **≥ 1,000** paired trades **and ≥ 150** trades whose exit actually moved |
| **C4** | Capital comparability | deployed-capital ratio test/control **≥ 0.95** |

C4 is the BT44 guard. Sizing is fixed at entry and must not move, so this should
land at ~1.00; a miss means the populations diverged and ₹/trade is no longer
measuring the rule. **Never judge a size-changing arm by ₹/trade.**

**No criterion may be relaxed, re-weighted or reinterpreted after seeing the
numbers.** If C1 passes only on gross, or only in one year, or only on a subset,
that is a KILL.

## 4. Prior, written down in advance: **POOR**

1. Three prior exit studies on this signal moved the mean by ≈ ₹0:
   fixed-target vs trend exits (+0.004 / +0.017 pp), the two-close false break
   (+₹3.45/trade, p=0.211), and the cost-aware stop (−₹0.09/trade, p=0.893).
2. The entries have **no measured drift**. On a zero-drift signal an exit change
   shuffles money between winners and losers rather than creating any. The
   standing lesson from BT42/BT43 is that a rule which improves *accuracy* is
   P&L-neutral until a paired test says otherwise.
3. The chart that prompted this (WELCORP 2023-03-09) would **not** have changed:
   its base-arm exit was `macd_fade` at 11:21, not the breakeven stop, and it
   reached 1.73R so it arms under either threshold. This test is aimed at the
   general population, not at that trade.

## 5. Descriptive observations to record (NOT criteria, cannot rescue a kill)

* Exit-reason mix, especially the `trail_stop` and `stop` counts.
* Trades that peak in [1.0R, 1.5R) — the population that loses its protection —
  and what happens to them.
* Mean winner, mean loser, gross win rate.
* Unpaired entry counts on both sides.

## 6. Result

### 6a. First run — all four criteria PASSED, and the pass was an artifact

`be{23,24}_{ctl,r15}`. The control reproduced the pre-existing `cs*_base` CSVs
byte-for-byte, so the new override is confirmed neutral at its default.

| | value |
| --- | --- |
| pooled paired REAL net delta | **+₹9.38/trade**, 95% CI [+6.58, +12.19] |
| paired t / p | **t=+6.55, p<0.0001** |
| per year | 2023 +9.98 · 2024 +8.48 |
| per quarter | **8/8 positive** |
| Wilcoxon / sign test | p<0.0001 / p=1.3e-08 |
| survives dropping top 20 by abs delta | yes (+4.19) |
| capital ratio | **1.0000** |
| gross win rate | 35.48% → 38.71% (2023) |

C1–C4 all PASS. **Overridden to KILL** on the mechanism check below.

### 6b. Why the pass was false — a same-bar look-ahead in the breakeven lift

`update_high()` raises the stop off a bar's **high**; `check_stop()` then fills
it off that **same bar's low**. A minute that runs up to 1R and back through the
entry therefore books an exit at the entry price — from a stop-modification
order nobody could have placed inside the candle.

Of the 144 trades whose exit differed, **107 (74.3%)** had the control exit on
the very minute it first reached 1R, with that minute's low already ≤ entry.
**All 107** were unplaceable.

| population | pooled delta |
| --- | --- |
| everything | **+₹9.38/trade** |
| the 107 same-bar artifact trades alone | **+₹30,413 total** (> the whole +₹29,445) |
| **excluding them** | **−₹0.31/trade** |
| the 37 trades where the rule operated legitimately | **−₹968 total** |

The effect does not merely weaken — it inverts. Raising the threshold to 1.5R
mostly made an impossible order fire less often.

Population-wide: the artifact hits **145 of 380 control breakeven scratches =
4.6% of all 3,139 trades**.

### 6c. The bug fixed, then the question re-asked (the registered criteria, unchanged)

`defer_breakeven_one_bar` binds the lift on the bar AFTER the one that earned
it — the ordering the cost-aware stop always used. Arms `bf{23,24}_{ctl,r15}`.

**Bug impact alone (1.0R same-bar → 1.0R deferred):**
**+₹13.34/trade, t=+7.91, p<0.0001**, 2023 +15.53 · 2024 +10.03, 126 exits
changed, capital ratio 1.0000. Gross/trade **−0.0414% → −0.0139%**.
`trail_stop` exits 689 → 572.

**1.5R vs 1.0R with the bug removed:**

| | 2023 | 2024 | pooled |
| --- | --- | --- | --- |
| REAL net delta ₹/trade | +0.17 | +0.09 | **+0.14** |
| p | 0.679 | 0.801 | **0.6275** |
| exits changed | 39 | 26 | **65** |
| better / worse | 13 / 20 | 10 / 14 | **23 / 34** |

95% CI **[−0.43, +0.71]**. C1 **FAIL**. C3 **VOID** (65 changed vs a 150 floor).
More changed trades got *worse* than better in both years.

## 7. Verdict

🔴 **KILLED.** Formally the corrected test is **VOID on C3** — but the reason the
sample is too small *is the answer*: once the look-ahead is gone, moving the
threshold from 1.0R to 1.5R barely touches anything (65 of 3,139 exits, 2.1%),
and what it does touch it makes slightly worse (23 better vs 34 worse). The
+₹9.38 that passed every locked criterion was a lever on a backtest artifact,
not on the market. Do not re-propose 1.5R; do not sweep other thresholds on
2023/2024 — a threshold that looks best at an interior point of a spent window
is the classic overfit signature, and the parameter has now been shown to do
nothing once the data is honest.

⭐⭐ **The real find is the bug, and it is worth ~3× what the feature promised.**
`bt17` has been **understating the deployed arm by ≈ ₹13/trade** at real costs
for its entire history.
⚠️ **Not backtest-only.** `scanner.py` runs the same `engine.step()` on each
closed 1-minute bar and `_exit()` records the signal price, so the live paper
ledger books fills at the entry price on a bar that has already closed. Real
money is not exposed (the Kite path is not live), but the paper numbers are.

**The strategy is still not viable.** Fixed, it is gross −0.0139%/trade and
about −0.20%/trade net of the real 0.21% round trip. The fix removes a false
loss; it does not create an edge.

⚠️ `defer_breakeven_one_bar` is **OFF by default** — nothing in live or in any
prior replay changed. Turning it on is a deliberate decision for the operator,
not something to flip silently.

### Lessons
1. ⭐⭐ **A rule that "improves" P&L by changing WHEN a stop binds must be
   audited against the bar that armed it.** My four criteria were statistically
   impeccable — 8/8 quarters, p<1e-4, capital ratio exactly 1.000 — and all four
   were measuring an unplaceable order. Second time in two days that a locked
   criterion set passed for a reason it was not designed to detect (see
   [BT44 Arm H](2026-09-19-add-to-winner-at-1r.md)). **Statistical strength is
   not mechanism validity.** Before accepting any exit-timing win, check what
   fraction of the changed exits happen on the arming bar itself.
2. The codebase already knew: the cost-aware stop carries a comment saying a
   same-bar lift "would let that bar's own high justify a stop its low could
   already have hit". The plain breakeven lock never got the same treatment.
   **When one code path documents a hazard, grep for its siblings.**
