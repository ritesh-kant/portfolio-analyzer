---
slug: session-resistance-target
strategy: momentum_trader (warrior_strict)
status: final
registered_at: '2026-09-25'
finalized_at: '2026-09-25'
decided_at: '2026-09-25'
hypothesis_hash: ''
---

# Hypothesis: a target set just below prior-session resistance fills more often without costing more than it gains (BT50)

Registered 2026-09-25, before any code ran, after the GODREJIND 2026-09-25
paper trade. The operator decided to adopt the rule as the `warrior_strict`
default whatever the result ("no arm, just build it"). This test decides what
may be *claimed* about it, and whether it should be reverted.

## 1. Mechanism

The target cap from BT47 only knows today's bars, yesterday's high/low/close,
the opening range and the round-rupee grid. It is blind to older supply.

GODREJIND 2026-09-25: entry ₹1,133.1, 2R target ₹1,147.9, nearest known
resistance the round ₹1,150, so no cap. The Sep 17 session high was ₹1,147.0,
and 16% of Sep 16–18 volume traded between ₹1,140 and ₹1,150. The day high was
₹1,146.0, one rupee under the old high, and the trade was scratched.

Two separate claims:

1. Prior-session highs are where sellers who are still holding from those days
   exit, so price stalls there.
2. Traders who know a level sell *in front of* it, so a target placed exactly
   at the level is the last order in the queue and often misses.

## 2. Frozen rule

All parameters were chosen from the chart and the idea, not from data.

- **Lookback**: the 10 sessions before today (`SESSION_LEVEL_SESSIONS = 10`).
- **Levels added**, highs only:
  - each prior session's high, kind `session_high`, structural like
    `prev_day`;
  - swing pivot highs on the prior sessions' 5-minute bars (the existing
    k=3 detector), clustered at the existing 0.30%, kind `session_pivot`.
    Structural only with two or more touches (the existing rule for pivots).
- **Where they are used**: the fixed-target cap only. Entries, the entry
  headroom test, the support-break exit and the resistance-reject exit keep
  exactly the level set they have today. Entry sets must therefore be
  identical between arms, which makes the comparison paired.
- **Buffer**: every structural resistance that caps the target, old kinds
  included, sets the target at `level × (1 − 0.15%)`, floored to the ₹0.05
  tick (`TARGET_BUFFER_PCT = 0.15`). A level whose buffered price is not above
  the fill is treated as already reached and skipped; the next one up is used.
- The target is `min(2R, buffered nearest resistance)`, as before.

The control is today's `warrior_strict`: session levels off, buffer 0. With
both off the new code must reproduce BT47's structural arm trade for trade.

## 3. Data

Same window and command shape as BT47: `warrior_strict`, `future_trigger`
fills, stress slippage, cached-year 2025 from 2025-09-22 and cached-year 2026
to 2026-09-18. This window was spent by BT47, so a positive result cannot
bless the rule, only a negative one can kill it.

## 4. Falsification criterion (LOCKED)

Computed on same-entry pairs, experiment minus control, net INR per trade.

PASS requires all five:

1. Paired mean delta > 0.
2. Two-sided paired t-test p < 0.05.
3. Median delta over the changed trades ≥ 0.
4. With the five largest positive deltas removed, the summed delta is still > 0.
5. The mean delta has the same sign in 2025 (Sep–Dec) and in 2026 (Jan–Sep).

Any failure = **no improvement established**. If criteria 1 and 2 hold with
the sign reversed (delta < 0 and p < 0.05), the recommendation is to revert
the default.

Also reported, not gating: target-hit count per arm, how many caps each level
kind produced, and the capital deployed per arm (sizing is unchanged, so this
should be 1.00×).

## 5. Result (2026-09-25)

Window 2025-09-22 → 2026-09-22 on cached data, 178 trades. The control
reproduces all 175 BT47 structural-arm trades exactly (same exit, target and
net on every one). The 3 extra trades are data only: two from 2026-09-22 and
COROMANDEL 2026-08-26, whose cache file was created on 2026-09-23, after BT47.

| Arm | Trades | Gross INR | Net INR | Net / trade | Target exits |
|---|---:|---:|---:|---:|---:|
| Control (BT47 default) | 178 | +4,222 | −82,408 | −463.0 | 48 |
| Session levels + 0.15% buffer | 178 | +4,996 | −81,639 | −458.6 | 74 |

Entries identical (178 shared, 0 arm-only), capital ratio 1.000.

| Criterion | Value | Result |
|---|---:|---|
| 1 Mean paired delta > 0 | +₹4.32 / trade | PASS |
| 2 p < 0.05 | p = 0.66, 95% CI [−₹14.88, +₹23.53] | FAIL |
| 3 Median changed delta ≥ 0 | −₹63.14 (31 better, 45 worse of 76) | FAIL |
| 4 Sum without top 5 gains > 0 | −₹1,328 | FAIL |
| 5 Same sign both years | 2025 +₹7.59, 2026 +₹3.12 | PASS |

**Verdict: no improvement established.** Not significantly worse either, so
the pre-registered revert condition is not met and the operator's default
stands.

### Why: the nearest level is too close

Adding ten sessions of highs makes the level set dense, and the rule takes the
nearest one. The median target fell from **1.78R to 1.08R**; targets under
0.5R went from 17% to **32%** of trades. Those near targets lose (57 trades
under 0.5R: −₹847 summed delta), the 0.5–1R band gains (+₹1,410). More target
fills (48 → 74) at smaller size is the BT43 pattern again: a higher win rate
on a zero-drift signal, net unchanged.

⚠️ **The motivating trade is not fixed by this rule.** On real candles the
10-session set for GODREJIND 2026-09-25 contains a structural level every
₹1–3 between ₹1,129 and ₹1,147 (the stock chopped in that range for two
weeks). Nearest above the ₹1,133.1 fill is the 2026-09-10 high ₹1,135.40, so
the target would have been **₹1,133.65, ₹0.55 above entry**, not ₹1,145.25
under the ₹1,147 high the operator drew. The unit test for GODREJIND uses a
synthetic history containing only the Sep 16/17 highs.

A rule that picks the *strongest* level, or skips levels closer than some
multiple of risk, would be a new hypothesis chosen after seeing this result.
It must be registered separately, with its parameter fixed a priori; on this
window it can kill, not bless.

Tools: `bt50_session_target_report.py` (criteria),
`bt50_changed_trades_report.py` (BT32-format charts of the 76 changed
trades), `bt50_changed_trades.csv`.
