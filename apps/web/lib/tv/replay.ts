/**
 * Replay works on a cut-off instant `end` (unix seconds): the chart shows only
 * candles that were COMPLETE at `end`, so a coarser timeframe never reveals the
 * not-yet-finished part of its last candle.
 *
 * A candle is complete when its own interval has elapsed, or when the next
 * candle has already begun (covers a session's last candle, which is shorter
 * than the nominal interval).
 */
export function completedCount(times: number[], candleSecs: number, end: number): number {
  const n = times.length;
  const complete = (i: number) =>
    times[i]! + candleSecs <= end || (i + 1 < n && times[i + 1]! <= end);
  // `complete` is true for a prefix of the series, so binary-search its boundary.
  let lo = 0;
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (complete(mid)) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** The cut-off after one more candle completes, or null when the series is exhausted. */
export function nextEnd(times: number[], candleSecs: number, end: number): number | null {
  const c = completedCount(times, candleSecs, end);
  return c >= times.length ? null : times[c]! + candleSecs;
}

/** The cut-off that makes candle `index` the last visible one. */
export const endAfter = (times: number[], candleSecs: number, index: number): number =>
  times[Math.max(0, Math.min(times.length - 1, index))]! + candleSecs;

/** TradingView's speed ladder, in candles per second. */
export const REPLAY_SPEEDS = [10, 7, 5, 3, 1, 0.5, 0.3, 0.1] as const;
