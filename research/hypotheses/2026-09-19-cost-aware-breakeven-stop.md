---
slug: cost-aware-breakeven-stop
strategy: momentum_trader
status: registered
registered_at: '2026-09-19'
finalized_at: ''
decided_at: ''
hypothesis_hash: ''
---

# Hypothesis: moving the 1R "breakeven" stop up to the true after-fees breakeven turns the breakeven-scratch exits from small losses into zero, and that gain is bigger than the winners it cuts short

Written in plain language on purpose. Registered **before** any run with the
fixed code. The only earlier full-year run of this rule (`coststop23_*`,
2026-09-19 13:51) used the **buggy** version, which put the stop at roughly
+1.0% of entry (the research stress slip was folded into the fee maths). That
run measured a different rule and is discarded here.

## 0. What the rule is, in one paragraph

Today the trend exits already do this: once a trade is up by one "R" (one
unit of the initial risk, i.e. the gap between the buy price and the hard
stop), the stop is lifted to the buy price. That is called the breakeven
lock. But a stop at the buy price is **not** breakeven: the round trip still
costs about 0.21% in brokerage, taxes and slippage, so every trade that gets
lifted and then stopped at entry books a small loss. The cost-aware stop
lifts the stop a little higher instead, to the first tick where the sale
covers all real fees, plus one extra tick of buffer. On a typical ₹392 stock
that is ₹393.40 instead of ₹392.50, i.e. +0.23% instead of 0%. Nothing else
changes: same entries, same hard stop, same trail, same trend exits, same
1R arming point.

| Entry | Qty | Stop today | Cost-aware stop | Lift |
|---:|---:|---:|---:|---:|
| ₹100.00 | 400 | ₹100.00 | ₹100.30 | +0.30% |
| ₹392.50 | 119 | ₹392.50 | ₹393.40 | +0.23% |
| ₹641.80 | 77 | ₹641.80 | ₹643.20 | +0.22% |
| ₹1,500.00 | 30 | ₹1,500.00 | ₹1,503.15 | +0.21% |

Zero new tunables: the price is derived from the real fee model
(`calc_costs`) and a fixed one-tick buffer that already existed.

## 1. Mechanism

Two forces, and the test is which one is bigger:

- **Saved money.** Every trade that reaches 1R and then falls all the way
  back to the entry stop currently loses about 0.21% (the fees). With the
  cost-aware stop it exits ~0.23% higher and nets roughly zero. Gain ≈ +₹90
  per such trade on a ₹45k position.
- **Lost money.** Any trade that reaches 1R, dips into the 0–0.23% band above
  entry, and then goes on to win is now cut early. The cost-aware stop gives
  back that trade's later profit.

The rule wins if breakeven scratches are common and "dip into the band then
rally" is rare. On a zero-drift signal (BT17: gross ≈ 0.00%/trade) there is
no reason to expect the dip-then-rally path to be rare, so the prior is
weak: this is a bookkeeping improvement, not an edge. It cannot make the
strategy viable (base is about −0.30%/trade at real costs); the most it can
do is shrink the loss.

## 2. Expected effect size

- **Direction**: cost arm nets more per trade than the base arm.
- **Magnitude**: small. Roughly 10–20% of trades exit on the breakeven lock;
  if all of them gained ~₹90 and none were cut short, that caps the gain near
  +₹10–₹18 per trade (+0.02 to +0.04 pp). The realistic number is lower.
- **Units**: ₹ per trade at **real** costs (0.21% round trip), paired on
  identical entries. Also reported at the 0.80% stress for continuity.

## 3. Falsification criterion (LOCKED before experimentation)

Arms: `base` (rule off) and `cost` (rule on), attention arm, `bt17
--attention --cached-year <Y>`, full calendar years 2023 and 2024. The entry
side is untouched by the rule, so both arms open the same trades; the test is
paired on shared `(date, symbol, entry_time)`.

- **C1 profit**: pooled (2023+2024) paired mean net delta at real costs
  (cost − base) **> 0 with paired t-test p < 0.05**. Otherwise KILL.
- **C2 size**: pooled paired delta **≥ +₹10 per trade** at real costs.
  Otherwise KILL (a smaller number is not worth carrying as live behaviour
  that differs from the backtests).
- **C3 consistency**: delta **> 0 in 2023 AND in 2024** separately. Otherwise
  KILL.
- **C4 sample**: ≥ 500 shared trades in each year, otherwise the test is
  void, not a pass.

Pass = all of C1–C3 with C4 satisfied. Because both windows are already
spent for other momentum work, **a pass here is "not killed", not "ship"**
(see `feedback_dirty_window_can_kill`): it earns the default-on setting for
the live scanner only alongside a forward check on the live log, and it does
not change the strategy's viability verdict.

**No anti-test is planned** and that is stated openly: the rule has no free
parameter to randomise, and the comparison is paired on identical entries.
If C1–C3 pass, one pre-declared secondary is allowed: a dose check
(`cost_stop_extra_ticks` = 5, i.e. a stop a few ticks higher still). If the
higher stop is *even better*, the gain is from stop tightness, not from
covering fees, and the rule is a tunable in disguise → treat as KILL for the
"cost-aware" claim.

