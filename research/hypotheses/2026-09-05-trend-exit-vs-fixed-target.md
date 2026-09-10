---
slug: trend-exit-vs-fixed-target
strategy: momentum_trader
status: killed
registered_at: '2026-09-05'
finalized_at: '2026-09-05'
decided_at: '2026-09-05'
hypothesis_hash: ''
parent: momentum-catalyst-upstox-v2
---

# Hypothesis: On the identical entries BT17 already measured, replacing the fixed 2:1 profit target with indicator-driven exits (ratcheting swing-low stop, close below the 9/20 average, MACD momentum roll-over, rejection at a derived resistance level, heavy-volume down bar) raises mean gross return per trade by at least +0.30 percentage points, because it stops capping the winners that a fixed target truncates.

> ⚠️ **Why this is a separate file.** BT17 ran on 2026-09-05 and reported gross
> 0.00%/trade with only 25% of trades reaching the fixed target. The operator
> then asked for exits driven by indicators rather than a preset price. That
> request is a reaction to an observed result, so folding it into
> `momentum-catalyst-upstox-v2` would be changing a locked rule after seeing the
> data — the exact failure mode listed in the validation rules. It is therefore
> registered separately, with its own dev/hold-out split, and the v2 gates are
> untouched.
>
> ⚠️ **The entries are not re-optimised.** All three modes replay the same
> `scan_setups` output on the same bars. Only the exit differs. Any difference
> in results is attributable to the exit and nothing else.

## 0. What is being compared (frozen before the run)

Exactly three modes, no more. `apps/signal-engine/src/momentum_trader/exits.py`.

| Mode | Exits available |
|---|---|
| `fixed_2r` (baseline) | hard stop, target at 2× risk, false break, 15:15 close |
| `trend_min` | hard stop, **ratcheting swing-low stop**, breakeven lock at 1R, **close below EMA9**, **close below EMA20**, false break, 15:15 close. **No target.** |
| `trend_full` | `trend_min` **plus** MACD histogram roll-over, rejection at derived resistance, heavy-volume down bar |

Frozen parameters, all conventional defaults, none fitted:

```
arm trend rules at   0.5 R of open profit
breakeven lock at    1.0 R
swing pivot window   3 bars either side
swing trail buffer   0.10 % below the confirmed pivot low
EMA                  9 and 20
MACD                 12 / 26 / 9
resistance band      0.35 %
climax volume        2.5 x the bar's 20-bar average
indicator warm-up    3 prior sessions (exits only; entries unchanged)
```

**Timing rules that keep it honest:** stops and the fixed target are checked on
every 1-minute bar, because an order in the market fills whenever price reaches
it. Trend signals are checked only when a bar closes on the position's own
timeframe, because an indicator is read off a finished candle. Trend rules are
armed only after 0.5R of open profit, otherwise noise on the entry bar closes
every trade instantly. The trail is raised *after* the exit decision on a bar,
so a stop can never bind on the same bar that created it.

## 1. Mechanism

A fixed target is a bet that you know in advance how far the move goes. BT17
says that bet is wrong in a specific way: the average winner that reached the
target made +1.61%, but three quarters of trades never got there, and the ones
that did were cut at exactly 2R regardless of how much further the move ran. A
trend exit removes the cap. It should raise the average winner without changing
the average loser, because the hard stop is unchanged.

The offsetting cost, and the reason this might fail: a trailing exit gives back
part of an open gain on every trade that reverses, and the breakeven lock
converts some trades that would have reached the target into scratches. Whether
the bigger tail outweighs the extra give-back is the empirical question.

## 2. Expected effect size

- Mean gross return per trade, `trend_min` minus `fixed_2r`: **+0.30 to +0.80**
  percentage points, entirely from the right tail.
- Mean holding time roughly 2 to 4× longer.
- Win rate expected to **fall** by 5 to 12 points. That is normal for trend
  following and is not by itself a failure; expectancy is the metric.
- `trend_full` is expected to sit between the two: its extra rules exit earlier,
  which trims both tails.

## 3. Falsification criterion (LOCKED before the dev run)

Dev window 2024 only. Primary comparison on identical entries.

- **PRIMARY:** mean gross %/trade of the best trend mode minus `fixed_2r`
  ≥ **+0.30** percentage points. Below that, indicator exits are not the answer
  to BT17's flat pool and this line of work stops.
- **Secondary, reported not gating:** expectancy in ₹ per trade, stressed net
  %/trade, win rate, mean and median holding time, per-exit-reason contribution,
  the 90th-percentile gross return, and the same table per setup.
- **Cost sanity:** longer holds do not add cost in this model (intraday MIS is
  charged per round trip, not per minute), so any gross improvement carries
  through to net one-for-one.

**Hold-out (2025), touched once, only if the dev primary passes:** the same
gross improvement must be ≥ **+0.15** points, correctly signed. Otherwise KILL.

🔒 **No-relax rules:**
- Three modes only. If a fourth idea appears after seeing these numbers, it is a
  new hypothesis file, not a tweak here.
- Parameters above are frozen. Notably `breakeven_at_r = 1.0` is suspected in
  the smoke test of converting winners into scratches; it is **not** to be
  retuned inside this hypothesis.
- A per-setup or per-exit-reason result that looks good is an observation, not a
  filter to add.
