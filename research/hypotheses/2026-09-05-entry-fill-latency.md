---
slug: entry-fill-latency
strategy: momentum_trader
status: killed
registered_at: '2026-09-05'
finalized_at: '2026-09-05'
decided_at: '2026-09-05'
hypothesis_hash: ''
parent: momentum-catalyst-upstox-v2
---

# Hypothesis: On the identical trades BT17 already measured, filling at the trigger level instead of the next bar's open raises mean gross return per trade by at least +0.30 percentage points, because the current engine detects a level break only at bar close and then waits one more bar to fill.

> ⚠️ **Why this is a separate file.** BT17 ran on 2026-09-05 and reported gross
> 0.00%/trade. The operator then observed that the engine waits for a candle to
> close before acting, and asked whether acting earlier recovers anything. That
> is a change to the entry rule proposed after seeing a result, the same shape
> as `trend-exit-vs-fixed-target`, so it gets its own file, its own criterion
> and its own dev/hold-out split. The v2 gates are untouched.
>
> ⚠️ **This measures a CEILING, not a strategy.** The treatment arm fills at the
> trigger price with zero slippage, which no real order achieves. The number it
> produces is therefore an upper bound on what any latency work could ever be
> worth. If the ceiling is below the bar, the per-tick rebuild is not worth
> building and no further latency work is justified. If the ceiling is above the
> bar, a realistic slippage-bearing version becomes a separate hypothesis.

## 0. The lag being measured

Five of the seven setups trigger on price taking out a known level — the prior
bar's high, the pause bar's high, yesterday's close. The level is known before
the bar that breaks it. What the engine currently does with that:

```
10:30:00  5-min bar opens
10:31:40  price crosses the trigger level        ← the event
10:35:00  5-min bar closes, scan_setups() fires  ← detection
10:35:00  pending entry recorded
10:36:00  next 1-min bar opens → FILL            ← execution
```

So the fill lands up to ~6 minutes after the level was actually crossed (up to
5 minutes inside the 5-min bar, plus 1 minute waiting for the next 1-min open).
For the 1-minute `micro_pullback` it is up to ~2 minutes. In a stock already
moving 4–8% on 3× volume, that is not a rounding error.

Both arms are replays of the same `scan_setups` output on the same cached bars.

| Arm | Fill price |
|---|---|
| `next_open` (baseline, = BT17) | open of the 1-min bar after the trigger bar closes |
| `trigger` (treatment) | the trigger level itself, exactly, no slippage |

## 1. Mechanism

A resting buy-stop order at the trigger level is a real, available order type.
It fills the instant price touches the level, with no polling and no decision
latency — strictly better than reacting to a tick 5–10 seconds early, and it
does not require predicting anything. If the current fill is systematically
above the trigger (because a momentum stock keeps going during those minutes),
switching to a resting order captures that difference.

The entry price also propagates: a lower entry means a shorter distance to the
unchanged pattern stop, so risk-based sizing buys more shares and the 2R target
sits nearer. The engine recomputes the whole plan from the new fill, so exit
outcomes may legitimately differ between arms. That is part of the effect being
measured, not a confound.

**Why it might fail.** The chase guard already refuses fills more than 1% past
the trigger, so the worst-lagged entries are currently discarded rather than
taken badly. The surviving fills may already be close to the trigger, leaving
little to recover. And a nearer target converts some current winners into
smaller winners.

## 2. Holding the trade set constant

The chase guard is evaluated against the **next-bar open in both arms**, even
though the treatment arm does not fill there. Without this, `trigger` mode
would never look chased (fill == trigger by construction) and would take extra
trades the baseline refused, so the comparison would mix a fill-price change
with a selection change.

Consequence: both arms take **exactly the same trades, at exactly the same
timestamps**. Any difference is attributable to the fill price and its
downstream sizing/target effects, and nothing else. Verified in the run by
asserting equal trade counts and equal entry timestamps.

Note this makes the ceiling *conservative* in one direction: a real resting
order would also capture some trades the chase guard currently rejects. That
expansion is a different, larger change and is explicitly out of scope here.

> 📌 **Outcome (added after the run, prediction left as written):** one trade of
> 992 did diverge — a zero-range pause bar whose trigger and stop were the same
> price, which `trigger` mode correctly cannot fill. The primary was therefore
> computed paired on the 991 common trades. See §7.

## 3. Expected effect size

- Mean gross %/trade, `trigger` minus `next_open`: **+0.15 to +0.60**
  percentage points. Anchored on the chase guard's 1% cap: fills cannot be more
  than 1% above the trigger, and the average should be well inside that.
- Win rate expected to **rise**, since every entry is at or below the baseline's.
- `target%` share of exits expected to rise (nearer target), `stop%` to fall.
- Mean qty expected to rise (shorter stop distance for the same ₹500 risk).

> 📌 **Outcome (added after the run, predictions left as written):** the fill-gap,
> exit-mix and qty predictions all held. Two did not: the uplift landed at
> +0.235 pp, **below** the predicted +0.15–0.60 band's midpoint and below the
> criterion; and win rate **fell** (13.51% → 12.82%) rather than rose, because
> "every entry at or below the baseline's" is false — the next bar opened below
> the trigger in 19.3% of trades. See §7.

