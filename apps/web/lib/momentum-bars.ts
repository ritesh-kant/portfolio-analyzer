import type { MomentumBar } from './momentum-api';

/** Same contract as Python resample_5m: candle START times, clock boundaries,
 * five distinct complete minutes. A missing minute never shifts later buckets. */
export function fiveMinuteBars(bars: MomentumBar[]): MomentumBar[] {
  const minute = 60_000;
  const bucketMs = 5 * minute;
  const buckets = new Map<number, MomentumBar[]>();
  for (const bar of bars) {
    const timestamp = Date.parse(bar.time);
    if (!Number.isFinite(timestamp) || timestamp % minute !== 0) return [];
    const start = Math.floor(timestamp / bucketMs) * bucketMs;
    const chunk = buckets.get(start) ?? [];
    chunk.push(bar);
    buckets.set(start, chunk);
  }
  return [...buckets.entries()].sort(([a], [b]) => a - b).flatMap(([start, chunk]) => {
    chunk.sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
    if (chunk.length !== 5 || chunk.some((bar, i) => Date.parse(bar.time) !== start + i * minute)) {
      return [];
    }
    return [{
      time: new Date(start).toISOString(),
      open: chunk[0]!.open,
      high: Math.max(...chunk.map((bar) => bar.high)),
      low: Math.min(...chunk.map((bar) => bar.low)),
      close: chunk[4]!.close,
      volume: chunk.reduce((sum, bar) => sum + bar.volume, 0),
    }];
  });
}

/** A fill belongs to the candle containing its timestamp, never a future bar. */
export function containingBarIndex(bars: MomentumBar[], time: string | null | undefined, interval: '1m' | '5m'): number {
  if (!time) return -1;
  const needle = Date.parse(time);
  const duration = (interval === '5m' ? 5 : 1) * 60_000;
  return bars.findIndex((bar) => {
    const start = Date.parse(bar.time);
    return start <= needle && needle < start + duration;
  });
}
