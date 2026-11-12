import type { RawTick, Side } from './bigtrades';
import type { Bar, FootprintCandle, FootprintLevel } from './types';

/**
 * Footprint (bid × ask volume by price) built from the recorded Upstox feed instead of estimated from 1-minute
 * bars. Used for NSE futures, which `apps/signal-engine/scripts/capture_ticks.py --futures …` records.
 *
 * What the feed gives (measured on NIFTY / RELIANCE futures, 2026-11-12): an update about every 0.8 s (NIFTY) to
 * 3 s (RELIANCE), each carrying the LAST trade (price, quantity), the best bid/ask and the day's cumulative
 * volume. So:
 *   - the volume between two updates is EXACT (difference of the day volume);
 *   - only ~25–30 % of it is seen as an individual trade, at a known price;
 *   - the rest traded somewhere on the way from the previous update's price to this one (median ₹1 apart on
 *     NIFTY, ₹0.10 on RELIANCE).
 *
 * How each update's volume is placed:
 *   - the seen trade goes on its own price row; buyer when it printed at/above the ask, seller at/below the bid
 *     (this update's quote, else the previous update's: the quote that rides with a trade is often from just
 *     before it), else the tick rule (up from the last price = buyer). `quote` marks which.
 *   - the unseen rest is spread evenly over the rows from the previous price to this one, buyer if price rose,
 *     seller if it fell, and the seen trade's side if it did not move. Always a tick-rule label.
 */

/** Volume that traded between two consecutive feed updates of one instrument. */
export interface VolumeStep {
  /** Last-trade time of this update, ms. */
  t: number;
  /** Last trade price at this update. */
  p: number;
  /** Last trade price at the previous update (= p for the first update after a gap). */
  from: number;
  /** Exact volume since the previous update (the seen trade only, for the first update after a gap). */
  vol: number;
  /** Part of `vol` seen as a single trade at `p`. */
  q: number;
  /** Aggressor of the seen trade; null when nothing tells (first update, no quote). */
  side: Side | null;
  /** The side came from the bid/ask rather than the tick rule. */
  quote: boolean;
}

/** Updates further apart than this are treated as a hole in the recording (recorder stopped, feed dropped). */
export const MAX_GAP_MS = 120_000;

export interface TickSeries {
  steps: VolumeStep[];
  /** Spans recorded without a hole, [first ms, last ms]. A candle uses ticks only if one span covers it. */
  segments: [number, number][];
}

function quoteSide(p: number, bid: number | null, ask: number | null): Side | null {
  if (ask != null && p >= ask) return 'buy';
  if (bid != null && p <= bid) return 'sell';
  return null;
}

/** One capture file (one instrument, one or more days) → volume per update. */
export function volumeSteps(ticks: RawTick[], maxGapMs = MAX_GAP_MS): TickSeries {
  const sorted = [...ticks]
    .filter((k) => Number.isFinite(k.t) && k.p > 0 && Number.isFinite(k.v))
    .sort((a, b) => a.t - b.t);
  const steps: VolumeStep[] = [];
  const segments: [number, number][] = [];
  let prev: RawTick | null = null;
  let prevSide: Side | null = null;
  for (const k of sorted) {
    if (prev && k.t === prev.t) continue; // the same trade twice
    const fresh = !prev || k.t - prev.t > maxGapMs || k.v < prev.v; // hole, or a new day's volume counter
    if (fresh) {
      segments.push([k.t, k.t]);
      prevSide = null;
    } else segments[segments.length - 1]![1] = k.t;
    const dv = fresh ? Math.max(0, k.q) : k.v - prev!.v;
    if (dv > 0) {
      let side = quoteSide(k.p, k.bp, k.ap) ?? (fresh ? null : quoteSide(k.p, prev!.bp, prev!.ap));
      const quote = side != null;
      if (!side && !fresh) side = k.p > prev!.p ? 'buy' : k.p < prev!.p ? 'sell' : prevSide;
      if (side) prevSide = side;
      steps.push({
        t: k.t,
        p: k.p,
        from: fresh ? k.p : prev!.p,
        vol: dv,
        q: Math.min(Math.max(0, k.q), dv),
        side,
        quote,
      });
    }
    prev = k;
  }
  return { steps, segments };
}

const rowIndex = (price: number, step: number) => Math.floor(price / step + 1e-9);

/** Per-candle footprint with where its numbers came from. */
export interface TickFootprint extends FootprintCandle {
  source: 'ticks';
  /** Share of the candle's volume whose side came from the bid/ask (the rest: tick rule). */
  quoteShare: number;
  /** Share of the candle's volume seen as individual trades at a known price. */
  seenShare: number;
}

