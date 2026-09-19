# False break must deteriorate, not merely sit below the level

Registered 2026-09-19, **before implementation and before any measurement**.
Nothing below "Results" existed when this was written. Not enabled. No live
change. This file exists so the rule cannot be tuned after seeing its number.

## The gap being closed

`setups.false_break` currently exits when the last two closes are both below
the broken level. It does not care what those two closes did relative to each
other. Two situations are treated identically:

1. **Failing** — close 1 below the level, close 2 lower still. The level gave
   way and supply is winning.
2. **Consolidating** — close 1 below the level, close 2 *higher* than close 1,
   pressing back toward the level. Price is recovering, just not yet through.

Case 2 is the textbook retest of a broken level from underneath. Exiting it is
the behaviour the two-close rule was supposed to stop, and it still does it —
the two-close change only removed the *single*-close version of the mistake.

## Fixed rule

Add one condition to the existing predicate, changing nothing else:

> the final close must be strictly below the first confirming close.

Formally, with `closes = 2`: `bars[-1].close < bars[-2].close`, in addition to
the existing requirement that both are below `level` and that some bar in the
`FLAG_MAX_BARS` lookback exceeded `level`.

Ties (`==`) do **not** count as deterioration and do not exit. No tolerance
band, no percentage threshold, no ATR scaling — a threshold is a tunable and
this test is specifically for the sign, not a magnitude. Everything else is
frozen: entry side, level derivation, stop/target ordering, trend exits,
sizing, costs, fills.

Shipped behind `EngineConfig.false_break_requires_deterioration`, default
**False**, plus `bt17 --false-break-deterioration`. Live stays off regardless
of outcome until a forward window says otherwise.

## Data

- **W1** 2024 full year, cached symbols, `--attention`. Already spent for
  entry-side mining, and spent again by BT42 for the exit side. It can
  therefore **KILL but not BLESS** (see the standing rule).
- **W2** 2022–2023, cached symbols, `--attention`. Also previously used.

Both windows are dirty. This test can only remove the idea. If it survives,
the rule stays OFF and the next evidence must be forward paper trading.

## Criteria — fixed before the run

Measured on the same-entry paired subset, deterioration ON vs the current
(post-fix) two-close rule OFF:

| # | Criterion | Threshold |
|---|---|---|
| G1 | gross lift per trade | ≥ **+0.05 pp** pooled across W1+W2 |
| G2 | sign consistency | lift > 0 in **both** windows |
| G3 | anti-test | `p < 0.05` vs refusing the same number of false-break exits at random, replayed through the engine |
| G4 | sample | ≥ **100** false-break exits affected across W1+W2 |

G3 must use the replacement-aware null, not random subsets of the OFF trades.
A false-break exit arms the reclaim re-entry path, so suppressing one can
create a later trade; the ON set is **not** a subset of the OFF set. This is
the same trap that produced BT38's false PASS on volume shelves and that the
price/volume gate hit in `2026-09-19-price-volume-gate-ab.md` (65 refused, 71
created).

**Verdict rule.** Any criterion failing ⇒ **KILL**, flag deleted, no variant
on this data. All passing ⇒ not killed, stays OFF, forward test required.

## Expected value, stated in advance

Prior is poor and is written down here so it cannot be revised afterwards. The
two-close change itself moved 2024 P&L by +₹3.45/trade (p = 0.211) on a
−₹507/trade base, and BT42 showed the rule already "wins more often without
earning more". Deterioration is a strictly narrower filter on an exit that the
trail and hard stop mostly duplicate: 9 of 9 displaced exits in the 2026 pilot
became a trail stop or hard stop within a bar at nearly the same price. The
realistic best case is a small positive skew change, not a P&L lever, and the
honest base rate from this repo's selectivity tests is that it reads as random
deletion.

## Results

_Not yet run._
