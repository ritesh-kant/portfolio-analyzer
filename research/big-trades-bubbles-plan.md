# Big Trades bubbles — implementation plan

Goal: circles on the chart (green = aggressive buy, red = aggressive sell), sized by trade quantity, at trade time and price. Same idea as MotiveWave "Big Trades" / Bookmap volume dots.

## What the three tools do

| | Data | Aggressor side | Aggregation | Size filter |
|---|---|---|---|---|
| MotiveWave Big Trades | historical tick data | @Ask flag (trade at ask = buy, at bid = sell) | Aggregate Period (ms): merges trades at the same price and same side within the window | Min Trade Size, up to 5 tiers; larger drawn on top; max 500 drawn, 25,000 computed |
| Bookmap volume dots | full tape + order book | exchange/inferred aggressor, colour gradient by aggressiveness | by volume, by time, or "smart" (merge adjacent trades, time-weighted position) | Minimal Trade Size |
| TradingView | Pine only sees OHLCV; `request.security_lower_tf` samples lower-TF bars; native `request.footprint` on Premium/Ultimate | inferred from candle direction (estimate) | none | script input |

Takeaway: the real feature = tick + aggressor side + same-price/same-side aggregation + size tiers + draw cap. TradingView's version is an approximation and is not a model to copy.

## Where we are (audit)

- No per-trade data with size or side exists anywhere. CVD, delta and footprint are all inferred from 1-minute OHLCV.
- Upstox V3 full mode: `upstox.py:318-375` forwards only `ltpc` and `vtt`. `marketFF` also carries last traded qty (`ltq`) and 5-deep bid/ask. Unverified field names, so check the protobuf first.
- Yahoo US WebSocket: `yahoo_stream.py` receives an optional `last_size` but `_parse` drops it. No bid/ask, so only the tick rule applies.
- Ticks are not persisted (only a manual JSONL recorder, `yahoo_stream.py:449`).
- Web chart: overlays are `ISeriesPrimitive` classes attached at `tv-chart.tsx:1580-1589`; copy `delta-primitive.ts`. Settings are localStorage. Live updates poll every 3 s; replay filters by `replayEnd`.

## Phases

### Phase 0 — feasibility spike (do first, ~1 hour)
1. Run the Upstox streamer for one liquid NSE symbol during market hours and dump a raw full-mode message. Confirm `ltq`, bid/ask and whether each tick is a trade or a snapshot (Upstox may batch/sample trades, so some prints could be missed).
2. Same for Yahoo `last_size` on a US small cap.
3. Decision gate: if `ltq` is reliable per update, continue. If ticks are sampled, bubbles are "largest samples", and we say so in the UI.

### Phase 1 — tick capture and storage
- Extend `upstox.py` to forward `ltq` + best bid/ask; parse `last_size` in `yahoo_stream.py`.
- Classify aggressor at capture: price >= ask → buy, <= bid → sell, else tick rule (up-tick buy, down-tick sell, zero-tick inherits previous).
- Store in new Mongo collection `mt_ticks` (symbol, ts, price, qty, side, method). Index on (symbol, ts). Only a watchlist of symbols; set a TTL (e.g. 30 days) because volume is large.
- Keep the capture in a separate task so it cannot slow the scanner's engine path.

### Phase 2 — API
- `GET /api/chart/ticks?symbol&from&to&minQty` returns aggregated big trades (server-side aggregation: same price + same side within N ms, as MotiveWave does). Cap the response at 500.

### Phase 3 — chart overlay
- `BigTradesPrimitive` (copy of `delta-primitive.ts`): circle radius ∝ sqrt(qty), colour by side, qty label, larger drawn first-under/on-top, tier thresholds.
- `big-trades-panel.tsx` for settings: min size tiers, aggregate window, colours, outline. Persist in localStorage like the others; add to `IndicatorType` in `indicator-defs.ts`.
- Respect replay (`time < replayEnd`), refresh with the existing 3 s poll.
- Show a clear "no tick data for this period" state, because history before capture starts does not exist.

### Phase 4 — validation
- Compare bubbles against a MotiveWave/TradingView footprint on the same symbol and minute for a few sessions. Check totals per minute match the bars' volume.
- Unit tests for aggressor classification and aggregation.

## Honest limits
- History starts the day capture is switched on. No backfill.
- Upstox gives no true aggressor flag, so side is inferred (bid/ask compare is good; tick rule is rougher). Bubbles for US names are tick-rule only.
- This improves the chart and could later feed a real CVD/delta in the Lab, but nothing here is a validated edge. Any use in a strategy still goes through the pre-registered hypothesis flow.

## Open questions for you
1. NSE only, US only, or both first?
2. Which symbols to capture (the momentum watchlist, or a fixed list)?
3. Retention: how many days of ticks to keep?

