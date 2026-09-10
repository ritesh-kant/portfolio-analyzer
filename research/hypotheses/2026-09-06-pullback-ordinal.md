---
slug: pullback-ordinal
strategy: momentum_trader
status: killed
registered_at: '2026-09-06'
finalized_at: '2026-09-06'
decided_at: '2026-09-06'
hypothesis_hash: ''
parent: momentum-catalyst-upstox-v2
---

# Hypothesis: Trades taken on the 1st, 2nd or 3rd pullback after the day's first sharp advance earn a higher mean gross return than trades taken on the 4th or later pullback, because a trend that has already been bought three times is extended and the remaining buyers are late.

> ⚠️ **Why this is a separate file.** BT17 and two follow-ups have already run on
> this data. The operator then asked to trade only the 1st–3rd pullback. That is
> an entry-selectivity change proposed after seeing results, so it gets its own
> file, its own criterion and its own dev/hold-out split, like
> `trend-exit-vs-fixed-target` and `entry-fill-latency` before it. The v2 gates
> are untouched.
>
> ⚠️ **Multiple-testing disclosure.** This is the **fourth** examination of the
> 2024 window (BT17 pool, trend-exit, fill-latency, this). Each additional look
> raises the chance that a good-looking result is noise. Two protections apply
> and neither may be waived: the 2025 hold-out stays sealed until dev decides,
> and the anti-strategy shuffle in §4 must clear before any PASS is recorded.
> If this fails, the 2024 window should be considered spent for entry-side
> mining and the next test must run on different data.
>
> ⚠️ **Observational split, not a filter.** The ordinal is *recorded* on every
> candidate; nothing is gated on it. The trade set is therefore byte-identical
> to `entry-fill-latency`'s baseline arm (992 trades), and the comparison is a
> split of trades we already took. No trade is added or removed, so there is no
> selection effect to unpick.

## 0. What "1st, 2nd, 3rd pullback" means here (frozen)

The operator's requirement is that counting starts from the day's **first big
move up**, not from the opening bell and not from when the stock joined the
watchlist. A stock that rallied, pulled back four times and only then crossed
our +4% line is on its fifth pullback even though it is the first one we ever
looked at. That gap is the whole point of this test.

Two definitions are needed, and **both are reused unchanged from code that was
already frozen** before this hypothesis existed. No new tunable number is
introduced anywhere in this test.

**The anchor — "the first big move up":** the first *pole* of the day, using
`setups.bull_flag`'s existing rule (`POLE_MIN_PCT = 2.0`, `POLE_MAX_BARS = 6`):
the earliest window of ≤ 6 five-minute bars whose high-to-low range is ≥ 2% and
whose last bar closes in the top 40% of that range. The anchor is the timestamp
of that window's final bar.

**A pullback:** a confirmed swing pivot high on the 5-minute chart, using
`levels.swing_pivots`'s existing rule (`PIVOT_K = 3`): a bar whose high is the
highest of the three bars either side. A pivot needs 3 bars to print after it
before it is confirmed, so this never looks ahead.

**The ordinal**, evaluated at the moment a setup fires:

```
pullback_ord = 1 + (confirmed 5-min pivot highs strictly after the anchor)
```

So a setup firing before any pivot has confirmed is ordinal 1 (the first
pullback of the move), and so on. Counted on 5-minute bars for **every** setup
including `micro_pullback`, so the number means the same thing on every row:
how deep into the day's trend this entry sits.

**No anchor:** if no pole forms before the setup fires, the day never had a
sharp advance to count from and `pullback_ord` is null. Those trades are
reported as a separate bucket and are **excluded from the primary comparison**.

## 1. Mechanism

Each pullback that gets bought consumes the supply of traders still willing to
buy higher. By the fourth, the people who wanted in are in, the early buyers are
sitting on profit and looking to sell, and the pattern's breakout has fewer
buyers behind it. Ross Cameron's stated preference for the first and second
pullback is the same claim.

If it is true here, ordinal 1–3 trades should show a higher mean gross return
than ordinal 4+, and the gap should not survive shuffling the labels.

**Why it might fail.** BT17 already showed 98% of our trades are the 1st–3rd
*pattern of the day*, and `one_trade_per_day` means we only ever take one. The
ordinal may turn out to be 1 or 2 on nearly every trade, leaving too small a
late-pullback group to compare against. It is also possible that lateness is
already priced into the chase guard, which rejects extended entries.

## 2. Expected effect size

- Mean gross %/trade, ordinal 1–3 minus ordinal 4+: **+0.5 to +1.5**
  percentage points.
