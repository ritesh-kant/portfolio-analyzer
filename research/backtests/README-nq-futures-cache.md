# NQ futures bar cache (Yahoo)

Phase 0 of the AR-model build (`research/specs/ar-model-chart-strategy-plan.md`). There is no CME market data on
the IBKR account, so Nasdaq-100 futures come from Yahoo's `NQ=F`. Yahoo only keeps a sliding window of intraday
history, so we record it ourselves and the history grows over time.

```bash
pnpm bt:nq
pnpm bt:nq --intervals 1m,5m
```

Or from the chart: open `NQ=F` in /chart. The bottom bar shows **"History: 1-min since … · saved … ago"** with an
**Update** button that runs this same script (`POST /api/chart/history`, local only). The chip turns amber after 14
days without a save and red after 25.

**Run it at least every 4 weeks** (weekly is safe), or 1-minute bars fall out of Yahoo's window before they are
saved. Each run takes a few seconds, fetches everything Yahoo still serves and merges it in. Nothing already
saved is lost.

## What Yahoo serves (measured 2026-11-12)

| Interval | Window | First backfill got |
|---|---|---|
| 1m | 4 weeks, 7 days per request (a window starting beyond ~30 days → HTTP 422) | 2026-10-14 → 11-12, 28,676 bars |
| 5m, 15m | ~70 days | 2026-09-03 → 11-12 |
| 60m | ~2.4 years | 2024-06-23 → 11-12 |
| 1d | 10 years | 2016-11-14 → 11-12 |

All 24 hours of the CME session are included (the Asia session too), with real volume. Quotes are delayed (~10
min).

## Layout

`research/backtests/.cache_yahoo_futures/NQ_F/<interval>/<YYYY-MM>.csv` (UTC month), columns
`time,open,high,low,close,volume` (bar start, unix seconds), plus `manifest.json` (coverage, last run, gaps).
The folder ignores itself in git (it holds a `.gitignore` with `*`).

The web chart reads it: `/api/chart/bars` combines the cached bars with Yahoo's answer for any `…=F` symbol that
has a cache folder. When Yahoo refuses a window (older than its limit), the cache alone answers. Locally only: on
Vercel the folder doesn't exist, so the route behaves exactly as before. Override the path with
`FUTURES_CACHE_DIR`.

## Checks done on the first backfill

- Re-running adds only the newest bars, and a still-forming last candle is replaced, not duplicated.
- 1-minute bars aggregate to Yahoo's 5-minute bars: 1 mismatch > 1 tick in 5,689 buckets.
- Yahoo's live trailing tick (stamped to the second) is dropped and is never saved as a bar.
- The gaps the manifest lists are almost all CME holidays. Real Yahoo holes: 60m 2026-01-30 → 02-02 (~3 days),
  5m 2026-11-11 22:30 ET (1.6 h; the 1m series has those minutes).

## ⚠ Contract rolls

`NQ=F` is the front month, **not back-adjusted**. The 60m series jumps on expiry Friday afternoons (2024-09-20
+201 pts, 2025-03-21 +520 pts, both at 15:00 ET). Later expiries show no clear jump, so the switch day is not
certain. Any range, ATR or fib that spans a roll sees a fake move. The backtest (phase 3) has to skip or adjust
roll days.