function add(rows: Map<number, FootprintLevel>, r: number, step: number, buy: number, sell: number) {
  let level = rows.get(r);
  if (!level) {
    level = { price: r * step, bid: 0, ask: 0 };
    rows.set(r, level);
  }
  level.ask += buy;
  level.bid += sell;
}

function split(vol: number, side: Side | null): [number, number] {
  if (side === 'buy') return [vol, 0];
  if (side === 'sell') return [0, vol];
  return [vol / 2, vol / 2];
}

/**
 * One footprint per chart candle from the recorded volume steps; null where no recorded span covers the whole
 * candle (the caller falls back to the 1-minute estimate there). The last candle counts as covered once the
 * recording reached its start, so the forming candle updates live. Bar times are seconds, step times ms.
 */
export function buildTickFootprints(
  chartBars: Bar[],
  series: TickSeries,
  candleSecs: number,
  step: number,
): (TickFootprint | null)[] {
  const out: (TickFootprint | null)[] = new Array(chartBars.length).fill(null);
  const { steps, segments } = series;
  if (!steps.length || !chartBars.length || !(step > 0)) return out;
  let s = 0;
  for (let i = 0; i < chartBars.length; i += 1) {
    const start = chartBars[i]!.time * 1000;
    const nextStart = i + 1 < chartBars.length ? chartBars[i + 1]!.time * 1000 : Infinity;
    const end = Math.min(nextStart, start + candleSecs * 1000);
    const last = i === chartBars.length - 1;
    const covered = segments.some(([a, b]) => a <= start && (b >= end || (last && b >= start)));
    while (s < steps.length && steps[s]!.t < start) s += 1;
    if (!covered) continue;
    const rows = new Map<number, FootprintLevel>();
    let total = 0;
    let quoted = 0;
    let seen = 0;
    let e = s;
    while (e < steps.length && steps[e]!.t < end) {
      const x = steps[e]!;
      e += 1;
      total += x.vol;
      seen += x.q;
      if (x.quote) quoted += x.q;
      if (x.q > 0) {
        const [b, sl] = split(x.q, x.side);
        add(rows, rowIndex(x.p, step), step, b, sl);
      }
      const rest = x.vol - x.q;
      if (rest <= 0) continue;
      const restSide: Side | null = x.p > x.from ? 'buy' : x.p < x.from ? 'sell' : x.side;
      const r0 = rowIndex(Math.min(x.p, x.from), step);
      const r1 = rowIndex(Math.max(x.p, x.from), step);
      const per = rest / (r1 - r0 + 1);
      const [b, sl] = split(per, restSide);
      for (let r = r0; r <= r1; r += 1) add(rows, r, step, b, sl);
    }
    if (!rows.size || !(total > 0)) continue;
    const levels = [...rows.entries()].sort(([a], [b]) => a - b).map(([, level]) => level);
    let poc = 0;
    let delta = 0;
    levels.forEach((level, idx) => {
      delta += level.ask - level.bid;
      if (level.ask + level.bid > levels[poc]!.ask + levels[poc]!.bid) poc = idx;
    });
    out[i] = {
      poc,
      levels,
      delta,
      total,
      step,
      source: 'ticks',
      quoteShare: quoted / total,
      seenShare: seen / total,
    };
  }
  return out;
}

/** Recorded footprints where they exist, the 1-minute estimate elsewhere. */
export function mergeFootprints<T>(estimated: (T | null)[], recorded: (T | null)[]): (T | null)[] {
  if (!recorded.length) return estimated;
  return estimated.map((fp, i) => recorded[i] ?? fp);
}

/** Wire format of the ticks route's `steps` view: [t, p, from, vol, q, side (1 buy, -1 sell, 0 none), quote 0/1]. */
export type PackedStep = [number, number, number, number, number, number, number];

export function packSteps(steps: VolumeStep[]): PackedStep[] {
  return steps.map((x) => [
    x.t,
    x.p,
    x.from,
    x.vol,
    x.q,
    x.side === 'buy' ? 1 : x.side === 'sell' ? -1 : 0,
    x.quote ? 1 : 0,
  ]);
}

export function unpackSteps(rows: PackedStep[]): VolumeStep[] {
  return rows.map(([t, p, from, vol, q, side, quote]) => ({
    t,
    p,
    from,
    vol,
    q,
    side: side === 1 ? 'buy' : side === -1 ? 'sell' : null,
    quote: quote === 1,
  }));
}
