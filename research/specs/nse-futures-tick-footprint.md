# NSE futures footprint from recorded ticks

For NSE futures (`NIFTY1!.NFO`, `BANKNIFTY1!.NFO`, `RELIANCE1!.NFO`, …) the chart's footprint (bid × ask volume
per price row, delta, imbalances) can now come from the **recorded Upstox feed** instead of the estimate from
1-minute bars. A recorded candle draws its Delta/Total box with a **blue outline**, and the footprint banner says
how many candles are recorded. Every other candle keeps the old estimate.

## How to record

In /chart, open an NSE future. The bottom bar shows **"Ticks: …"** with a **Record till 15:30** button. It starts
`apps/signal-engine/scripts/capture_ticks.py` for that contract in the background (it survives a dev-server
reload and stops by itself at 15:30). **Stop** ends it. Only the time it runs is covered, so start it **before
09:15** to get the whole session. From a terminal, for several contracts at once:

```bash
cd apps/signal-engine && .venv/bin/python scripts/capture_ticks.py --futures NIFTY,BANKNIFTY --until 15:30
```

It needs a valid Upstox token in the repo `.env` (`UPSTOX_ANALYTICS_TOKEN` or `UPSTOX_ACCESS_TOKEN`). With an
expired token the chip turns red ("running but no data"), and the recorder's log is in its tooltip. Data goes to
`apps/signal-engine/data/ticks/<IST day>/NSE_FO_<id>.jsonl` (git-ignored). Local only: none of this exists on
Vercel.

## What the data is — and is not (measured 2026-11-12)

The Upstox V3 feed sends a snapshot per instrument, not every trade. Each snapshot carries the last trade, the best
bid/ask and the **day's cumulative volume**.

| | NIFTY fut | BANKNIFTY fut | RELIANCE fut |
|---|---|---|---|
| Update every | 0.8 s (noon) – 2 s (afternoon, 3 contracts subscribed) | ~2.5 s | ~3 s |
| Recorded volume vs Upstox 1-min candles, 5 min | 98.8 % | 100 % | 102.4 % |
| Volume seen as individual trades | ~27 % | | ~30 % |
| Price move between updates | median ₹1, 90 % within ₹5 | | median ₹0.10 |
| Seen trades labelled from the bid/ask | 80 % | | 96 % |

So:
- **Volume per update is exact.** Per minute it matches Upstox's own candles to within one update's volume
  (an update that straddles the minute boundary lands in one minute or the other).
- **Price placement is approximate.** The seen trade sits on its own price row. The unseen ~73 % is spread
  evenly over the rows between the previous update's price and this one. On NIFTY use footprint rows of ₹1 or
  wider. Finer rows show detail that was never measured.
- **Buyer/seller is partly inferred.** The seen trade is a buyer at/above the ask and a seller at/below the bid.
  It checks this update's quote first, then the previous one's, since the quote that rides with a trade is often
  from just before it. Otherwise it falls back to price direction. The unseen volume always uses price direction
  (rising = buyers), the textbook "tick rule". The banner gives the share of volume labelled from the bid/ask:
  ~22–26 % on NIFTY. The rest is the tick rule. Delta and imbalances inherit that.
- **No history.** Upstox keeps candles, not trades. A footprint exists only for minutes we recorded. Real
  exchange tick history would need a paid NSE data vendor (not checked).
- **Gaps.** Updates more than 2 minutes apart count as a hole in the recording, and the candles around it fall
  back to the estimate. A thin contract quiet for 2 minutes also triggers this (FINNIFTY futures went 12 s
  without a trade at 14:30).

## Code

| | |
|---|---|
| Volume per update, placement, side, candle coverage | `apps/web/lib/tv/tick-footprint.ts` (+ `.test.mjs`) |
| Serves it to the chart | `GET /api/chart/ticks?symbol=…&view=steps` |
| Start / stop / status of the recorder | `/api/chart/ticks/record`, `lib/tv/tick-recorder.ts`, `lib/tv/ticks.server.ts` |
| Chart wiring (recorded where covered, estimate elsewhere; replay-safe) | `useTickSteps` in `components/tv/use-tick-steps.ts`, `tickFootprints` in `tv-chart.tsx` |
| Chip | `components/tv/ticks-chip.tsx` |
| Recorder (`--futures` = front-month contract by underlying) | `apps/signal-engine/scripts/capture_ticks.py` |

Not done: scheduling the recorder for every session (launchd/cron) and the stacked-imbalance extension lines
and min-volume filter from the course (drawing features, independent of the data source).
