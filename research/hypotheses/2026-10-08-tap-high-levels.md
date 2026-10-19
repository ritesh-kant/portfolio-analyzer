---
slug: tap-high-levels
strategy: momentum_warrior_strict
status: killed
registered_at: '2026-10-08'
finalized_at: ''
decided_at: '2026-10-09'
hypothesis_hash: ''
---

# Hypothesis (BT61): a ceiling that 5-minute bars were rejected at repeatedly is real supply, and the engine misses it

## 1. Mechanism

LATENTVIEW 2024-03-28, bullish engulfing, buy 482.44 at 12:07. The 5-minute highs 486.95 / 487.20 /
487.20 at 11:05 / 11:15 / 11:25 are a clear triple top, all completed before the entry. The engine's
5-minute level set held only 494.00 (one touch, the 11:00 spike) and the round ₹490, so the displayed and
binding resistance was the round number, 0.57% above the real ceiling.

Cause (reproduced on the cached bars): the swing-pivot test needs a bar to be the single highest of ±3
bars. The 494 spike is inside that window of the 11:05 and 11:15 bars, and the 11:25 bar shares its high
with 11:15 (a tie fails `argmax == k`). Result: zero pivots at 487.20. Clustering and touch counting never
run on a price that produced no pivot. The 1-minute set did contain 487.20 (2 touches), but exits and the
target of a 5-minute setup read the 5-minute set only (`_open_position`, `tf = bars_5m`).

New level kind `tap_high` (`levels.tap_high_levels`): group 5-minute highs within 0.15% of a seed high; a
bar counts as a tap only if it closed below the level; taps must be >= 2 bars apart; the level is the top of
the taps. Structural at 2+ touches, the same rule pivots follow. Only completed bars are read. Constants
(`TAP_TOL_PCT=0.15`, `TAP_MIN_GAP_BARS=2`) were fixed from the LATENTVIEW chart before any replay.

Where it applies (5-minute data only; 1-minute setups unchanged): `exit_state.levels` of 5-minute setups
(target cap, structural exit, resistance-reject exit) and the 5-minute half of the 1R headroom veto, signal
and fill. `--tap-levels N` / `EngineConfig.tap_levels_min_touches`, 0 = off and reproduces every earlier run.

## 2. Expected effect size

Honest prior: small, probably null. BT37 (volume shelves) and BT50 (session highs) added resistance the same
way and were null: denser levels shrink targets and veto trades without changing expectancy, and the live rule
loses about ₹80/trade at real costs. The change is a correctness fix of what the levels are; it is not
expected to turn the rule profitable. A fix that is right but P&L-null is a legitimate outcome and is kept OFF.

## 3. Falsification criterion (LOCKED before running)

Control = live rule (`--warrior-strict --multi-entry`). Primary arm = control + `--tap-levels 3`. Secondary
arm = control + `--tap-levels 2`, reported but never allowed to rescue a failed primary. Windows:
2024-01-01..2024-12-31, 2025-01-01..2025-12-31, 2026-01-01..2026-09-29. Real-cost (itemised MIS) net.
All three years are spent: results can reject the change, never prove it better.

The arm changes entries as well as exits (veto), so it is NOT a subset of the control. Report both (a) the
trades common to both arms, paired by symbol-date + entry time, and (b) the full pooled sets. Random-subset
anti-tests are invalid for the reason recorded under BT42 and BT38. Print the capital-deployed ratio; if it
differs from 1 by more than 5% judge on net per rupee deployed, not per trade.

REJECT the change (flag stays OFF) if ANY:
1. Pooled arm net per trade (full sets, per rupee if the capital ratio is off) < control.
2. Pooled median net per trade of the arm < control median.
3. The arm is worse than the control in at least 2 of the 3 years (net per trade).
4. Pooled drop-top-5 net per trade of the arm < control's.

Mechanism check (reported, not a criterion): on the trades whose exit level changed, how often the new
`tap_high` is the binding resistance, and the median change in target distance in R.
LATENTVIEW 2024-03-28 must show structural_resistance 487.20 `tap_high` under `--tap-levels 3`.

## 4. Data / hold-out

Cached 1-minute bars 2024, 2025, 2026. No sealed hold-out remains (rule retired 2026-10-04); the live paper
record is the only untuned data. Even a pass here is a spent-window result and cannot be believed on its own.

## 5. Results

Run 2026-10-08/09, real-cost (itemised MIS) net, full pooled sets. Control = live rule, primary arm
`--tap-levels 3`, secondary `--tap-levels 2`. Runs: `bt17_trades_bt61_tap_{2024,2025,2026}_{ctl,t3,t2}`.
Scorer: `research/backtests/bt61_tap_levels_report.py`.

| | trades | net/trade | median | drop-top-5 | win % | deployed vs ctl |
|---|---|---|---|---|---|---|
| control | 283 | -₹78.6 | -₹127.9 | -₹95.4 | 31.8 | 1.000 |
| 3 taps (primary) | 280 | -₹82.1 | -₹135.3 | -₹99.2 | 31.4 | 0.990 |
| 2 taps (secondary) | 266 | -₹84.0 | -₹135.3 | -₹102.0 | 31.2 | 0.940 |

Primary: criteria 1, 2, 3 (worse in 3/3 years) and 4 all REJECT. Secondary (judged per rupee, capital ratio
0.94): 1, 2 and 4 REJECT, 3 passes (worse in 1/3). **Verdict: REJECTED, flag stays OFF.**

### Read this before believing the verdict: the mechanism barely engaged

Every trade in the live rule is `attention_1m_confirmation`, a 1-MINUTE setup, and the tap levels were scoped
to 5-minute data only (section 1). So for these trades the target / support-exit / resistance-reject side
never saw a tap level: `tap_high` was the binding resistance on **0 of 280** arm trades, and on the trades both
arms took the paired difference is exactly ₹0.00 (0 exits changed, 3-tap: 279 common trades). The only live
effect was the 5-minute half of the headroom veto, which removed 3 trades at 3 taps and 17 at 2 taps (and added
1), and that is all the table above measures. So this run says: adding tap levels to the veto does not help
the live rule. It does NOT test the case that prompted it (LATENTVIEW 2024-03-28 was a 5-minute
`bullish_engulfing`, the Lab's base trade, not a live-rule setup; it is not in any of these runs).

Control caveat: the flag-off control has 91 trades in 2024 against 92 in BT60's control (KARURVYSYA
2024-07-26 absent, every other exit identical). The flag-off path is unchanged by this work (`tap_min_touches=0`
is the default everywhere and the 1023 engine tests pass), so the difference comes from something else between
2026-10-05 and now; all arm-versus-control comparisons above use this run's own control.

Not a rescue: a follow-up that applies 5-minute taps to the target of 1-minute setups would be a different
hypothesis and needs its own BT number and criteria. The Lab checkbox `Tap-high ceiling` covers the 5-minute
base-trade case exploratorily.
