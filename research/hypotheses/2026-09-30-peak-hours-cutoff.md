# 2026-09-30 — Peak-hours entry cutoff (BT53)

**Status:** open · **Arm:** `warrior_strict` (NSE long) · **Registered before any P&L was computed.**

## Why

Operator, 2026-09-30, after IKS rallied 1,790 → 1,822 between 11:15 and 11:40 on
2026-09-29 and was never eligible: "what if I remove the before-11 constraint?"

`PEAK_HOURS_END = 11:00` is the one `warrior_strict` threshold that was never
measured. The 2026-09-15 hypothesis says so in as many words: "a judgement call,
not a measurement", chosen from session-volume shape. This file measures it.

## Hypothesis

Entries between 11:00 and the pre-existing 14:30 cutoff earn money net of real
costs, so the 11:00 cap is refusing profitable trades.

## Design

- Control: the live rule (`bt17 --warrior-strict --multi-entry --checkpoint-stop`),
  peak-hours end 11:00. Arm: identical, `--peak-hours-end 14:30` (= the ordinary
  14:30 entry cutoff, i.e. the constraint removed).
- **The arm is a superset of the control.** Every decision before 11:00 is
  unchanged (nothing before 11:00 reads anything after it), so the arm's extra
  trades are exactly the entries the cap refuses. The test is therefore run on
  those ADDED trades directly — no subset/anti-test ambiguity (cf. BT37/BT41).
  Verified in the run: control trades must reproduce inside the arm 1:1.
- Windows: 2022-10→12, 2023, 2024, 2026-01-01→09-25 — the BT52 set. **2025 is
  the sealed hold-out and is not touched.** All four windows are spent, so this
  run can KILL but cannot bless ([[feedback_dirty_window_can_kill]]); a pass
  earns one 2025 single-shot, nothing more.
- Costs: real MIS 0.206% round trip (`net_inr − stress_inr`), and stressed.

## Pass criteria (locked; all must hold on the ADDED trades)

1. Pooled mean real net ₹/trade > 0, one-sided t-test p < 0.05.
2. Real net positive in ≥ 3 of the 4 windows.
3. Drop the 5 best added trades → pooled real net still > 0.
4. Median added trade ≥ the control's median trade (the added trades are not a
   worse population than the ones we already take).

Fail any → KILL, 11:00 stays. No re-run with 12:00 / 13:00 / other cutoffs on
these windows: picking the best cutoff after seeing them is the tuning this
file exists to prevent. The per-hour breakdown is reported as description only.

## Result (2026-09-30, BT53) — 🔴 KILL, 1 of 4

`bt53_peak_hours_report.py`. Superset claim verified: all 536 control trades
reproduce inside the 14:30 arm with identical exits (0 missing, 0 changed).

| | n | real net total | mean/trade | median | gross %/trade |
|---|---|---|---|---|---|
| Added (11:00–14:30 entries) | 271 | −₹23,064 | −₹85.11 | −₹120.44 | +0.035 |
| Control (live rule) | 536 | −₹47,763 | −₹89.11 | −₹125.23 | +0.019 |

By window (added): 2022-Q4 −₹2,070 · 2023 −₹10,332 · 2024 −₹3,200 · 2026 −₹7,462 — negative in all four.

1. mean > 0, p < 0.05 — **FAIL** (−₹85.11, p = 1.000)
2. ≥ 3/4 windows positive — **FAIL** (0/4)
3. drop top-5 > 0 — **FAIL** (−₹26,491)
4. median ≥ control median — PASS (−₹120 vs −₹125)

Reading: the midday trades are neither better nor meaningfully worse than the
morning ones — both are the same ~zero-gross, cost-losing population. The cap
does not protect the strategy from a bad hour; it just limits how many
cost-losing trades it takes. Removing it = +51% trades, ≈ −₹23k more over the
four windows. 11:00 stays. Per §"Pass criteria", no other cutoff is tried here.

## Operator decision (2026-09-30)

Shipped anyway, at **14:00** (not 14:30): "remove this 11 am barrier from indian
stocks and shift the cutoff to 2pm". `engine.PEAK_HOURS_END`, `NSE.peak_hours_end`,
`Settings.mt_peak_hours_end` and the ECS `MT_PEAK_HOURS_END` are all 14:00; the
bt17 `--warrior-strict` mirror follows. From BT53's per-hour table the 14:00 arm
adds ≈ 258 trades (the 14:00–14:29 hour, 13 trades −₹1,452, is excluded), ≈ −₹21.6k
over the four windows — approximate, not re-run. Gate reports on warrior_strict
(long and short) must split at 2026-09-30. US is unaffected: its peak-hours rule
has been OFF since 2026-09-24 (entries to 15:10 ET).
