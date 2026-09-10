# Catalyst-first pullback, forward paper test

**Registered:** 2026-09-07, before implementation and before the first live
session of this test.
**Status:** registered.
**Start:** first NSE session on or after 2026-09-08.

## Claim

Among the existing liquid NSE momentum universe, a hard corporate catalyst
followed by the **first MA9 pullback** has better intraday continuation than the
same technical pattern without a catalyst. The exit has no fixed profit target;
it closes only on a hard stop, pattern failure, a confirmed negative trend
signal, or end of day.

This is deliberately a new, simple playbook. It does not combine chart-quality,
MACD-entry, round-number, support, or resistance filters: those conditions have
already been tested without a point-in-time catalyst and did not select profit.

## Frozen paper protocol

* **Data:** live Upstox FULL feed; one-minute bars built from actual ticks.
* **Catalyst:** a hard `nt_signals` event (`earnings`, `order_win`, `regulatory`,
  `m_and_a`, or `capital_action`) detected and stored no more than 24 hours
  before the trigger. The stored timestamp must precede the trigger.
* **Universe:** existing price, turnover, circuit-band, day-change (+4% to +8%)
  and time-of-day RVOL (>=3x) rules.
* **Entry:** `ma9_pullback` only, first confirmed pullback after the session's
  first pole (`pullback_ord == 1`), filled at the next one-minute open.
* **Exclusions:** no micro pullbacks, no early/imaginary ten-second fill, one
  position per symbol-day.
* **Exit:** `trend_full`; a real initial hard stop is always active. There is
  no profit target.
* **Control:** the scanner paper-records the same setup without catalysts. No
  order is sent in either group.

## Locked decision criteria

Evaluate only after at least 30 catalyst-tagged closed paper trades and 30
non-catalyst control trades:

1. catalyst group stressed net return/trade > 0;
2. catalyst gross return minus control gross return >= +0.40 percentage points;
3. catalyst gross return minus same-day NIFTY 500 open-to-close return >= +0.30
   percentage points;
4. catalyst group annualised Sharpe >= 0.5; and
5. shuffled catalyst labels produce an equal-or-better spread with p < 0.10.

All five must pass. Any failure kills the playbook. No historical news scraping,
threshold changes, additional setups, or live orders are permitted as a rescue.

## Result

Pending forward accumulation. The market was closed at registration; no live
candidate or trade has been observed under this protocol.
