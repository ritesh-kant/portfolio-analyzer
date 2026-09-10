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