- Ordinal 4+ expected to be gross-negative outright.
- Most trades expected to land at ordinal 1–2.

## 3. Feasibility precondition

The comparison group must be real. **n(ordinal ≥ 4) ≥ 30** on the 2024 dev
window. If it is smaller, this hypothesis is **NOT TESTABLE on this data** and
must be recorded as such — it may not be rescued by lowering the cut to 1–2 vs
3+, by merging buckets, or by widening the window to 2024+2025.

## 4. Falsification criterion (LOCKED before any code is written)

Dev window 2024 only. Same cached bars, same 992 trades, split by ordinal.
**All four must pass. Any one failing is a KILL.**

- **PRIMARY-spread:** mean gross %/trade (ordinal 1–3) − (ordinal 4+)
  ≥ **+0.50** percentage points, positive sign.
- **PRIMARY-level:** mean gross %/trade of the ordinal 1–3 group
  ≥ **+0.30%**. A spread between two losing groups is not an edge — if the kept
  side is still near zero gross it cannot cover the ~1.0% round-trip cost and
  filtering changes nothing. This gate exists because `entry-fill-latency`
  produced a real, significant effect that was nonetheless useless.
- **anti:** shuffle the ordinal labels across trades 5,000 times;
  p(shuffled spread ≥ observed) < **0.10**.
- **n:** ordinal 4+ group ≥ 30 trades (§3).

**Hold-out (2025), touched once, only if all four dev criteria pass:** the
spread must be ≥ **+0.25** points, correctly signed, and the ordinal 1–3 group
gross-positive. Otherwise KILL.

🔒 **No-relax rules:**
- The cut is **1–3 vs 4+**, fixed, because that is what was asked for. If the
  data suggests 1–2 or 1–4 would look better, that is a new hypothesis file, not
  an adjustment here.
- The anchor and pivot definitions are the frozen ones named in §0 and are not
  to be retuned. Changing `POLE_MIN_PCT`, `POLE_MAX_BARS` or `PIVOT_K` to make
  the ordinal distribution more agreeable is fitting to the data.
- A per-setup result that looks good is an observation, not a filter.
- Passing does **not** license live money. It licenses adding the ordinal as a
  gate in the forward capture, nothing more.
- If §3's precondition fails, the answer is "not testable here", not a redesign.

## 5. Data & split

- **Dev:** 2024-01-01 → 2024-12-31.
- **Hold-out:** 2025-01-01 → 2025-12-31. Never read until dev decides.
- Cache: `research/backtests/.cache_upstox/1m/`, already populated. No fetches.
- Costs: production intraday MIS plus the +40 bps/side stress, identical across
  buckets, so the split cannot be distorted by cost modelling.

## 6. Code

- `pullback.py` — `first_pole()`, `pullback_ordinal()`; reuses `POLE_MIN_PCT`,
  `POLE_MAX_BARS` from `setups.py` and `swing_pivots`/`PIVOT_K` from `levels.py`.
- `engine.py` — records `pullback_ord` on each `Candidate`. **Recording only;
  no gate**, so the trade set is unchanged.
- Runner: `bt17_momentum_pool.py` writes the column and reports the split.
- Tests: `tests/momentum_trader/test_pullback.py`.

## 7. Result — 2024 dev, run 2026-09-06

### Code-state disclosure

The run took **991** trades, not the 992 named in §4. Between registration and
the run, an unrelated fix landed in `setups.py`: a `_has_stop_room(trigger, stop)`
validity check now rejects a setup whose stop is not strictly below its trigger.
That removes exactly one degenerate trade (KAJARIACER 2024-11-25, a zero-range
pause bar giving trigger == stop), which was discovered during `entry-fill-latency`
and queued as separate work. It is a validity check, not a threshold — `MIN_STOP_PCT`
was deliberately not applied — so no frozen parameter changed.

One trade of 992 cannot move any criterion below, all of which miss by wide
margins. Recorded for reproducibility, not as a caveat on the verdict.

### The test was feasible

§3's precondition passed: **155 trades on ordinal 4+**, well above the 30
required. This is a real answer, not "untestable".

| ordinal | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| n | 378 | 256 | 120 | 83 | 37 | 23 | 7 | 4 | 1 |
| gross %/trade | −0.083 | −0.039 | −0.034 | −0.150 | +0.215 | −0.110 | −0.166 | −0.546 | −0.436 |

909 trades were anchored; 82 never formed a pole before entry and are excluded
from the primary per §0 (their gross was −0.284%/trade, the worst bucket of all).

### All three testable criteria FAILED