## Phase 0 spike results (2026-11-05, 17:25 IST, both markets closed/pre-market)

- **Upstox schema (confirmed from the installed protobuf):** `LTPC` has `ltp, ltt, ltq, cp` (ltq = last traded quantity). `MarketFullFeed` has `marketLevel.bidAskQuote` (`bidP, bidQ, askP, askQ`, 5 levels), `vtt`, `tbq`, `tsq`, `atp`. So side classification (price vs best bid/ask) and trade size are available in principle.
- **Upstox live capture: NOT done.** Handshake returned 401 Unauthorized; the access token in `.env` has expired. It needs refreshing, then rerun during NSE hours (09:15–15:30 IST). Script: `/private/tmp/claude-502/-Users-ritesh-codebase-ritesh-codebase-portfolio-analyzer/cdfb3c0c-69ca-418b-abfd-725d16de8ff0/scratchpad/spike_upstox.py`.
- **Yahoo (US) pre-market, 25 s, NVDA/TSLA/AAPL/SOFI:** 2 messages only, fields `id, price, time, exchange, quote_type, change, change_percent, price_hint`. No `last_size` or day volume appeared in these. Pre-market is thin, so rerun in the US regular session (20:00 IST now that DST has ended) before concluding.
- **Still unanswered:** does Upstox send one message per trade or a sampled snapshot, and is `ltq` per trade? That decides whether bubbles are true prints.

### Update: Upstox auth fixed (same day)
The 401 was my script reading the empty legacy `UPSTOX_ACCESS_TOKEN`; the scanner uses `UPSTOX_ANALYTICS_TOKEN` first. With that token the V3 full-mode stream connects. After-hours snapshot for RELIANCE (NSE closed): `ltpc` has `ltq` ("1", the closing-auction print), `marketLevel.bidAskQuote` has 5 slots but only the ask side was filled (post-close), `vtt`, `atp`, `tsq`. Mode is `full_d5` (5-deep). Still unanswered: message-per-trade vs sampled snapshot — needs NSE hours (09:15 IST) or a live MCX contract (MCX_FO was NORMAL_OPEN at 17:30).

### Phase 0 result — Upstox live capture (MCX, 2026-11-05 ~17:30 IST, 75 s, CRUDEOIL + NATURALGAS futures)
- The stream sends about **1 message per second** per instrument (72 and 55 messages in 74 s), and 41/71 and 39/54 repeat the previous `ltt` (snapshots, no new trade).
- `ltq` is the **last trade only**. Comparing lots visible through `ltq` on new-`ltt` messages with the real lots traded (`vtt` delta): **crude 63 of 127 lots (50%), natgas 31 of 44 (70%)**. Trades between snapshots are dropped.
- `vtt` is exact total volume (in lots on MCX; `ltq` is in units, e.g. 100 per crude lot), so per-second volume is right but individual prints are not.
- Bid/ask best-level price/qty arrives with every message, so side classification of the sampled trade (price vs bid/ask) works.
- **Conclusion:** Upstox cannot give a true Big Trades tape. Bubbles from it would be sampled prints (≈50–70% coverage on these two contracts) plus per-second volume from `vtt`. NSE equities are untested; check at 09:15 IST on a liquid stock before deciding.
- Real per-trade tape would need a different source (broker tick-by-tick, e.g. Kite/Zerodha ticker "full" mode is also snapshot-based; true tape = exchange TBT feed / vendor).

## Built (sampled version, 2026-11-05) — local only
- `apps/signal-engine/scripts/capture_ticks.py` records new trades (one JSONL line per new `ltt`) to `apps/signal-engine/data/ticks/<IST day>/<key>.jsonl` (git-ignored). Usage: `.venv/bin/python scripts/capture_ticks.py --symbols RELIANCE,INFY --until 15:30`.
- `apps/web/lib/tv/bigtrades.ts` (+ 5 node tests): aggressor classification (best bid/ask, else tick rule), same-price/same-side aggregation window, min-size filter, coverage estimate.
- `apps/web/app/api/chart/ticks/route.ts`: serves the last 3 capture days for a chart symbol (reads local disk, so **it returns nothing on Vercel**).
- `apps/web/components/tv/big-trades-primitive.ts` + `big-trades-control.tsx`, wired in `tv-chart.tsx`: "Big Trades" switch in the top bar next to Replay, settings popover (min size, aggregate ms), replay-aware, 500-bubble cap.
- Verified in the browser on a synthetic RELIANCE tape (deleted afterwards). Not yet verified on a real NSE session. `coverage()` is only meaningful where `ltq` and `vtt` share a unit (NSE equities; not MCX, where `vtt` is in lots).
- Production step still open: store ticks in Mongo and serve through portfolio-service.
