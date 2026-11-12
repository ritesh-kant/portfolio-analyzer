import { atr } from './indicators.ts';
import type { Bar, FootprintCandle, FootprintLevel } from './types';

/**
 * Stacked imbalances (the course's step 4d: "stacked imbalances at that spot make it better").
 *
 * A footprint row is a DIAGONAL imbalance when buyers lifting the offer at a price outweigh sellers hitting the bid
 * one row lower by `ratio` (or the mirror for sellers). Several imbalanced rows in a row on the same side are
 * "stacked": aggressive buying (or selling) right through a price band. The band becomes a zone that tends to
 * hold on a retest, until price trades all the way through it.
 *
 * The footprint chart (components/tv/footprint-series.ts) marks the same rows with the same rule; both call
 * `diagonalImbalances` so they can never disagree.
 *
 * Honest limits: on most candles the footprint is ESTIMATED from 1-minute bars (each minute's volume split into
 * buyers and sellers by where it closed), so these are estimated stacks, not real order flow. Candles built from
 * recorded ticks (`source: 'ticks'`) are marked `estimated: false`.
 */

/**
 * Per row: is it an ask (buyer) / bid (seller) diagonal imbalance? Levels sorted from low to high price. With `step`,
 * a row is only compared with the row directly next to it in price (a gap in the rows is no evidence either way).
 */
export function diagonalImbalances(
  levels: FootprintLevel[],
  ratio: number,
  step?: number,
): { ask: boolean[]; bid: boolean[] } {
  const next = (a: FootprintLevel, b: FootprintLevel | undefined) =>
    b != null && (step == null || Math.abs(Math.abs(b.price - a.price) - step) < step * 1e-6);
  const ask = levels.map((l, i) => {
    const below = levels[i - 1];
    return next(l, below) ? l.ask > 0 && l.ask >= ratio * Math.max(below!.bid, 1) : false;
  });
  const bid = levels.map((l, i) => {
    const above = levels[i + 1];
    return next(l, above) ? l.bid > 0 && l.bid >= ratio * Math.max(above!.ask, 1) : false;
  });
  return { ask, bid };
}

/** Runs of at least `n` consecutive true flags, as [first, last] index pairs. `n` < 2 keeps single rows too. */
export function flagRuns(flags: boolean[], n: number): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  for (let i = 0; i <= flags.length; i += 1) {
    if (i < flags.length && flags[i]) {
      if (start < 0) start = i;
      continue;
    }
    if (start >= 0 && i - start >= Math.max(1, n)) out.push([start, i - 1]);
    start = -1;
  }
  return out;
}

/** Only the flags inside runs of at least `n` (`n` < 2 = every flag). What the footprint highlights. */
export function stackedOnly(flags: boolean[], n: number): boolean[] {
  if (n < 2) return flags;
  const out = flags.map(() => false);
  for (const [a, b] of flagRuns(flags, n)) for (let j = a; j <= b; j += 1) out[j] = true;
  return out;
}

export interface StackZone {
  /** Candle the stack printed in: the zone is known at that candle's close. */
  index: number;
  /** 'buy' = stacked ask imbalances (support below price); 'sell' = stacked bid imbalances (resistance). */
  side: 'buy' | 'sell';
  /** Price band: bottom of the lowest row to the top of the highest. */
  lo: number;
  hi: number;
  /** Imbalanced rows in the stack. */
  rows: number;
  /** First later candle that traded all the way through the zone (buy: low below `lo`; sell: high above `hi`). */
  usedAt: number | null;
  /** True unless the candle's footprint came from recorded ticks. */
  estimated: boolean;
}

export interface StackCfg {
  /** Imbalance ratio, e.g. 3 = one side at least 3× the other (300 %). */
  ratio: number;
  /** Rows that must stack. */
  minRows: number;
  /**
   * Drop stacks taller than this × ATR 14 of the chart's candles. A real stack is a handful of rows; one taller
   * than a typical candle is a bad print (e.g. Yahoo's NQ minute of 2026-10-30 15:00 New York, 1,267 points).
   */
  maxAtr: number;
}

export const STACK_DEFAULTS: StackCfg = { ratio: 3, minRows: 3, maxAtr: 1 };

/**
 * Every stacked-imbalance zone on the chart, in candle order. `footprints[i]` belongs to `bars[i]` (null where no
 * footprint exists). Uses nothing after a zone's own candle except to find when it was traded through.
 */
export function stackedZones(
  bars: Bar[],
  footprints: (FootprintCandle | null)[],
  cfg: Partial<StackCfg> = {},
): StackZone[] {
  const { ratio, minRows, maxAtr } = { ...STACK_DEFAULTS, ...cfg };
  const zones: StackZone[] = [];
  const a14 = atr(bars, 14);
  const n = Math.min(bars.length, footprints.length);
  for (let i = 0; i < n; i += 1) {
    const fp = footprints[i];
    if (!fp || fp.levels.length < 2) continue;
    const { ask, bid } = diagonalImbalances(fp.levels, ratio, fp.step);
    // ATR known at this candle (before 14 candles exist: the candle's own range)
    const cap = maxAtr * (a14[i] ?? bars[i]!.high - bars[i]!.low);
    const add = (side: StackZone['side'], [a, b]: [number, number]) => {
      const lo = fp.levels[a]!.price;
      const hi = fp.levels[b]!.price + fp.step;
      if (cap > 0 && hi - lo > cap) return;
      zones.push({
        index: i,
        side,
        lo,
        hi,
        rows: b - a + 1,
        usedAt: null,
        estimated: fp.source !== 'ticks',
      });
    };
    for (const run of flagRuns(ask, Math.max(2, minRows))) add('buy', run);
    for (const run of flagRuns(bid, Math.max(2, minRows))) add('sell', run);
  }
  // when each zone was traded through: one pass, oldest zones first
  const open = [...zones];
  for (let j = 0; j < bars.length && open.length; j += 1) {
    const b = bars[j]!;
    for (let k = open.length - 1; k >= 0; k -= 1) {
      const z = open[k]!;
      if (j <= z.index) continue;
      if (z.side === 'buy' ? b.low < z.lo : b.high > z.hi) {
        z.usedAt = j;
        open.splice(k, 1);
      }
    }
  }
  return zones;
}

/** Still standing at the close of candle `asOf`: printed by then and not yet traded through. */
export const zoneActive = (z: StackZone, asOf: number) => z.index <= asOf && (z.usedAt == null || z.usedAt > asOf);

/**
 * The same-side zone standing at the close of `asOf` that holds `price` (± `tolerance`), the youngest if several.
 * A long wants a buy zone under it, a short a sell zone.
 */
export function zoneAt(
  zones: StackZone[],
  side: StackZone['side'],
  price: number,
  asOf: number,
  tolerance = 0,
): StackZone | null {
  let best: StackZone | null = null;
  for (const z of zones) {
    if (z.side !== side || !zoneActive(z, asOf)) continue;
    if (price < z.lo - tolerance || price > z.hi + tolerance) continue;
    if (!best || z.index > best.index) best = z;
  }
  return best;
}
