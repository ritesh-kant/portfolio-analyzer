import type { Bar } from './types';

/**
 * NSE futures candles come from Upstox's intraday candle API, which only publishes a minute once it has closed
 * (~15–20 s after), so on its own the chart's last price was 20–80 s old. Upstox's live quote
 * (`/v3/market-quote/ohlc?interval=I1`, needs a token) returns the previous and the still-forming 1-minute candle.
 * This folds whichever of those minutes the candle API has not published yet into the chart's bars.
 *
 * Upstox's 5m/15m/30m/1h intraday candles already hold the forming bucket up to the last published minute,
 * so only minutes after `lastPublished` are added — never one twice. Buckets are anchored at the 09:15 IST open,
 * as Upstox's are (1h candles start 09:15, 10:15 …).
 */
export interface LiveMinute {
  /** Minute start, unix seconds. */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const IST = 19_800;
const OPEN = 9 * 3600 + 15 * 60;

/** Start of the `secs`-long bucket holding `time`, counted from that day's 09:15 IST. */
export function bucketStart(time: number, secs: number): number {
  const dayStart = Math.floor((time + IST) / 86_400) * 86_400 - IST;
  const open = dayStart + OPEN;
  return open + Math.floor((time - open) / secs) * secs;
}

export function mergeLiveMinutes(
  bars: Bar[],
  minutes: LiveMinute[],
  lastPublished: number | null,
  secs: number,
): Bar[] {
  const fresh = minutes
    .filter((m) => m.close > 0 && (lastPublished == null || m.time > lastPublished))
    .sort((a, b) => a.time - b.time);
  if (!fresh.length) return bars;
  const out = bars.slice();
  for (const m of fresh) {
    const start = bucketStart(m.time, secs);
    const last = out[out.length - 1];
    if (last && last.time === start) {
      out[out.length - 1] = {
        ...last,
        high: Math.max(last.high, m.high),
        low: Math.min(last.low, m.low),
        close: m.close,
        volume: last.volume + m.volume,
      };
    } else if (!last || start > last.time) {
      out.push({ time: start, open: m.open, high: m.high, low: m.low, close: m.close, volume: m.volume });
    }
  }
  return out;
}
