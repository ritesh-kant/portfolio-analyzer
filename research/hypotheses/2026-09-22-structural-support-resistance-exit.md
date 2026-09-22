# Structural support exit and resistance-capped target (BT47)

Run 2026-09-22 at the operator's request after reviewing the RHIM intraday
chart. This is a post-hoc historical comparison, not a pre-registered
experiment. The operator nevertheless selected it for the paper-trading
configuration after reviewing the result.

## Question

The existing RHIM plan used a fixed two-risk target. For an entry at ₹392.55
with a ₹389.55 stop, the calculation is:

```text
target = entry + 2 × (entry - stop) = 392.55 + 2 × 3.00 = ₹398.55
```

That calculation did not consult the chart's prior resistance near ₹397.85.
The operator also asked to replace the two-close false-break exit with a sell
after local support breaks, while retaining the existing hard stop.

## Frozen experimental arm

The structural arm changes two position-management decisions, both from
completed information available when the trade fills:

1. It freezes the nearest structural support below entry. A completed
   one-minute close below that support exits at the close. The hard stop and
   target remain executable first on every minute; no support is recalculated
   after entry.
2. It freezes the nearest structural resistance above entry and sets the
   active target to `min(2R target, structural resistance)`. If no qualifying
   resistance lies between entry and 2R, 2R remains unchanged.

Structural levels reuse the engine's existing causal anchors and confirmed
pivots. They are not the hand-drawn TradingView lines; each closed trade
records the chosen level and its source. A support at or below the hard stop
cannot improve protection, so it does not create a `support_break` exit.

The arm replaces—not supplements—the two-close false-break rule. Consequently
the false-break reclaim re-entry path is disabled. Stops, fill model,
transaction-cost stress, sizing, trend exits, and entry rules are unchanged.

## Data and commands

The requested trailing window was 2025-09-22 through 2026-09-22. The cached
2026 intraday data ended at 2026-09-18, so the actual replay is
**2025-09-22 through 2026-09-18** (362 calendar days). It uses the deployed
`warrior_strict` configuration and its live-safe `future_trigger` fill model.
No inference is made about the missing 2026-09-19 and 2026-09-22 sessions.

```bash
uv run research/backtests/bt17_momentum_pool.py \
  --cached-year 2025 --start 2025-09-22 --end 2025-12-31 \
  --warrior-strict --jobs 8 --tag struct25_control

uv run research/backtests/bt17_momentum_pool.py \
  --cached-year 2025 --start 2025-09-22 --end 2025-12-31 \
  --warrior-strict --jobs 8 --tag struct25_structural

uv run research/backtests/bt17_momentum_pool.py \
  --cached-year 2026 --start 2026-01-01 --end 2026-09-22 \
  --warrior-strict --jobs 8 --tag struct26_control

uv run research/backtests/bt17_momentum_pool.py \
  --cached-year 2026 --start 2026-01-01 --end 2026-09-22 \
  --warrior-strict --jobs 8 --tag struct26_structural

uv run research/backtests/bt47_structural_exit_report.py \
  --control research/backtests/bt17_trades_struct25_control.csv \
            research/backtests/bt17_trades_struct26_control.csv \
  --experiment research/backtests/bt17_trades_struct25_structural.csv \
               research/backtests/bt17_trades_struct26_structural.csv
```

## Results

| Arm | Trades | Gross INR | Costs INR | Net INR | Net INR / trade | Gross win rate |
|---|---:|---:|---:|---:|---:|---:|
| Current two-close / fixed 2R | 175 | +4,683 | 85,143 | −80,460 | −459.8 | 47.43% |
| Structural support / resistance cap | 175 | +4,195 | 85,140 | −80,946 | −462.5 | 53.14% |

The entry sets were identical: 175 shared entries and zero arm-only entries.
That is important: disabling reclaim did not affect this particular sample, so
the paired result is a direct exit comparison.

| Same-entry measure: structural minus current | Result |
|---|---:|
| Changed exits | 62 / 175 |
| Net INR | −485 |
| Net INR per trade | −2.77 |
| 95% confidence interval | [−₹22.81, +₹17.26] |
| Paired t-statistic / two-sided p-value | −0.271 / 0.7864 |

Only 46 of 175 entries (26.29%) had a frozen structural support above the
hard stop, and the arm produced 11 `support_break` exits. The target was
capped by structural resistance on 99 trades: 84 round levels, 8 prior-day
anchors, and 7 confirmed pivot highs. The structural arm hit its target 48
times versus 19 in the control, but that higher win rate did not improve
net expectancy.

## Decision

The observed result is small, negative, and statistically indistinguishable
from zero, so it does **not** establish a performance improvement. The
operator selected the structural rule anyway because its target and exit are
chart-consistent: it caps a 2R target at entry-known resistance—such as RHIM's
₹397.85 level when the causal level engine recognizes it—and exits after
frozen support breaks without weakening the hard stop.

It is now the default momentum position-management behavior and is enabled in
the local paper-trading configuration through `MT_STRATEGY=warrior_strict`.
The two-close false-break reclaim strategy and the separate backtest switch
were removed from active configuration.
