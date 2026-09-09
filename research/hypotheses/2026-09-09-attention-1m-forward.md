# Two-stage attention watchlist with one-minute confirmation

**Registered:** 2026-09-09, after reviewing the 2026-09-09 TEGA trade but
before this strategy's first live paper session.
**Status:** registered; forward paper only.
**Start:** first NSE session on or after 2026-09-10.

## Disclosure and claim

The rules below were chosen after observing TEGA's 2026-09-09 chart. That
session is diagnostic and is permanently excluded from evidence for this
strategy. Historical entry windows have already been mined, so this revision
will not be presented as backtested proof.

The claim is that separating early pattern awareness from entry confirmation
will capture valid continuation earlier while avoiding entries into a failed
breakout. Lower day-change and RVOL values create attention, not permission to
trade.

## Frozen forward paper protocol

- **Broad watchlist:** the existing NSE universe and static liquidity/
  eligibility filters.
- **Attention watchlist:** day change at least **+1.5%**, time-of-day cumulative
  RVOL at least **1.5x**, completed five-minute EMA9 above EMA20, and price above
  session VWAP. Promotion also requires either a recognized setup or a doji,
  dragonfly doji, hammer, spinning top, bullish engulfing, tweezer bottom, or
  morning-star tag (plus inverted-hammer attention and three-white-soldiers)
  on the latest one-minute, completed five-minute, or evolving five-minute
  candle.
- **Promotion is not entry:** the promotion candle can never also be the entry
  confirmation. Neutral doji/spinning-top patterns only request attention.
- **Entry confirmation:** on a later closed one-minute candle, require a green
  body, close in the upper 40% of its range, at least **2.5x** its trailing
  20-bar one-minute volume average. The earlier candlestick/setup created the
  attention state; a second named candlestick pattern is not required.
- **Execution:** arm a buy-stop at the confirmation candle high for three
  minutes. Fill only from an observed quote strictly after the decision and at
  or above the trigger. Cancel when the first executable quote is more than 1%
  above the trigger, the stop is not sane, the order expires, or the concurrent
  position cap is full. Never substitute a lower earlier/next-bar open.
- **Risk:** paper only; existing per-trade risk/notional and five-position caps;
  one trade per symbol-day; no new entries from 14:30 IST.
- **Exit:** `trend_full` with the initial hard stop and pattern-failure logic.
- **Records:** promotions, candidates, all rejections, entries and exits are
  stored separately. The forward log must not be mixed with older scanner data.

## Locked review gate

Review after at least 30 closed paper trades and 20 trading sessions. Report
trade count, actual-cost net return/trade, gross return/trade, win rate, median,
95% bootstrap interval, worst trade, maximum daily drawdown, attention-to-entry
conversion, every rejection reason, and event-time data gaps.

The strategy is killed if mean actual-cost net return per trade is not positive
or if its 95% interval excludes the pre-cost viability level of **+0.35% gross**
on the downside. Passing is permission for a larger paper sample, not live-money
approval. No threshold, pattern-set, fill, or exit changes are allowed inside
this sample; any revision gets a new file and a new forward log.

## Result

Pending forward accumulation. The TEGA 2026-09-09 trade is excluded.