## 4. Falsification criterion (LOCKED before any code is written)

Dev window 2024 only. Same cached 1-minute bars BT17 used, no new fetches.

- **PRIMARY:** mean gross %/trade, `trigger` − `next_open`, ≥ **+0.30**
  percentage points. Below that, closing the entry lag cannot pay for a
  per-tick scanner rebuild and this line of work stops.
- **Secondary, reported not gating:** the raw fill gap (mean and distribution of
  `fill / trigger − 1`, in %), stressed net %/trade, win rate, exit-reason mix,
  mean qty, the same table per setup, and the per-trade paired difference with
  its t-statistic.
- **Cost note:** the +40 bps/side stress is applied identically in both arms, so
  the net comparison already carries a slippage penalty even though the gross
  comparison does not.

**Hold-out (2025), touched once, only if the dev primary passes:** the same
gross improvement must be ≥ **+0.15** points, correctly signed. Otherwise KILL.

🔒 **No-relax rules:**
- Two arms only. A partial-fill model, a tick-level replay, or a "N seconds
  before close" variant is a new hypothesis file, not a tweak here.
- The chase guard stays at 1% and stays evaluated on the next-bar open in both
  arms. Loosening it to admit more trades is the selection change this design
  exists to exclude.
- **A PASS does not mean the strategy works.** BT17's stressed net is
  −1.00%/trade, so viability needs roughly +1.00 pp of gross. This criterion is
  set at +0.30 pp because that is the threshold for *engineering effort being
  justified*, not for profitability. A PASS licenses building per-tick
  evaluation and resting stop orders in the scanner. It does not license live
  money, and it does not on its own make the pool tradeable.
- The close-confirmed setups (`flat_top_breakout`, `orb15`) require a bar to
  close above the level. They are included unchanged: their trigger level is
  still known in advance, so a resting order above it is still valid, and no
  part of this test reads a partial bar or predicts a close.

## 5. Data & split

- **Dev:** 2024-01-01 → 2024-12-31, both arms.
- **Hold-out:** 2025-01-01 → 2025-12-31. Never read until dev decides.
- Cache: `research/backtests/.cache_upstox/1m/` (571 MB, already populated).
- Prior evidence, not part of this split: BT17 over 2024+2025 under `next_open`
  gave 1,876 trades, gross 0.00%, stressed net −1.00%.

## 6. Code

- `engine.py` — `EngineConfig.fill_mode` ∈ {`next_open`, `trigger`}; the
  `next_open` path stays byte-identical to BT17, verified by the existing
  engine tests continuing to pass.
- Runner: `bt17_momentum_pool.py --fill-mode {next_open,trigger}`.
- Tests: `tests/momentum_trader/test_fill_mode.py`.

## 7. Result — 2024 dev, run 2026-09-05

Baseline arm reproduced BT17 exactly: **992 trades, gross −0.0807%, stressed net
−1.0863%**. The `next_open` path is unchanged, as designed.

| arm | n | gross %/trade | stressed net %/trade | win % | mean qty |
|---|---|---|---|---|---|
| `next_open` (BT17) | 992 | −0.081 | −1.086 | 13.51 | 96.7 |
| `trigger` (ceiling) | 991 | **+0.155** | −0.852 | 12.82 | 100.8 |

### One trade diverged, and correctly

KAJARIACER 2024-11-25 `micro_pullback` had **trigger == stop == ₹1,217.35** — a
zero-range pause bar, so the pattern's high and low were the same price. In
`next_open` the fill drifted up to ₹1,221.95, manufacturing a 0.38% stop
distance out of nothing, and the trade was taken (and lost, −0.376% gross). In
`trigger` the fill *is* the stop, so `plan_trade` correctly refused it.

This is not a comparison defect: a resting buy-stop whose stop sits at the same
price is not a trade. But note the direction — dropping a baseline **loser**
slightly flatters the treatment arm, so the primary below is computed **paired
on the 991 trades present in both arms**, which removes the confound entirely.

> **Cause fixed 2026-09-05, after this run.** The zero-range bar was a detector
> defect, not a fill-model artifact: `setups.micro_pullback` (and the other six)
> emitted a `Setup` whose stop was not below its trigger, and `plan_trade` could
> not catch it because it judges the stop band against the fill. The detectors
> now reject that at construction (`setups._has_stop_room`; spec §4). **The
> numbers on this page stand exactly as run and were not recomputed** — BT17 was
> deliberately not re-run. For the record, replaying the guard over the recorded
> BT17 candidate log: it removes 8 of 2,948 candidates (3 in 2024, 5 in 2025) and
> exactly **1 of the 1,876 pool trades** — this KAJARIACER one. The baseline arm
> here would become 991 trades and the two arms would then agree trade-for-trade;
> the paired primary of +0.2352 pp is already computed on those
> 991 and is therefore unaffected. The decision in §8 does not change.

### PRIMARY criterion (≥ +0.30 pp gross uplift): FAILED

Paired on 991 common trades, same entry timestamps:

