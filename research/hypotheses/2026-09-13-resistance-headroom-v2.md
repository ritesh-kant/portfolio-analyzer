# Resistance headroom v2 — refuse without room, and mean it

*Registered 2026-09-13. Status: **promising in-sample, UNCONFIRMED**. The
development result below was produced on the same window the rule was designed
on and must not be treated as a verdict.*

## 1. Provenance — read this before the numbers

This did not start as a hypothesis. The operator changed
`_resistance_aware_attention_confirmation` to (a) merge 5-minute levels into the
1-minute set and (b) refuse an entry whose next structural resistance sits closer
than one initial-risk unit. An A/B of that change measured **+0.0098 pp**, of
which the refusal contributed +0.0199 pp and late re-entries gave back −0.0101.

A probe then instrumented the rule over 805 confirmations and found two things:

| observation | number |
|---|---|
| the 5m merge changes the binding level | **7.3%** of confirmations |
| refusals whose blocking level is a **round number** | **30.9%** of 424 vetoes |
| refusals blocked by a real `pivot_high` | 57.3% |

v2 was built from those three facts. **It was therefore fitted to 2022-23**, the
same window it is scored on below. That is the central caveat of this document.

## 2. The frozen rule

`EngineConfig.resistance_veto_v2` (default **False**), three changes together:

1. **No 5-minute merge** — the level set stays on one timeframe (inert at 7.3%).
2. **Round marks excluded from the headroom test**, in *both* branches. A price
   grid is not supply, and round-number entry rules were separately measured at
   +0.022 pp and killed in this repo.
3. **A refusal ends the day** (`DayState.resistance_refused`) rather than
   freeing the slot for a later confirmation. Measured: replacement entries
   −0.0072% against +0.0362% for standing aside.

Attribution is forfeited by construction — three changes, one measurement.

Reproduce: `uv run research/backtests/bt17_momentum_pool.py --attention
--resistance-v2 --jobs 10 --start 2022-01-01 --end 2023-12-31 --symbols "$(...)"
--tag res_v2`, 224 symbols cached in both years, identical list per arm.

## 3. Development result (2022-23, IN-SAMPLE)

| arm | n | gross | win | net @ 0.21% | ₹/trade |
|---|---|---|---|---|---|
| base (`HEAD`) | 3,401 | +0.0163% | 36.8% | −0.1937% | −462 |
| operator's rule | 2,718 | +0.0261% | 36.3% | −0.1839% | −451 |
| **v2** | **1,894** | **+0.0466%** | 37.4% | **−0.1634%** | **−438** |

* v2 − base = **+0.0303 pp**, trade count **−44.3%**
* anti-strategy (random 1,894 of 3,401, 2,000 draws, seed 20260913): random mean
  +0.0157% ± 0.0113 → **p = 0.000**. Not a frequency effect.
* positive in **both** years: 2022 +0.0195 pp, 2023 +0.0418 pp
* refused trades gross −0.0175% vs base +0.0163% — it removes the worse ones
* total ₹ −828,636 vs −875,653 for a random cut of the same size

**Still loses money.** −0.1634%/trade at real costs. The lift is ~7× too small to
cross the 0.21% round trip. This changes the size of the loss, not its sign.

## 4. Locked criteria for the ONE confirmation run

The rule above is frozen. The confirmation window is **2025** — the least-used
data left (2022-23, 2024 and 2026 are spent for entry-side mining; 2025 has only
been read at pool level). One run, `--attention` base vs `--resistance-v2`, same
symbol-list discipline. **Criteria fixed before that run executes:**

| # | criterion | bar |
|---|---|---|
| C1 | direction | v2 − base gross **> 0** |
| C2 | size | v2 − base ≥ **+0.0150 pp** (half the in-sample +0.0303, the usual out-of-sample haircut) |
| C3 | not frequency | anti-strategy p < 0.05 vs random same-size subsets |
| C4 | still honest about costs | reported at 0.21% real and 0.80% stress, no exceptions |

C1-C3 must all pass. Anything less and v2 is **killed**, with no variant #3 on
this data — the in-sample result would then be documented as fitting.

Passing does **not** mean ship-to-live. It means the effect survived one honest
out-of-sample test while still being far too small to trade on its own. The only
thing that would make it matter is stacking with the execution fix (fill gap is
still mean +0.256% over trigger — larger than this entire effect).

## 5. Result

*(empty until the 2025 run executes — do not edit §4 after this point)*
