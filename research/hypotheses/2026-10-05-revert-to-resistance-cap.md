---
slug: revert-to-resistance-cap
strategy: momentum_warrior_strict
status: killed
registered_at: '2026-10-05'
finalized_at: ''
decided_at: '2026-10-05'
hypothesis_hash: ''
---

# Hypothesis (BT60): the plain resistance-capped target (BT50 rule) is no worse than the checkpoint stop

## 1. Mechanism

Operator request (2026-10-05): simplify. Drop the checkpoint stop (BT52, live since 09-29) and go back
to target = min(2R, resistance minus 0.15% buffer), the whole quantity sold when the target fills.
The engine has no partial exits, so "sell all" is already the behaviour. EMA9 stays ON.
This is exactly BT52's control arm, so the prior is known: checkpoint beat the cap by +₹9.9/trade
pooled (p=0.079) and in 4/4 years. This run tests whether the simpler rule is acceptable anyway,
on three calendar years including 2025 (the sealed-2025 rule was retired 2026-10-04).

## 2. Expected effect size

Direction: cap ≤ checkpoint by roughly ₹5-15/trade (BT52). The simplification is expected to cost money.

## 3. Falsification criterion (LOCKED before running)

Control = live rule (`--warrior-strict --multi-entry`, checkpoint ON). Arm = control +
`--no-checkpoint-stop`. Windows: 2024-01-01..12-31, 2025-01-01..12-31, 2026-01-01..09-29.
Real-cost (itemised MIS) net, paired by symbol-date + entry time where both traded. All three
years are spent: results can reject the simplification, never prove the cap better.

REJECT the simplification (keep the checkpoint) if ANY:
1. Pooled arm − control net per trade < ₹0.
2. Pooled median net per trade of the arm < control median.
3. The arm is worse than the control in at least 2 of the 3 years (net per trade).
4. Pooled drop-top-5 net per trade of the arm < control's.

Capital deployed per trade is printed; the change is exit-only, so size is unchanged.

## 4. Data / hold-out

Cached 1-minute bars 2024, 2025, 2026. No sealed hold-out remains; the live paper record is the only untuned data.

## 5. Results

Run 2026-10-05, real-cost (itemised MIS) net. Control = live rule (checkpoint ON), arm =
`--no-checkpoint-stop`. 2026 control = `bt17_trades_bt59_ema9_off_2026_ctl` (same flags, same code).
Runs: `bt17_trades_bt60_cap_{2024,2025}_{ctl,new}`, `bt17_trades_bt60_cap_2026_new`.

| year | trades | net/trade ctl | arm | Δ paired | p | exits changed | net ₹ ctl | arm |
|---|---|---|---|---|---|---|---|---|
| 2024 | 92 | −₹71.4 | −₹77.4 | −₹6.0 | 0.47 | 21 | −6,568 | −7,123 |
| 2025 | 98 | −₹110.3 | −₹112.8 | −₹2.5 | 0.69 | 11 | −10,810 | −11,054 |
| 2026 | 94 | −₹57.3 | −₹62.1 | −₹4.8 | 0.39 | 10 | −5,386 | −5,835 |
| **pooled** | 284 | **−₹80.2** | **−₹84.5** | **−₹4.4** | 0.26 | 42 | −22,764 | −24,012 |

Identical entries in every year (284/284, 0 replacements); capital deployed ratio 1.000. Median pooled
−₹135.3 → −₹126.8; drop-top-5 −₹97.0 → −₹99.8.

Criteria: (1) pooled net/trade −₹84.5 < −₹80.2 **REJECT**; (2) pooled median −₹126.8 vs −₹135.3
not worse **pass**; (3) worse in 3 of 3 years **REJECT**; (4) drop-top-5 −₹99.8 < −₹97.0 **REJECT**.
**Simplification REJECTED on criteria 1, 3 and 4; keep the checkpoint.** Direction matches BT52
(checkpoint better in every year, now including 2025), but the gap is smaller than BT52's
+₹9.9/trade (−₹4.4 here) and not significant (p=0.26). Exit mix: cap arm sells 63 at target vs 36,
checkpoint ON had 24 `checkpoint_stop` exits that the cap arm does not have. Both rules lose
₹80–85 per trade; neither is an edge. The rule difference is worth about ₹4/trade.
