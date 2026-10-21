import type { Bar } from './types';

/**
 * Absorption setups on 15-minute candles, as taught in the order-flow video this panel was built from
 * (Booming Bulls, "How to Use Order Flow in Trading?"):
 *
 *   1. SIGNAL candle with a dominant wick and a delta that points INTO that wick:
 *        buyers trapped  — long UPPER wick, positive delta (buyers pushed at the high and were rejected)
 *        sellers trapped — long LOWER wick, negative delta (sellers pushed at the low and were rejected)
 *      The body's colour does not matter; the wick has to be at least as big as the body.
 *   2. CONFIRMATION: a later candle (within `within` candles) that breaks the signal candle's extreme the
 *      other way with a body and a delta that agree:
 *        buyers trapped  → a red candle with negative delta whose low is below the signal candle's low (short)
 *        sellers trapped → a green candle with positive delta whose high is above the signal candle's high (long)
 *      A candle that closes beyond the signal candle's wick tip first means the idea failed.
 *
 * The delta is the one the chart's footprint shows for that candle (lib/tv/footprint.ts): each 1-minute bar's
 * volume is split into buys and sells by that minute's own body, and the minutes are summed.
 *
 * ⚠ The data source serves 1-minute bars, not trades, so the delta is an estimate, not true order flow, and the
 * footprint only reaches back about 7 sessions. The video also wants the signal at a point of interest (order
 * block, supply/demand zone, value-area edge, point of control); that location check is not automated here.
 */

export interface DeltaConfig {
  /** The dominant wick must be at least this percent of the candle's high-low range. */
  wick: number;
  /** Skip candles whose |delta| is below this percent of the candle's volume. */
  delta: number;
  /** Minimum volume over the mean of the 20 candles before it; 0 flags every setup. */
  rvol: number;
  /** A confirming candle must come within this many candles after the signal candle. */
  within: number;
  /** Show setups the market did not follow through on (expired or failed); applied by the caller. */
  missed: boolean;
}

export const DELTA_DEFAULTS: DeltaConfig = { wick: 40, delta: 5, rvol: 1, within: 3, missed: false };

export type DeltaKind = 'buyers' | 'sellers';
export type DeltaStatus = 'confirmed' | 'pending' | 'missed';

export interface DeltaDivergence {
  /** Index of the signal candle in the candles passed in. */
  index: number;
  /** Signal candle start, unix seconds. */
  time: number;
  /** Which side was trapped: buyers = upper wick with positive delta, sellers = lower wick with negative delta. */
  kind: DeltaKind;
  /** Signal candle's high and low: the confirmation has to break the low (buyers trapped) or the high (sellers). */
  high: number;
  low: number;
  /** The dominant wick as a percent of the candle's range. */
  wick: number;
  /** Footprint delta (buys − sells) of the signal candle, in shares/contracts. */
  delta: number;
  /** |delta| as a percent of the candle's volume. */
  pct: number;
  /** Candle volume over the prior-20-candle mean; null until ten prior candles exist. */
  rvol: number | null;
  status: DeltaStatus;
  /** The confirming candle, when status is 'confirmed'. */
  confirmIndex: number | null;
  confirmTime: number | null;
}

/** A setup located on the chart: the bars the badge and the confirmation go on. */
export interface PlacedDivergence extends DeltaDivergence {
  chartIndex: number;
  confirmChartIndex: number | null;
}

const LOOKBACK = 20;
const MIN_PRIOR = 10;

/**
 * Setups among `candles`, oldest first. `deltas[i]` is candle i's footprint delta (null where the footprint
 * has no 1-minute data under it, which skips the candle as a signal and as a confirmation).
 */
export function findDeltaDivergences(
  candles: Bar[],
  deltas: (number | null)[],
  cfg: DeltaConfig = DELTA_DEFAULTS,
): DeltaDivergence[] {
  const out: DeltaDivergence[] = [];
  for (let i = 0; i < candles.length; i += 1) {
    const c = candles[i]!;
    const delta = deltas[i];
    const range = c.high - c.low;
    if (delta == null || !(c.volume > 0) || !(range > 0)) continue;
    const body = Math.abs(c.close - c.open);
    const upper = c.high - Math.max(c.open, c.close);
    const lower = Math.min(c.open, c.close) - c.low;
    // the dominant wick, and the delta that has to point into it
    const kind: DeltaKind | null =
      upper > lower && upper >= body && delta > 0
        ? 'buyers'
        : lower > upper && lower >= body && delta < 0
          ? 'sellers'
          : null;
    if (!kind) continue;
    const wick = ((kind === 'buyers' ? upper : lower) / range) * 100;
    if (wick < cfg.wick) continue;
    const pct = (Math.abs(delta) / c.volume) * 100;
    if (pct < cfg.delta) continue;
    const from = Math.max(0, i - LOOKBACK);
    let rvol: number | null = null;
    if (i - from >= MIN_PRIOR) {
      let sum = 0;
      for (let j = from; j < i; j += 1) sum += candles[j]!.volume;
      const mean = sum / (i - from);
      rvol = mean > 0 ? c.volume / mean : null;
    }
    // No baseline yet: nothing to judge it against, so keep it.
    if (rvol != null && rvol < cfg.rvol) continue;

    let status: DeltaStatus = 'pending';
    let confirmIndex: number | null = null;
    const last = Math.min(candles.length - 1, i + cfg.within);
    for (let j = i + 1; j <= last; j += 1) {
      const n = candles[j]!;
      const d = deltas[j];
      // closing beyond the wick tip: the trapped side won after all
      if (kind === 'buyers' ? n.close > c.high : n.close < c.low) {
        status = 'missed';
        break;
      }
      if (d == null) continue;
      const breaks = kind === 'buyers' ? n.low < c.low : n.high > c.high;
      const agrees = kind === 'buyers' ? n.close < n.open && d < 0 : n.close > n.open && d > 0;
      if (breaks && agrees) {
        status = 'confirmed';
        confirmIndex = j;
        break;
      }
    }
    // not confirmed and the window has fully played out: no follow-through
    if (status === 'pending' && i + cfg.within <= candles.length - 1) status = 'missed';
    out.push({
      index: i,
      time: c.time,
      kind,
      high: c.high,
      low: c.low,
      wick,
      delta,
      pct,
      rvol,
      status,
      confirmIndex,
      confirmTime: confirmIndex == null ? null : candles[confirmIndex]!.time,
    });
  }
  return out;
}

/**
 * Index of the chart bar (of `secs` seconds, start times ascending in `times`) that contains instant `t`,
 * or -1 when `t` falls outside every bar.
 */
export function barContaining(times: number[], t: number, secs: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let at = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid]! <= t) {
      at = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return at >= 0 && t < times[at]! + secs ? at : -1;
}