| | value |
|---|---|
| gross uplift, `trigger` − `next_open` | **+0.2352 pp** |
| 95% confidence interval | **[+0.191, +0.280]** |
| paired t-statistic | **10.40** |
| median per-trade difference | +0.131 pp |
| improved / identical / worse | 61.8% / 10.1% / 28.2% |
| stressed net uplift | +0.2339 pp |

**The whole confidence interval sits below the locked +0.30 bar.** This is a
FAIL, and not a marginal one that more data would resolve — the effect is
measured precisely and it is precisely too small.

### The effect is real, unlike the exit test

t = 10.40 here against t = 0.18 for `trend-exit-vs-fixed-target`. The mechanism
is confirmed on every diagnostic:

- The baseline pays a mean **+0.204%** above the trigger to enter (median
  +0.176%, p90 +0.627%, capped at +0.99% by the chase guard).
- Removing that gap flips pool gross from **−0.081% to +0.155%** — the first
  positive gross reading in this entire line of work.
- The sizing channel amplifies it: a lower entry means a shorter stop distance,
  so mean qty rises 96.7 → 100.8 and the 2R target sits nearer. **Target exits
  more than double, 149 → 312, and hard stops nearly halve, 361 → 216.** The
  uplift (+0.235) therefore exceeds the raw price gap (+0.204).

### Why the ceiling is not the full fill gap

In **19.3% of trades the next bar opened BELOW the trigger** — price broke the
level and pulled back. On those 191 trades the baseline's patience is worth
**+0.258 pp** and a resting order gives it up; on the other 800 the resting
order wins **+0.353 pp**. The current wait-one-bar behaviour is accidentally
harvesting a small pullback benefit one time in five.

### Per setup (paired, observations only — not filters)

| setup | n | gross base | gross trigger | uplift | baseline fill gap |
|---|---|---|---|---|---|
| `micro_pullback` | 512 | −0.174 | +0.134 | +0.307 | +0.232 |
| `ma9_pullback` | 157 | +0.049 | +0.145 | +0.096 | +0.076 |
| `flat_top_breakout` | 127 | −0.067 | +0.280 | +0.346 | +0.324 |
| `bull_flag` | 109 | −0.018 | +0.086 | +0.104 | +0.162 |
| `vwap_reclaim` | 76 | +0.144 | +0.191 | +0.047 | +0.139 |
| `orb15` | 10 | +0.100 | +0.270 | +0.170 | +0.249 |

`micro_pullback` and `flat_top_breakout` clear +0.30 individually. Per §4 these
are observations, not a licence to trade only those two — that selection would
be fitted to this result and is explicitly forbidden by the no-relax rules.

### Viability is untouched

Stressed net improves from −1.086% to −0.852%, so **every arm still loses**.
Gross going positive by 0.155% against a ~1.0% round-trip cost is not a
tradeable edge. The +0.30 pp bar was a threshold for justifying engineering
effort, never for profitability; §4 recorded that viability needs roughly
+1.00 pp before the run.

## 8. Decision

- [x] **KILL** — the pre-registered +0.30 pp primary failed at **+0.2352 pp**,
      95% CI [+0.191, +0.280], entirely below the bar.

**Hold-out (2025) NOT touched**, per the locked rule. It stays sealed.

**No per-tick rebuild.** The scanner keeps per-candle evaluation and the
`next_open` fill. The measured ceiling does not pay for moving the Fargate loop
to tick-level evaluation plus resting stop-order management, and the ceiling is
optimistic by construction: it fills at the level with **zero slippage**, while
a real buy-stop becomes a market order and fills at the level or worse. Any
realistic slippage — plausibly 5–15 bps in a stock moving 4–8% on 3× volume —
consumes a large fraction of +0.235 pp.

**`fill_mode` stays in the code** as the two-line switch it is, defaulting to
`next_open`. It cost nothing to keep and it documents the measurement.

**Do not relitigate the bar.** +0.30 pp was set in §4 before any code was
written, with a stated rationale, and the result came in below it with a tight
interval. Lowering it now, or keeping only the two setups that individually
cleared it, is exactly the fitting-to-result failure this file exists to
prevent.

**What a legitimate follow-up would have to look like.** Not a retune of this
test. A different question — for instance whether a resting *limit* order
placed below the trigger beats both arms, which is the opposite bet (it trades
fill probability for price and would capture the 19.3% pullback case). That is a
new hypothesis file with its own criterion, and it inherits the same ceiling
problem: nothing on the execution layer can close a ~1.0% cost gap.

**Standing conclusion for `momentum-catalyst-upstox-v2` is unchanged.** Entries
have been measured twice (BT17 pool, this file), exits once
(`trend-exit-vs-fixed-target`), and execution once (this file). The catalyst
gate remains the only untested component.

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

**Effect on this file:** both arms carried the leak. The paired uplift (+0.235 pp,
t = 10.40) is a difference between two arms with identical exit logic, so it
stands. The absolute `next_open` figure (−0.081% on 2024) is the leaked number,
not the spec'd strategy. Nothing here was re-run; the verdict does not depend on
the absolute level.
