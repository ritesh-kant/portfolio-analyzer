import type { Bar } from './types';

export interface ProfileRow {
  /** Bottom edge of the row. */
  price: number;
  /** Volume from candles that closed up / down. */
  up: number;
  down: number;
}

export interface VolumeProfile {
  rows: ProfileRow[];
  step: number;
  lo: number;
  hi: number;
  /** Row index with the most volume (point of control). */
  poc: number;
  /** Row indices bounding the value area. */
  vaLo: number;
  vaHi: number;
  /** Price edges: value-area high / low, and the POC row's midpoint. */
  vah: number;
  val: number;
  pocPrice: number;
  total: number;
  maxRow: number;
}

export const DEFAULT_ROWS = 24;
export const VALUE_AREA = 0.7;

/**
 * Volume by price over candles `from..to` (inclusive indices, any order, clamped).
 *
 * Each candle's volume is spread over the rows its high–low range overlaps, in
 * proportion to the overlap — we only have candles here, not ticks, so this is
 * an approximation of where inside the candle the volume traded. A candle's
 * volume counts as "up" if it closed at or above its open, else "down".
 * The value area grows out from the point of control, one row at a time toward
 * the busier neighbour, until it holds `valueArea` of the total.
 */
export function buildProfile(
  bars: Bar[],
  from: number,
  to: number,
  rows = DEFAULT_ROWS,
  valueArea = VALUE_AREA,
): VolumeProfile | null {
  if (!bars.length) return null;
  const a = Math.max(0, Math.min(bars.length - 1, Math.min(from, to)));
  const b = Math.max(0, Math.min(bars.length - 1, Math.max(from, to)));
  const slice = bars.slice(a, b + 1);
  let lo = Infinity;
  let hi = -Infinity;
  for (const bar of slice) {
    lo = Math.min(lo, bar.low);
    hi = Math.max(hi, bar.high);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  const count = hi > lo ? Math.max(1, Math.round(rows)) : 1;
  const step = hi > lo ? (hi - lo) / count : Math.max(Math.abs(hi) * 0.001, 0.01);
  const out: ProfileRow[] = Array.from({ length: count }, (_, i) => ({
    price: lo + i * step,
    up: 0,
    down: 0,
  }));

  for (const bar of slice) {
    if (bar.volume <= 0) continue;
    const side = bar.close >= bar.open ? 'up' : 'down';
    const range = bar.high - bar.low;
    if (range <= 0 || count === 1) {
      const idx = Math.min(count - 1, Math.max(0, Math.floor((bar.close - lo) / step)));
      out[idx]![side] += bar.volume;
      continue;
    }
    const first = Math.max(0, Math.floor((bar.low - lo) / step));
    const last = Math.min(count - 1, Math.floor((bar.high - lo) / step));
    for (let r = first; r <= last; r += 1) {
      const bottom = lo + r * step;
      const overlap = Math.min(bar.high, bottom + step) - Math.max(bar.low, bottom);
      if (overlap > 0) out[r]![side] += bar.volume * (overlap / range);
    }
  }

  const sum = (r: ProfileRow) => r.up + r.down;
  let total = 0;
  let poc = 0;
  out.forEach((r, i) => {
    total += sum(r);
    if (sum(r) > sum(out[poc]!)) poc = i;
  });

  let vaLo = poc;
  let vaHi = poc;
  let held = sum(out[poc]!);
  while (held < valueArea * total && (vaLo > 0 || vaHi < count - 1)) {
    const below = vaLo > 0 ? sum(out[vaLo - 1]!) : -1;
    const above = vaHi < count - 1 ? sum(out[vaHi + 1]!) : -1;
    if (above >= below) {
      vaHi += 1;
      held += above;
    } else {
      vaLo -= 1;
      held += below;
    }
  }

  return {
    rows: out,
    step,
    lo,
    hi: count === 1 ? lo + step : hi,
    poc,
    vaLo,
    vaHi,
    vah: out[vaHi]!.price + step,
    val: out[vaLo]!.price,
    pocPrice: out[poc]!.price + step / 2,
    total,
    maxRow: sum(out[poc]!),
  };
}

/** First index in `bars` whose time is >= t. */
function lowerBound(bars: Bar[], t: number): number {
  let lo = 0;
  let hi = bars.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid]!.time < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Profile of chart candles `a..b`, built from the 1-minute candles underneath when they cover the whole range
 * (a much finer shape, and what TradingView does), else from the chart's own candles. Every profile on the chart
 * goes through here so a session profile and a fixed range one over the same candles agree on the POC.
 * Results are memoised in `cache` when given.
 */
export function profileOver(
  bars: Bar[],
  fine: Bar[] | null,
  secs: number,
  a: number,
  b: number,
  rows: number,
  valueArea: number,
  cache?: Map<string, VolumeProfile | null>,
): VolumeProfile | null {
  const start = bars[a]!.time;
  const lastStart = bars[b]!.time;
  const useFine =
    !!fine && fine.length > 0 && fine[0]!.time <= start && fine[fine.length - 1]!.time >= lastStart;
  const source = useFine ? fine! : bars;
  const i0 = useFine ? lowerBound(fine!, start) : a;
  const i1 = useFine ? lowerBound(fine!, lastStart + secs) - 1 : b;
  const last = source[i1];
  const key = `${a}|${b}|${rows}|${valueArea}|${useFine}|${i1}|${last?.volume}|${last?.high}|${last?.low}`;
  if (cache?.has(key)) return cache.get(key)!;
  const profile = i1 >= i0 ? buildProfile(source, i0, i1, rows, valueArea) : null;
  if (cache) {
    if (cache.size > 600) cache.clear();
    cache.set(key, profile);
  }
  return profile;
}
