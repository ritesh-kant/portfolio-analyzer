# Four-bar price/volume quadrant overlay — exploratory same-day replay

**Requested and measured:** 2026-09-18.  
**Status:** enabled by explicit operator decision; no separate arm.  
**Data status:** post-hoc and fully spent. The rule was specified after seeing
the session's trades, so this result cannot license deployment.

## Rule tried

On top of every entry the paper ledger actually accepted, use the last four
completed one-minute bars at the decision time. Fit normalized least-squares
slopes to close and total volume. Keep a long entry only when both slopes are
strictly positive (`up_price_up_volume`).

This is total OHLCV volume, not true buy/sell volume. The feed does not identify
the aggressor side. Four bars were chosen because the source graphic depicts
four volume bars; the window was not optimized.

Implementation is `EngineConfig.require_rising_price_volume`. Its dataclass
default remains `False` for historical reproducibility, but it is explicitly
enabled in both `attention_1m_merged` (the strategy actually recorded today)
and `warrior_strict` (the strategy named by the deployment template). No new
strategy arm was created.
The reproducible ledger replay is `research/backtests/bt38_today_price_volume_overlay.py`.

## Same-day result

The Mongo paper ledger held 25 closed positions for the 2026-09-18 NSE session,
all labelled `attention_1m_merged`. Despite the current infrastructure template
naming `warrior_strict`, no `warrior_strict` position, candidate, or rejection
was recorded from 2026-09-16 through this session. This replay therefore reports
what actually ran, not what the checked-in deployment template intends.

| | trades | gross INR | net INR |
|---|---:|---:|---:|
| Recorded positions | 25 | +859.05 | −1,614.33 |
| Overlay kept | 22 | +1,339.25 | −826.46 |
| Overlay refused | 3 | −480.20 | −787.87 |

The three refused entries were HOMEFIRST 12:12, POONAWALLA 12:26, and HUDCO
12:48 IST. All had rising four-bar close slopes and falling four-bar volume
slopes, and all lost after costs.

## Interpretation and limit

The overlay improved this one day's recorded result by ₹787.87, but the kept
set still lost ₹826.46 net. This is a trade-level counterfactual, not a complete
day replay: refusing an entry can make room for a later replacement under the
multi-entry engine, and those replacement candidates are not reconstructed.

Most importantly, the rule and window were applied to the same day that
motivated the request. Three refused observations are not evidence of an edge.
The operator explicitly chose to keep the filter on without a separate arm.
Accordingly, future results measure a changed bundled strategy and cannot be
compared as continuation of either arm's previously registered sample. The
four-bar window is frozen; historical threshold search would be fitting.

## Validation correction and five-minute check

The shared slope helper was hardened to use bars from the current session only.
The live bar builder normally supplies today's bars, but stored-chart replays
can include prior-session warm-up; without this boundary an entry in the first
three minutes could incorrectly form a four-bar slope across the overnight gap.

A completed four-bar **five-minute** version was also evaluated on the same 25
positions, using only 5-minute candles fully closed by the decision time. It was
not enabled:

| Gate | trades kept | gross INR | net INR |
|---|---:|---:|---:|
| 1-minute overlay | 22 | +1,339.25 | −826.46 |
| 1-minute + 5-minute overlays | 11 | −472.35 | −1,534.21 |

The 5-minute overlay rejected 11 trades that passed the 1-minute rule; together
they earned +₹707.75 net, including EIHOTEL's +₹1,313.06 winner. Requiring both
timeframes would therefore have removed the profitable half of this day's
accepted set and nearly erased the 1-minute overlay's improvement. The existing
five-minute EMA/VWAP trend context remains in force; no extra 5-minute volume
slope was added.
