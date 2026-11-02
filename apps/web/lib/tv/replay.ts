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

const FINE_SECS = 60;

/** Index of the first time >= t in an ascending list. */
function firstAtOrAfter(times: number[], t: number): number {
  let lo = 0;
  let hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid]! < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * The next cut-off. Where 1-minute bars cover the cut-off (and the chart is coarser than 1 minute) replay moves one
 * minute at a time, so the forming candle grows the way TradingView's does; elsewhere it moves a whole candle.
 */
export function nextReplayEnd(
  times: number[],
  candleSecs: number,
  fineTimes: number[],
  end: number,
): number | null {
  if (candleSecs > FINE_SECS && fineTimes.length && fineTimes[0]! <= end) {
    const i = firstAtOrAfter(fineTimes, end);
    if (i < fineTimes.length) return fineTimes[i]! + FINE_SECS;
  }
  return nextEnd(times, candleSecs, end);
}

/**
 * The cut-off for starting at candle `index`: its open (nothing of it drawn yet, so it forms minute by minute) when
 * 1-minute bars reach back that far, else the end of that candle as before.
 */
export function startReplayEnd(
  times: number[],
  candleSecs: number,
  fineTimes: number[],
  index: number,
): number {
  const i = Math.max(0, Math.min(times.length - 1, index));
  if (i > 0 && candleSecs > FINE_SECS && fineTimes.length && fineTimes[0]! <= times[i]!) return times[i]!;
  return endAfter(times, candleSecs, index);
}

/**
 * The candle forming at the cut-off: `candle` with its high/low/close/volume rebuilt from the 1-minute bars played
 * so far, or null when none has played yet.
 */
export function formingCandle<
  B extends { time: number; open: number; high: number; low: number; close: number; volume: number },
>(candle: B, candleSecs: number, fine: B[], fineTimes: number[], end: number): B | null {
  if (candleSecs <= FINE_SECS || !fine.length || fine[0]!.time > candle.time || end <= candle.time) return null;
  let high = candle.open;
  let low = candle.open;
  let close = candle.open;
  let volume = 0;
  let any = false;
  const stop = Math.min(end, candle.time + candleSecs);
  for (let k = firstAtOrAfter(fineTimes, candle.time); k < fine.length && fine[k]!.time < stop; k += 1) {
    const b = fine[k]!;
    high = Math.max(high, b.high);
    low = Math.min(low, b.low);
    close = b.close;
    volume += b.volume;
    any = true;
  }
  return any ? { ...candle, high, low, close, volume } : null;
}
