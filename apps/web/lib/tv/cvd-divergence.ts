import type { CvdCandle } from './cvd';
import type { Bar } from './types';

/**
 * Price / CVD divergence, read the way a trader reads it off the chart:
 *
 *   1. Find swing highs and swing lows of PRICE (a pivot: `pivot` candles on each side that are lower / higher).
 *   2. Read the CVD at that swing: its high for a swing high, its low for a swing low (the extreme of the CVD
 *      candle, within `tol` candles of the swing, since CVD often peaks a candle early or late).
 *   3. Compare each swing with the PREVIOUS swing of the same kind, if it is not further back than `maxGap` candles
 *      and sits in the same CVD anchor period (the running total restarts at zero, so a pair that straddles a reset
 *      compares two different baselines).
 *
 *        regular bearish  price higher high  · CVD lower high   → buyers exhausted, reversal down
 *        hidden  bearish  price lower high   · CVD higher high  → sellers absorbed at a lower high, trend down resumes
 *        regular bullish  price lower low    · CVD higher low   → sellers exhausted, reversal up
 *        hidden  bullish  price higher low   · CVD lower low    → buyers absorbed at a higher low, trend up resumes
 *
 *   4. A divergence is a WARNING. It is `confirmed` only once a candle CLOSES through the opposite swing between the
 *      two pivots (above the intervening high for a bullish one, below the intervening low for a bearish one), within
 *      `confirmWithin` candles of the second swing becoming known. It `failed` when a candle closes beyond the second
 *      swing's own extreme first (the move the divergence warned against kept going), or the window ends.
 *
 * No look-ahead: a swing is only known `pivot` candles after it, so `knownIndex` is when the divergence could first
 * be drawn; running this on a prefix of the candles gives exactly that prefix of the results.
 *
 * ⚠ The CVD is an estimate built from bars (lib/tv/cvd.ts), and a divergence says nothing about timing or whether
 * it resolves: they persist and fail regularly. This marks them; it does not say they pay.
 */

export interface CvdDivConfig {
  /** Candles on each side that a swing must clear. */
  pivot: number;
  /** The two swings must be at most this many candles apart (and, with the pivot, at least `pivot` + 1). */
  maxGap: number;
  /** The CVD extreme is the highest/lowest within this many candles of the price swing. */
  tol: number;
  /** Price must differ by at least this percent between the two swings (equal highs are a double top, not a divergence). */
  minPricePct: number;
  /** The CVD must differ by at least this percent of its range across the two swings. */
  minCvdPct: number;
  /** A confirming close must come within this many candles after the second swing is known. */
  confirmWithin: number;
  /** Show the two hidden kinds as well as the regular ones. */
  hidden: boolean;
}

export const CVD_DIV_DEFAULTS: CvdDivConfig = {
  pivot: 3,
  maxGap: 30,
  tol: 1,
  minPricePct: 0.05,
  minCvdPct: 5,
  confirmWithin: 10,
  hidden: false,
};

export type CvdDivKind = 'regular-bullish' | 'regular-bearish' | 'hidden-bullish' | 'hidden-bearish';
export type CvdDivStatus = 'confirmed' | 'pending' | 'failed';

export interface CvdDivergence {
  kind: CvdDivKind;
  bullish: boolean;
  hidden: boolean;
  /** Candle indexes of the earlier and later swing. */
  i1: number;
  i2: number;
  /** Price at each swing (high for swing highs, low for swing lows). */
  p1: number;
  p2: number;
  /** CVD read at each swing, and the candle it was read from. */
  c1: number;
  c2: number;
  ci1: number;
  ci2: number;
  /** First candle on which this could be drawn: the second swing plus `pivot` candles. */
  knownIndex: number;
  status: CvdDivStatus;
  /** The candle that closed through the intervening swing, when confirmed. */
  confirmIndex: number | null;
  /** The intervening swing that has to be broken: the highest high (bullish) / lowest low (bearish) between the swings. */
  level: number;
}

interface Pivot {
  index: number;
  price: number;
  cvd: number;
  cvdIndex: number;
}

/** Whether candle `i` is a swing high (`high`) or swing low, `n` candles each side; ties on the left count, on the right do not. */
function isPivot(bars: Bar[], i: number, n: number, high: boolean): boolean {
  const v = high ? bars[i]!.high : bars[i]!.low;
  for (let k = 1; k <= n; k += 1) {
    const l = high ? bars[i - k]!.high : bars[i - k]!.low;
    const r = high ? bars[i + k]!.high : bars[i + k]!.low;
    if (high ? l > v || r >= v : l < v || r <= v) return false;
  }
  return true;
}

/**
 * Divergences among `bars`, oldest first. `cvd[i]` is the CVD candle of `bars[i]`; `anchors[i]` the key of its anchor
 * period; `usable[i]` false skips a candle as a swing (no volume under it, or too few intrabars to trust the delta).
 */