- Passing this does **not** license live money. It only decides which exit the
  `momentum-catalyst-upstox-v2` forward capture should run.

## 4. Data

Same Upstox 1-minute cache BT17 built, `research/backtests/.cache_upstox/1m/`.
No new fetches, so the comparison is free and repeatable. Costs: production
intraday MIS plus the 40 bps/side stress, identical across modes.

## 5. Split

- **Dev:** 2024-01-01 → 2024-12-31. All three modes.
- **Hold-out:** 2025-01-01 → 2025-12-31. Never read until dev decides.
- Prior evidence, not part of this split: BT17 over 2024+2025 combined under
  `fixed_2r` gave 1,876 trades, gross 0.00%, stressed net −1.00%.

## 6. Code

- `exits.py` — the three modes, the state machine, the frozen parameters.
- `levels.py` — swing pivots, clustering into support/resistance, plus prior-day,
  opening-range and round-number anchors.
- `indicators.py` — MACD, ATR and bar-level volume ratio added for this test.
- `engine.py` — `EngineConfig.exit_mode` selects the mode; the fixed path is
  byte-identical to BT17, verified by the existing engine tests still passing.
- Runner: `bt17_momentum_pool.py --exit-mode {fixed_2r,trend_min,trend_full}`.
- Tests: `tests/momentum_trader/test_exits.py`, 20 cases.

## 7. Result — 2024 dev, run 2026-09-05

992 trades, **identical entries in all three modes** (verified: same n, same
entry timestamps). Only the exit differs.

| mode | gross %/trade | stressed net %/trade | win % | median hold | mean win | mean loss | p90 gross | max gross |
|---|---|---|---|---|---|---|---|---|
| `fixed_2r` | −0.081 | −1.086 | 13.5 | 8 min | +1.53 | −0.49 | 1.30 | 5.92 |
| `trend_min` | −0.077 | −1.082 | 8.8 | 8 min | +1.13 | −0.45 | 0.79 | 11.59 |
| `trend_full` | −0.064 | −1.070 | 7.6 | 6 min | +0.80 | −0.47 | 0.78 | 11.59 |

**PRIMARY criterion (≥ +0.30 pp gross uplift): FAILED.**

| comparison | uplift | verdict |
|---|---|---|
| `trend_min` − `fixed_2r` | **+0.004 pp** | FAIL |
| `trend_full` − `fixed_2r` | **+0.017 pp** | FAIL |

Paired per-trade test (same entry, `trend_min` − `fixed_2r`): mean difference
+0.004 pp, median 0.000, t-statistic **0.18**. 62% of trades were byte-identical
(they exited on the unchanged hard stop or false break), 23% improved, 14% got
worse. The difference is indistinguishable from noise.

**The rules did work mechanically.** Exits on a 9-average break: 300 trades,
+0.635% gross, 37-minute average hold against an 8-minute median overall — the
rule genuinely holds winners longer. The extreme right tail nearly doubled, from
a best trade of +5.92% to +11.59%. MACD roll-over (+0.99% on 58) and volume
climax (+1.28% on 65, the only exit reason with positive total P&L) were the two
best-performing exits in `trend_full`.

**And it still made no difference,** because the gain at the extreme was paid for
exactly in the middle of the distribution. The 90th-percentile trade fell from
+1.30% to +0.79% and the mean winner fell from +1.53% to +1.13%: the ratchet and
the breakeven lock convert would-be 2R winners into scratches at roughly the rate
the tail improves. `trend_full` trades this even harder — its resistance rule
fired 183 times at +0.31% and an 11-minute hold, cutting trades early.

**Interpretation: the exit is not the binding constraint.** Two structurally
opposite exit philosophies extract the same ~0.00% from the same entries. That is
what a pool with no drift looks like: there is nothing in these entries for any
exit to harvest. Consistent with BT9, BT14, BT15 and BT17.

## 8. Decision

- [x] **KILL** — the pre-registered +0.30 pp primary failed on both trend modes
      (+0.004 and +0.017 pp, t = 0.18).

**Hold-out (2025) NOT touched**, per the locked rule. It remains sealed for any
future hypothesis on this data.

**Consequence for the parent hypothesis:** `momentum-catalyst-upstox-v2` keeps
`fixed_2r` for forward capture. The two exits perform identically, and the fixed
target is simpler, has fewer parameters, and is the one BT17 already characterised.
Nothing about the parent's catalyst gates changes; they remain untested.

**Do not retune and re-run.** The suspected breakeven-at-1R problem was named in
§3 before the run and is confirmed by the mean-winner drop. Retuning it here
would be fitting to this result. If it is ever worth testing, it is a new
hypothesis file with its own criterion, and it must justify why an exit change
should matter when two opposite exits already came out equal.

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

**Effect on this file:** the `fixed_2r` baseline in §7 carried the leak, so this
was `trend_min`-with-lock vs `fixed_2r`-with-lock. The verdict stands (both arms
shared it; the paired difference was +0.004 pp). **One attribution in §7 is
corrected:** the drop in mean winner (1.53 → 1.13) and p90 was attributed partly
to "the breakeven lock converting 2R winners into scratches". Both arms had the
lock, so that drop is attributable to the swing-low ratchet and EMA9/EMA20 exits
alone. The conclusion — two opposite exits extract the same ~0 — is unchanged.
