# Resistance checkpoint stop (BT52)

**Registered:** 2026-09-27, BEFORE any code was run on data.
**Proposed by:** operator, after the AARTIIND 2026-07-15 trade: capped target
₹509.20 = 0.45R, hit one minute after entry, +₹107.80 gross, +₹5 after real
costs.
**Arm:** `warrior_strict` exits only. Entries untouched.

## Idea

Today the fixed target is `min(2R, just under the nearest structural
resistance)` (BT50). Selling at a resistance that sits 0.45R away caps the win
below the trade's own costs. Instead:

* the target stays at **2R, never capped**;
* the price the current rule would have sold at becomes a **checkpoint**. When
  a 1-minute bar's high reaches it, the stop is lifted to
  **checkpoint × (1 − 0.15%)**, floored to the ₹0.05 tick;
* the lift binds from the **next** bar (same deferral as the breakeven lift
  since BT46 — a bar's high may not justify a stop its own low fills);
* nothing else changes: hard stop, 1R breakeven, swing trail, trend exits,
  support-break exit, 15:15 close. A trade whose 2R target is already below the
  nearest resistance has no checkpoint and is identical to control.

Parameters fixed a priori: one checkpoint (the one level control would sell
at), buffer 0.15% (BT50's buffer, not fitted). No sweep.

## Window

2026-01-01 → 2026-09-25, the 233 symbols with a 2026 cache, `--warrior-strict
--multi-entry`, fill `future_trigger`. **2026 is SPENT** (BT47/BT50/the YTD
report). Per the dirty-window rule it can KILL this idea but cannot bless it; a
pass earns one clean single-shot confirmation and nothing more.

Control = `bt17_trades_ytd2026.csv` (139 trades, real-cost net −₹9,715).

## Locked criteria (all must pass; money at the real itemised MIS cost model)

1. Arm total net ≥ control total + ₹20 × control trades (≥ +₹2,780).
2. Paired Δ net on trades matched by (date, symbol, entry_time, entry):
   two-sided t-test p < 0.10.
3. Median Δ of the matched trades whose outcome changed ≥ 0.
4. Δ total with the 5 largest positive changed-trade deltas removed > 0.
5. Deployed capital (Σ entry × qty) ratio arm/control within 0.95–1.05.

Mechanism check, printed but not a gate: count of `checkpoint_stop` exits,
how many filled at the bar's open (a gap through the stop), and how many
changed trades are the late 2R wins vs the given-back checkpoint wins.

## Result

🔴 **KILL — 1 of 5 criteria** (single run, 2026-09-27, no re-run).

139 trades both sides, all 139 matched (entries identical), 52 changed.

| | control (BT50 cap) | checkpoint arm |
|---|---|---|
| gross | +₹4,111 | +₹6,153 |
| net @ real costs | −₹9,715 | **−₹7,675** |
| win % @ real | 32.4 | 33.8 |

1. ✗ total Δ +₹2,040 vs ≥ +₹2,780 needed
2. ✗ paired mean +₹14.7/trade, p = 0.221
3. ✗ median changed trade −₹74.2
4. ✗ drop the top 5 → −₹992
5. ✓ capital ratio 1.000

Mechanism: the checkpoint was reached on 52 trades. **11** went on to 2R
(+₹3,870, mean +₹352); **30** came back and exited on the checkpoint stop
(−₹2,759, median −₹76 — the 0.15% buffer on ~₹47k notional, plus 6 gap fills
below the stop); 11 left on trend exits (+₹929). The whole gain is five trades
(JSWCEMENT, SCHNEIDER, ABSLAMC, NCC, COHANCE = +₹3,032). Same shape as
BT43/BT47: a rule that trades many small certain losses for a few large wins
nets ~0 on a zero-drift entry. AARTIIND 07-15 itself: +₹64 (resistance_reject
at 509.85 instead of target 509.20).

Stays OFF (`resistance_checkpoint_stop=False` default; bt17 `--checkpoint-stop`).
Direction was positive, so this is "no detectable effect", not "harmful"; do
not retune the buffer on 2026. Report: `bt52_checkpoint_stop_report.html`.

## Extension — 3 spent years (2026-09-28, operator request)

Same locked rule and criteria, no parameter changed. Window 2022-09-28 →
2024-12-31 plus 2026 YTD; **2025 hold-out deliberately excluded** (operator
chose this over "true last 3 years"). Descriptive: every year is spent.

| year | trades | changed | control net | arm net | Δ | paired ₹/tr | p |
|---|---|---|---|---|---|---|---|
| 2022 (Q4) | 52 | 14 | −₹8,077 | −₹7,236 | +₹841 | +16.2 | 0.47 |
| 2023 | 217 | 78 | −₹21,218 | −₹19,409 | +₹1,809 | +8.0 | 0.32 |
| 2024 | 131 | 50 | −₹14,804 | −₹13,445 | +₹1,359 | +5.3 | 0.63 |
| 2026 | 139 | 52 | −₹9,715 | −₹7,675 | +₹2,040 | +14.7 | 0.22 |
| **pooled** | 539/536 | 194 | **−₹53,813** | **−₹47,765** | **+₹6,048** | **+9.9** | **0.079** |

Criteria pooled: ✗ total Δ (+₹6,048 vs ≥ +₹10,780) · ✓ p = 0.079 · ✗ median
changed −₹74.8 · ✓ drop-top-5 +₹864 · ✓ capital 0.994 → **3/5, verdict
unchanged: KILL**. Direction positive in 4/4 years — a small, consistent
+₹10/trade on a system losing ₹100/trade. 40 trades ran to 2R (+₹13,561);
116 gave back to the checkpoint stop (−₹10,634, 21 gap fills). Not a fix:
the pool is gross ₹11 on 539 trades. Report `bt52_checkpoint_stop_3y_report.html`.

## Amendment — shipped ON for `warrior_strict` (2026-09-29, operator decision)

Operator: "keep this on by default, i don't want separate arm". So, despite the
3/5 KILL above, `scanner._strategy_config(warrior_strict)` and bt17
`--warrior-strict` now set `resistance_checkpoint_stop=True`; every other arm
and `EngineConfig()` keep it OFF. `bt17 --warrior-strict --no-checkpoint-stop`
replays the BT50 sell-at-cap control. Disclosed, not relitigated: the measured
effect is +₹9.9/trade (p = 0.08, positive 4/4 spent years), median changed
trade −₹75, on a pool that is gross ≈ 0. Any gate report on `warrior_strict`
must split trades before/after 2026-09-29.
