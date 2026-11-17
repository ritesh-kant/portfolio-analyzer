import type { Bar, FootprintCandle } from './types';

/**
 * The course's order-flow trigger (step 4 of research/specs/orderflow-course-ar-model.md), phase 4 of
 * research/specs/ar-model-chart-strategy-plan.md. Three pure checks on one candle's footprint; the strategy
 * (lib/tv/ar-strategy.ts, `confirm: 'orderflow'`) strings them together:
 *
 *   4a Absorption  "high effort, low result": the candle holding the extreme since the zone touch traded far more
 *                  than usual (≥ `absorbVol` × the median volume of the 12 candles before it), its POC sits in the
 *                  third of the candle at the rejection end (his "POC stuck at the rejection end / in the wick"), and
 *                  it closed in the other half of its range (the push produced no result).
 *   4c Delta flip  a LATER candle whose delta is on the trade's side by at least `flipShare` of its volume, that
 *                  closes in the trade's direction and beyond the absorption candle's close, without a new extreme
 *                  ("measurable change in price behaviour"; "next candle fails to continue").
 *   4d Entry       the next candle pulls back into the confirmation candle's low-volume node: the thinnest inner
 *                  row between its close and the far end (`lvnPrice`).
 *
 * Thresholds are OURS: he shows one example (absorption candle 12K contracts, 2–4× its neighbours, POC at the top,
 * then −337 / −273 delta) and gives no numbers.
 *
 * Honest limits: where the footprint is ESTIMATED from 1-minute bars, each minute's volume is called buying or
 * selling by where that minute closed, so a candle's delta mostly repeats its direction and the "flip" adds little
 * beyond a green/red candle; absorption then rests on volume and POC position, which are better measured (volume is
 * exact per minute). Only candles built from recorded ticks (`source: 'ticks'`) carry measured order flow.
 */

export type FlowSide = 'long' | 'short';

export interface Absorption {
  index: number;
  /** Candle volume ÷ median volume of the candles before it. */
  volRatio: number;
  /** Where the POC row's middle sits in the candle's range: 0 = at the low, 1 = at the high. */
  pocPos: number;
  /** Where the close sits in the range, same scale. */
  closePos: number;
  delta: number;
  /** All three conditions held. */
  ok: boolean;
  /** Why not, in words (empty when ok). */
  why: string[];
}

/** Candles the "usual volume" is the median of. */
export const VOL_LOOKBACK = 12;
/** The POC must sit in this outer fraction of the candle at the rejection end. */
export const POC_ZONE = 1 / 3;

function median(xs: number[]): number {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

/** Median volume of the `VOL_LOOKBACK` candles before `i` (nothing at or after `i`). */
export function usualVolume(bars: Bar[], i: number): number {
  const xs: number[] = [];
  for (let k = Math.max(0, i - VOL_LOOKBACK); k < i; k += 1) xs.push(bars[k]!.volume);
  return median(xs);
}

/** The POC row's middle price. */
export const pocPrice = (fp: FootprintCandle) => fp.levels[fp.poc]!.price + fp.step / 2;

/**
 * 4a: is candle `i` absorption for a `side` trade (long = sellers absorbed at a low, short = buyers at a high)?
 * Null when the candle has no footprint or no range.
 */
export function absorption(
  bars: Bar[],
  fp: FootprintCandle | null | undefined,
  i: number,
  side: FlowSide,
  absorbVol: number,
): Absorption | null {
  const b = bars[i];
  if (!b || !fp || !fp.levels.length) return null;
  const range = b.high - b.low;
  if (!(range > 0)) return null;
  const usual = usualVolume(bars, i);
  const vol = b.volume;
  const volRatio = usual > 0 ? vol / usual : 0;
  const pocPos = Math.min(1, Math.max(0, (pocPrice(fp) - b.low) / range));
  const closePos = (b.close - b.low) / range;
  const long = side === 'long';
  const why: string[] = [];
  if (!(volRatio >= absorbVol)) why.push(`volume ${volRatio.toFixed(1)}× usual (needs ${absorbVol}×)`);
  if (long ? pocPos > POC_ZONE : pocPos < 1 - POC_ZONE) why.push(`POC not at the ${long ? 'low' : 'high'} end`);
  if (long ? closePos < 0.5 : closePos > 0.5) why.push(`closed near the ${long ? 'low' : 'high'}`);
  return { index: i, volRatio, pocPos, closePos, delta: fp.delta, ok: why.length === 0, why };
}

/**
 * 4c: does candle `i` flip the pressure after absorption candle `a`? Needs its own footprint. `extreme` is the
 * extreme since the touch (a new one means the push continued).
 */
export function deltaFlip(
  bars: Bar[],
  fp: FootprintCandle | null | undefined,
  i: number,
  a: number,
  side: FlowSide,
  flipShare: number,
): { ok: boolean; share: number; why: string } | null {
  const b = bars[i];
  const ab = bars[a];
  if (!b || !ab || !fp || !(fp.total > 0)) return null;
  const long = side === 'long';
  const share = (long ? fp.delta : -fp.delta) / fp.total;
  if (!(share >= flipShare)) {
    return { ok: false, share, why: `delta ${fp.delta >= 0 ? '+' : '−'}${Math.abs(Math.round(fp.delta))} (${Math.round(share * 100)}% of volume on the ${long ? 'buy' : 'sell'} side, needs ${Math.round(flipShare * 100)}%)` };
  }
  if (long ? !(b.close > b.open && b.close > ab.close) : !(b.close < b.open && b.close < ab.close)) {
    return { ok: false, share, why: `delta flipped but the candle did not close ${long ? 'up, above' : 'down, below'} the absorption candle` };
  }
  return { ok: true, share, why: '' };
}

/**
 * 4d: the confirmation candle's low-volume node for the pull-back: the thinnest row strictly inside the candle
 * (its first and last rows are thin by nature) on the far side of the close (below it for a long). Ties go to the
 * row nearest the close, which a pull-back reaches first. Null when the candle has fewer than 3 rows there.
 */
export function lvnPrice(bar: Bar, fp: FootprintCandle | null | undefined, side: FlowSide): number | null {
  if (!fp || fp.levels.length < 3) return null;
  const long = side === 'long';
  let best: { price: number; vol: number; dist: number } | null = null;
  for (let k = 1; k < fp.levels.length - 1; k += 1) {
    const l = fp.levels[k]!;
    const mid = l.price + fp.step / 2;
    if (long ? mid > bar.close : mid < bar.close) continue;
    if (mid < bar.low || mid > bar.high) continue;
    const vol = l.ask + l.bid;
    const dist = Math.abs(bar.close - mid);
    if (!best || vol < best.vol || (vol === best.vol && dist < best.dist)) best = { price: mid, vol, dist };
  }
  return best?.price ?? null;
}
