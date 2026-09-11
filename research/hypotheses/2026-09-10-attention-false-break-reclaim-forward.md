# Attention false-break reclaim

**Registered:** 2026-09-10, after the SWANCORP paper-trade review.  
**Status:** separate forward paper arm; the observed SWANCORP session is
diagnostic and excluded from the evaluation sample.

## Claim

Some attention breakouts briefly lose their breakout level, then reclaim it on
renewed demand.  One volume-backed reclaim may be a distinct opportunity, but
must not loosen the initial-entry or exit rules of the existing attention arm.

## Fixed implementation

- Starts with the unchanged `attention_1m` entry and `trend_full` exit rules.
- Eligible only after that original attention entry exits specifically as
  `false_break`; hard-stop, trail, EMA, target, and EOD exits never qualify.
- One later completed one-minute bar must remain in the five-minute EMA9 >
  EMA20 / above-VWAP context, be green, close in its upper 40%, trade at least
  2.5x its trailing 20-bar one-minute volume, and close above the exact failed
  breakout level.
- The retry is a future-only buy-stop at the reclaim candle high, expires after
  three minutes, and receives a new stop from the last three one-minute lows.
- At most one reclaim order may be armed per symbol per session.  The retry can
  never trigger a third attempt.
- It uses the same risk sizing, chase cap, trend exits, costs, and paper-only
  restriction as the attention control.  It writes to a separate CSV log and
  every Mongo record carries its strategy name.

## Diagnostic replay — excluded from evaluation

**SWANCORP, 2026-09-10:** its initial ₹299.55 entry exited at ₹298.70 through
the ₹299.50 breakout level.  The later 12:15 reclaim was a valid confirmation
(2.66x one-minute volume): a ₹301.00 future buy-stop, ₹297.60 structure stop,
147-share position, and ₹307.80 theoretical 2R target.  The unchanged
`trend_full` exit closed it at ₹300.00 on an EMA9 break at 12:19, producing
₹−147.00 gross, ₹90.98 costs, and **₹−237.98 net**.  The later ₹315.35 high is
not credited to this strategy because it had already exited.

## Review gate

Do not change its trigger, stop, retry count, or exits while accumulating.  At
30 closed reclaim trades and 20 sessions, compare incremental net return, exit
mix, drawdown, and retry conversion against the separate attention control.
It remains paper-only unless its incremental actual-cost net return is positive
without a worse maximum daily drawdown.
