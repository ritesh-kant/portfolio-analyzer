# Strict max-move checklist

**Registered:** 2026-09-07, before this run.
**Status:** killed 2026-09-07.
**Backtest:** `research/backtests/bt25_max_move_checklist.py`.

## Question

Can the operator's complete checklist select a small number of NSE momentum
trades that are profitable after actual intraday costs, without guessing a fixed
profit target?

This is one fixed checklist, **not** a parameter search for the best historical
result. It is evaluated once on 2025. 2025 has been viewed as part of the
original pool result, but no variation was selected on it; this result is
therefore validation evidence, not a new untouched hold-out.

## Frozen plan

Both arms use NIFTY 500 names already passing the normal price and turnover
universe filters, day change from +4% through +5%, time-of-day relative volume
at least 3x, a next-minute-open fill, and one first setup candidate per
symbol-day. A rejected first candidate may not be replaced by a later setup;
this preserves an exact control subset.

The strict arm additionally requires all of the following at a closed 5-minute
trigger bar:

1. no `micro_pullback` setup;
2. a well-formed chart over the prior 20 sessions;
3. EMA9 above EMA20, price at/above VWAP, and prior-day close at/above the
   20-day daily average;
4. an earlier 2%+ surge with a 2.5x-volume bar;
5. positive MACD histogram;
6. trigger outside the closest half of the stated ₹0.50 round-number interval;
7. no derived resistance within 0.35% above the trigger; and
8. derived support within 0.35% below the trigger.

The exit is `trend_full`: hard stop always active; otherwise exit only on a
confirmed bearish condition (EMA weakness, MACD fade, resistance rejection,
volume climax, or end of day). There is no fixed profit target.

The "wait for a candle close above resistance" rule is already enforced by the
flat-top breakout setup. A DuckDuckGo news search is deliberately absent:
today's ranking cannot establish what information was visible at an old
intraday trigger. A dated event feed or forward capture is required to test it.

## Locked criteria

The control is the same day-change range, first-candidate rule and trend exit,
without the strict checklist. The strict arm must meet every condition:

* at least 30 trades;
* mean gross return at least +0.35% per trade;
* positive total net ₹ after actual MIS charges (not the 0.80% stress case);
* gross-return lift at least +0.20 percentage points over control;
* random-subset anti-test p < 0.05 using 5,000 seeded draws; and
* single-plan deflated Sharpe probability at least 95% versus zero.

Failure of any condition kills this checklist. The component thresholds, the
sample, and the checklist may not be relaxed or re-run to obtain a better
number.

## Results

Single registered run, 2025-01-01 through 2025-12-31, full NIFTY 500 universe
with normal price/turnover filters. Both arms use the same first-candidate rule,
day-change band, next-open fill, and `trend_full` negative-signal exit.

| Measure | Control | Strict checklist |
|---|---:|---:|
| Trades | 461 | **1** |
| Gross return/trade | -0.044% | **-0.748%** |
| Gross total | -₹10,068 | **-₹311** |
| Net total at actual MIS charges | -₹51,747 | **-₹397** |
| Real-cost win rate | 26.2% | **0.0%** |
| Single-plan DSR | 0.000 | not defined (one trade) |

The strict trade was **HSCL, 2025-06-05**: a flat-top breakout at ₹501.40,
entered 14:10 and closed 14:19 at ₹497.65 on a false-break signal. It lost
0.748% gross and ₹397 after actual charges.

The strict arm is an exact subset of control. Its gross lift was **-0.704 pp**;
the seeded random-subset anti-test was **p = 0.8762**. Of 718 observed setup
candidates, 339 were refused for short-term EMA warm-up, 109 for daily
downtrend, 94 for no surge volume, 53 for thin trading, and only two passed all
conditions; only one subsequently filled.

### Decision: KILL

All six locked checks fail: sample size (1 < 30), gross return, actual-cost
profit, lift, anti-test, and DSR. The checklist is **not enabled** in the live
scanner (`require_max_move` remains false). It must not be loosened, have a rule
dropped, or be re-run on another historical period to find a better number.

This does not prove that a real, time-stamped news catalyst cannot work; that is
the only component this replay cannot test. It does show that chart quality,
trend indicators, surge, round levels, support and resistance, combined without
a point-in-time catalyst, do not create a tradeable historical rule here.