export function findCvdDivergences(
  bars: Bar[],
  cvd: CvdCandle[],
  anchors: string[],
  usable: boolean[] = [],
  cfg: CvdDivConfig = CVD_DIV_DEFAULTS,
): CvdDivergence[] {
  const n = Math.max(1, Math.floor(cfg.pivot));
  const out: CvdDivergence[] = [];
  if (bars.length < 2 * n + 2 || cvd.length !== bars.length) return out;

  /** The CVD extreme next to a swing, within the same anchor period. */
  const cvdAt = (i: number, high: boolean): { v: number; at: number } | null => {
    let best: { v: number; at: number } | null = null;
    for (let k = Math.max(0, i - cfg.tol); k <= Math.min(bars.length - 1, i + cfg.tol); k += 1) {
      const c = cvd[k]!;
      if (c.missing || usable[k] === false || anchors[k] !== anchors[i]) continue;
      const v = high ? c.high : c.low;
      if (!best || (high ? v > best.v : v < best.v)) best = { v, at: k };
    }
    return best;
  };

  for (const high of [true, false]) {
    let prev: Pivot | null = null;
    for (let i = n; i < bars.length - n; i += 1) {
      if (usable[i] === false || cvd[i]!.missing || !isPivot(bars, i, n, high)) continue;
      const at = cvdAt(i, high);
      if (!at) continue;
      const cur: Pivot = { index: i, price: high ? bars[i]!.high : bars[i]!.low, cvd: at.v, cvdIndex: at.at };
      const before = prev;
      prev = cur;
      if (!before || anchors[before.index] !== anchors[i]) continue;
      const gap = i - before.index;
      if (gap > cfg.maxGap) continue;

      const dPrice = cur.price - before.price;
      if (Math.abs(dPrice) < (Math.abs(before.price) * cfg.minPricePct) / 100) continue;
      const dCvd = cur.cvd - before.cvd;
      let lo = Infinity;
      let hi = -Infinity;
      for (let k = before.index; k <= i; k += 1) {
        if (cvd[k]!.missing) continue;
        lo = Math.min(lo, cvd[k]!.low);
        hi = Math.max(hi, cvd[k]!.high);
      }
      if (!(hi > lo) || Math.abs(dCvd) < ((hi - lo) * cfg.minCvdPct) / 100) continue;

      const priceUp = dPrice > 0;
      const cvdUp = dCvd > 0;
      if (priceUp === cvdUp) continue; // they agree
      // highs: price up + CVD down = regular bearish; price down + CVD up = hidden bearish
      // lows:  price down + CVD up = regular bullish; price up + CVD down = hidden bullish
      const hidden = high ? !priceUp : priceUp;
      if (hidden && !cfg.hidden) continue;
      const bullish = !high;
      const kind: CvdDivKind = `${hidden ? 'hidden' : 'regular'}-${bullish ? 'bullish' : 'bearish'}`;

      // the swing a human watches for the break: the extreme of the opposite side between the two swings
      let level = high ? Infinity : -Infinity;
      for (let k = before.index; k <= i; k += 1) {
        level = high ? Math.min(level, bars[k]!.low) : Math.max(level, bars[k]!.high);
      }

      const knownIndex = i + n;
      let status: CvdDivStatus = 'pending';
      let confirmIndex: number | null = null;
      const last = Math.min(bars.length - 1, knownIndex + cfg.confirmWithin);
      for (let k = i + 1; k <= last; k += 1) {
        const close = bars[k]!.close;
        // a close through the level counts only once the swing is known (a human could not have drawn it earlier)
        if (k >= knownIndex && (high ? close < level : close > level)) {
          status = 'confirmed';
          confirmIndex = k;
          break;
        }
        // the move the divergence warned against kept going: closed beyond the second swing's own extreme
        if (high ? close > cur.price : close < cur.price) {
          status = 'failed';
          break;
        }
      }
      if (status === 'pending' && knownIndex + cfg.confirmWithin <= bars.length - 1) status = 'failed';

      out.push({
        kind,
        bullish,
        hidden,
        i1: before.index,
        i2: i,
        p1: before.price,
        p2: cur.price,
        c1: before.cvd,
        c2: cur.cvd,
        ci1: before.cvdIndex,
        ci2: cur.cvdIndex,
        knownIndex,
        status,
        confirmIndex,
        level,
      });
    }
  }
  return out.sort((a, b) => a.i2 - b.i2);
}

/**
 * A usable flag per chart candle: false when fewer than `min` intrabars sit under it (or none are loaded that far
 * back), where the CVD's buy/sell split is just the candle's own colour (the warning TradingView CVD scripts give
 * below ~5 intrabars). Divergence between price and a CVD built from price's own colour means nothing.
 */
export function intrabarUsable(bars: Bar[], sub: Bar[], secs: number, min = 5): boolean[] {
  const out: boolean[] = [];
  let j = 0;
  for (const b of bars) {
    while (j < sub.length && sub[j]!.time < b.time) j += 1;
    let n = 0;
    const end = b.time + secs;
    for (let k = j; k < sub.length && sub[k]!.time < end; k += 1) n += 1;
    // older than every intrabar loaded: the CVD there is the candle's own colour times its volume, not order flow
    out.push(sub.length > 0 && b.time >= sub[0]!.time && n >= min);
  }
  return out;
}

/** A delta-divergence setup, as far as linking needs it: the chart candle it sits on, its confirming candle, which way it points. */
export interface SetupRef {
  chartIndex: number;
  confirmChartIndex: number | null;
  /** buyers trapped = a short setup; sellers trapped = a long setup. */
  kind: 'buyers' | 'sellers';
}

/**
 * Index into `setups` of a delta-divergence setup pointing the same way as `d` (long setup for bullish, short for
 * bearish) whose signal or confirming candle is within `window` candles of the divergence's second swing, or null.
 */
export function nearSetup(d: CvdDivergence, setups: SetupRef[], window: number): number | null {
  for (let k = 0; k < setups.length; k += 1) {
    const s = setups[k]!;
    if (s.kind !== (d.bullish ? 'sellers' : 'buyers')) continue;
    const at = [s.chartIndex, s.confirmChartIndex].filter((x): x is number => x != null);
    if (at.some((x) => Math.abs(x - d.i2) <= window)) return k;
  }
  return null;
}