| | keep (ord 1–3) | late (ord 4+) |
|---|---|---|
| n | 754 | 155 |
| gross %/trade | **−0.060** | **−0.070** |
| stressed net %/trade | −1.066 | −1.075 |
| win % | 13.79 | 13.55 |

| criterion | required | observed | verdict |
|---|---|---|---|
| PRIMARY-spread | ≥ +0.50 pp | **+0.0091 pp** | FAIL |
| PRIMARY-level | ≥ +0.30% | **−0.0605%** | FAIL |
| anti (shuffle p) | < 0.10 | **0.4688** | FAIL |
| n(ord 4+) | ≥ 30 | 155 | PASS |

**The anti-strategy result is the decisive one.** p = 0.469 means that if the
ordinal labels are shuffled at random, a spread this large or larger appears
about half the time. The split carries no information whatsoever. This is not a
small effect below a demanding bar, as `entry-fill-latency` was (t = 10.40);
it is indistinguishable from randomly labelling the same trades.

### The mechanism is not merely absent — it is faintly inverted

Within the keep group the ordering runs the wrong way: ordinal 1 (−0.083%) was
*worse* than ordinal 2 (−0.039%) and ordinal 3 (−0.034%). Ordinal 5, with 37
trades, was the single best bucket in the table at **+0.215%** gross. §2
predicted ordinal 4+ would be gross-negative outright and ordinal 1–3 clearly
better; neither held.

The only monotone pattern is at the far tail — ordinals 7, 8 and 9 (12 trades
between them) are all sharply negative — which is what a handful of exhausted
late entries should look like, but 12 trades is noise and there is no cut at
1–3 that captures it.

### Interpretation

"Buy only the first three pullbacks" is a real and widely taught idea, and this
data does not support it on NSE intraday movers. The most likely reason is
visible in the distribution: `one_trade_per_day` means we take at most one entry
per stock, and 83% of anchored entries already land on ordinal 1–3. The rule was
close to already-satisfied, so imposing it removes 155 trades that were
performing the same as the ones it keeps.

## 8. Decision

- [x] **KILL** — three of four locked criteria failed: spread +0.009 pp against
      +0.50 required, keep-side level −0.061% against +0.30% required, and
      anti-strategy p = 0.469 against < 0.10 required.

**Hold-out (2025) NOT touched.** It remains sealed.

**No pullback gate is added** to the forward capture. `pullback_ord` stays as a
**recorded field** on every candidate — it costs nothing, it is now measured,
and the forward log will carry it for anyone who asks this question again.

**Do not move the cut.** Ordinal 5 being the best bucket, and ordinals 7–9 being
the worst, are exactly the kind of observations that invite a re-split. §4
forbids it, and the anti-strategy p-value says any such split would be fitting
to noise.

**2024 is now spent for entry-side mining.** This was the fourth examination of
the window (BT17 pool, trend-exit, fill-latency, this), as disclosed at
registration. Per that disclosure, further entry-side hypotheses on 2024 should
not be run; the next test must use different data. The 2025 hold-out is not a
substitute — spending it on mining is precisely what it is protected against.

**Scoreboard.** Entries measured twice, exits once, execution once, entry
timing (this) once. The **catalyst gate remains the only untested component of
`momentum-catalyst-upstox-v2`**, and it is now also the only remaining lever
that can be tested without touching 2024 again, because it requires new data
(results dates) by construction.

## Post-hoc disclosure (2026-09-06): breakeven lock leaked into `fixed_2r`

While decomposing losses on 2026-09-06 it was found that `exits.update_high`
applied the trend-mode breakeven-at-1R lock in **every** mode, including
`fixed_2r`, from the moment `exits.py` was introduced (2026-09-05). Spec §4 lists
exactly four fixed-mode exits (false_break, stop, target, 15:15); the original
BT17 run had zero `trail_stop` exits. With the leak, ~24% of `fixed_2r` trades
exited at breakeven and target hits fell from ~25% to ~15%.

Measured on 2022–2023 (2,408 identical entries): gross **−0.046% → +0.025%**,
win rate 21.6% → 33.9%, realised R:R 3.18 → 2.06 (i.e. the planned 2:1), target
exits 348 → 594, `trail_stop` 575 → 0. Fixed in `ExitConfig.for_mode(MODE_FIXED)`
(`arm_at_r = breakeven_at_r = inf`); regression test
`tests/momentum_trader/test_fixed_mode_no_lock.py`.

**Effect on this file:** an observational split of one arm that carried the
leak. Every bucket shares the same exit logic, so the spread (+0.009 pp) and
anti-strategy p (0.469) are unaffected in kind. Absolute bucket levels are the
leaked numbers. Verdict stands.
