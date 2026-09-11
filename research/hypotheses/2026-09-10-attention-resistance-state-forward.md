# Attention confirmation with resistance state

**Registered:** 2026-09-10, after the KEC paper trade.  
**Status:** forward paper test; the KEC session is diagnostic only and excluded
from the evaluation sample.

## Claim

An attention-confirmation entry should not buy directly into a meaningful
ceiling with less than one initial-risk unit of room.  A volume-backed,
completed one-minute close through that ceiling is a separate breakout event.
After the breakout, the broken level must not cause a resistance exit unless
price tests it and closes back below it on a bearish candle.

## Fixed implementation

- **Control:** `attention_1m`, unchanged.
- **Treatment:** `attention_1m_resistance_state`.
- A structural resistance is a prior-day, opening-range, or round-number
  anchor, or a confirmed pivot cluster with at least two touches.  All levels
  are derived from bars closed before the confirmation minute.
- A normal attention confirmation with less than 1R of headroom to the nearest
  structural ceiling is recorded as `attention_wait_resistance_break`, not
  entered.
- A normal high-volume confirmation that closes above the nearest structural
  ceiling becomes a breakout confirmation.  Its pending buy-stop remains at
  that candle's high, subject to the existing three-minute expiry and 1% chase
  cap.  The crossed ceiling becomes its false-break level.
- `resistance_reject` applies only to structural levels and requires a trade at
  or above the level followed by a bearish close back below it.  EMA, MACD,
  volume-climax, hard-stop, false-break, and EOD exits are otherwise unchanged.

## Locked review

Do not revise thresholds, level definitions, fills, or exits during this run.
After at least 30 closed treatment trades and 20 sessions, compare treatment
with the unchanged control on actual-cost net return/trade, total net INR,
maximum daily drawdown, trade count, exit-reason mix, and the share of
resistance exits followed by a +1R favourable move within 15 minutes.

The treatment is not promoted beyond paper trading unless it has positive
actual-cost net return and does not merely achieve it by eliminating nearly all
trades.

## Diagnostic trades — excluded from evaluation

- **KEC, 2026-09-10.** The control entered ₹407.10 and closed at ₹407.70 via
  `resistance_reject` around a one-touch ₹407.65 intraday pivot; costs turned
  the ₹73.20 gross profit into a ₹29.19 net loss.  The resistance-state replay
  refused the original 11:36 signal, recognized a later high-volume reclaim of
  ₹406.71, entered ₹407.30 at 11:40, and exited ₹410.50 on `ema9_break` at
  11:57: gross ₹390.40, net ₹287.59.  This is an illustrative replay only.
- **SWANCORP, 2026-09-10.** The control entered ₹299.55 and closed ₹298.70 via
  `false_break` after losing the actual ₹299.50 breakout level; net ₹−96.76.
  It later reclaimed the level at 12:15 and reached ₹315.35 (above the displayed
  ₹314.55 target).  This is a distinct reclaim/re-entry question.  It is not
  changed by this treatment and must not be added without a separately
  registered paper arm.

## Operational status

Deployed for future **paper-only** scans in AWS `ap-south-1` on 2026-09-10:
ECS task definition `mt-scanner-prod:9`, image tag
`resistance-state-20260910`, strategy `attention_1m_resistance_state`, and log
path `/data/mt_attention_1m_resistance_state_forward_log.csv`.  The old
`attention_1m` strategy remains the control and its log remains separate.