Pre-declared descriptives (no verdict weight): number of trades whose exit
changed; split of the delta into "saved scratches" (base exit was a
`trail_stop` at exactly the entry price) vs "cut winners" (cost arm exit is a
`trail_stop` at the cost stop while base went on to earn more); exit-reason
counts per arm.

## 4. Data needed

- Upstox 1-minute cache already on disk (`research/backtests/.cache_upstox/1m/`),
  2023 and 2024, attention arm universe (about 250 and 210 symbols).
- No new feeds. Fee model = `src/news_trader/trailing_sl.calc_costs`.

## 5. Train / dev / hold-out split

- Windows used: 2023 and 2024 (both already touched for entry-side and
  exit-side work; nothing here is fresh).
- 2025 remains sealed and is **not** touched by this test.
- Forward: live paper log after enabling, if it is enabled.

## 6. Code reference

- Rule: `apps/signal-engine/src/momentum_trader/engine.py`
  (`EngineConfig.cost_aware_breakeven`, `_cost_aware_breakeven_stop`, the
  arm-then-bind-next-bar logic in the bar loop).
- Runner flag: `bt17 --cost-aware-breakeven`.
- Analysis: `research/backtests/bt43_cost_aware_stop.py` (pairs the arms and
  prints C1–C4).
- Tests: `tests/momentum_trader/test_engine.py` (3 cost-stop tests).

## 7. Result (BT43, run 2026-09-19)

Both arms opened identical trades in both years, so every figure below is
paired trade-for-trade. Runs: `bt17 --attention --cached-year {2023,2024}`
with and without `--cost-aware-breakeven`, tags `cs23/cs24_{base,cost}`.

### Profit: a dead null

| Window | Shared trades | Base real net/trade | Cost real net/trade | Delta | p |
|---|---:|---:|---:|---:|---:|
| 2023 | 1,891 | −₹103.9 | −₹104.5 | **−₹0.59** | 0.542 |
| 2024 | 1,248 | −₹139.4 | −₹138.7 | **+₹0.67** | 0.422 |
| Pooled | 3,139 | — | — | **−₹0.09** | 0.893 |

95% CI on the pooled delta is [−₹1.41, +₹1.23] against a −₹103 to −₹139 base.
Total effect across 3,139 trades: **−₹285**. The two years disagree in sign and
both are indistinguishable from zero.

### Accuracy: a real +3 pp, same as the false-break result

| Window | Base win rate | Cost win rate |
|---|---:|---:|
| 2023 | 24.75% | **27.92%** |
| 2024 | 21.47% | **24.52%** |

### Why it cancels — the mechanism is visible and works exactly as designed

2023, of the 124 trades whose exit changed:

| | Trades | Total |
|---|---:|---:|
| Base exited at the entry price (the scratch the rule targets) | 35 | **+₹3,865** |
| Cost arm did better | 76 | +₹5,647 (mean +₹74) |
| Cost arm did worse — all of them cut by the new stop | 41 | **−₹6,770** (mean −₹165) |

The rule really does convert scratch losses into zeros, and it really does
collect about ₹74 each time. But the trades it cuts short lose about ₹165 each,
because a trade that dips into the 0–0.23% band above entry and then recovers
is exactly the kind that was going to run. Saves and cuts cancel almost
perfectly. `trail_stop` exits rise 403→492 (2023) and 286→322 (2024), drawn
from `ema9_break`, `volume_climax`, `macd_fade` and `resistance_reject`.

### Verdict

| Criterion | Result |
|---|---|
| C1 pooled delta > 0, p < 0.05 | **FAIL** (−₹0.09, p=0.893) |
| C2 pooled delta ≥ +₹10/trade | **FAIL** |
| C3 delta > 0 in both years | **FAIL** (−0.59 / +0.67) |
| C4 ≥ 500 shared trades per year | PASS |

**KILL.** The dose secondary was not run: it is only permitted after C1–C3
pass.

⭐ **The general lesson, now seen twice in two days.** This is the second rule
(after the two-close false break) that raises the gross win rate by 2–3 pp and
moves profit by nothing. On a signal with no drift, any rule that converts
small losses into zeros converts an equal amount of small winners into zeros
too. Win rate is the wrong scoreboard here, and a rule that improves it should
be assumed P&L-neutral until a paired test says otherwise.

⚠ Do not re-test on 2023 or 2024; both are now spent for this question too.
The earlier `coststop23_*` run (2026-09-19 13:51) used the pre-fix stop at
about +1% of entry and is superseded — it is a different rule, and its −₹32.51
per trade should not be quoted against this one.

## 8. Decision

- [ ] **NOT KILLED** — C1–C3 pass; enable in live scanner + forward check
- [x] **KILL** — C1, C2 and C3 all failed. `cost_aware_breakeven` stays
      `False`, is set nowhere, and remains a backtest-only flag. No second
      look, no threshold change, no variant on this data.
