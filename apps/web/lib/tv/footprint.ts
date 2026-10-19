import type { Bar, FootprintCandle, FootprintLevel } from './types';

/**
 * Volume footprint (bid × ask by price level) for each chart candle.
 *
 * ⚠ The data source serves 1-minute bars, not ticks, so true bid/ask volume is
 * not available. Each 1-minute bar's volume is spread across the price rows it
 * traded through (body-weighted) and split into buy/sell by where it closed in
 * its range. Totals per minute are exact; the per-row split is an estimate.
 */

const NICE = [1, 2, 2.5, 5, 10];

/** Round up to a 1-2-2.5-5 × 10ⁿ step so rows land on round prices. */
export function niceStep(raw: number): number {
  if (!(raw > 0)) return 0.01;
  const exp = Math.floor(Math.log10(raw));
  const base = 10 ** exp;
  const frac = raw / base;
  const pick = NICE.find((n) => n >= frac - 1e-9) ?? 10;
  return Number((pick * base).toFixed(Math.max(0, 6 - exp)));
}

/** Row height that gives a typical candle ~`rows` rows. */
export function autoStep(bars: Bar[], rows = 14): number {
  const ranges = bars
    .slice(-200)
    .map((b) => b.high - b.low)
    .filter((r) => r > 0)
    .sort((a, b) => a - b);
  if (!ranges.length) return 0.05;
  const median = ranges[Math.floor(ranges.length / 2)]!;
  return Math.max(0.01, niceStep(median / rows));
}

/** Step one rung up/down the 1-2-2.5-5 ladder. */
export function stepLadder(step: number, direction: 1 | -1): number {
  const ladder: number[] = [];
  for (let exp = -3; exp <= 4; exp += 1) {
    for (const n of [1, 2, 2.5, 5]) ladder.push(Number((n * 10 ** exp).toFixed(4)));
  }
  let at = 0;
  ladder.forEach((v, i) => {
    if (Math.abs(v - step) < Math.abs(ladder[at]! - step)) at = i;
  });
  return ladder[Math.min(ladder.length - 1, Math.max(0, at + direction))]!;
}

const rowIndex = (price: number, step: number) => Math.floor(price / step + 1e-9);

function spread(bar: Bar, step: number, into: Map<number, FootprintLevel>) {
  if (bar.volume <= 0) return;
  const lo = rowIndex(bar.low, step);
  const hi = rowIndex(bar.high, step);
  const bodyLo = rowIndex(Math.min(bar.open, bar.close), step);
  const bodyHi = rowIndex(Math.max(bar.open, bar.close), step);
  const rows = hi - lo + 1;
  const bodyRows = bodyHi - bodyLo + 1;
  const wickRows = rows - bodyRows;
  // 70% of volume sits in the body rows, 30% in the wicks (all of it, if there are no wicks).
  const bodyShare = wickRows > 0 ? 0.7 : 1;
  const range = bar.high - bar.low;
  const buyShare = range > 0 ? Math.min(0.95, Math.max(0.05, 0.5 + 0.45 * ((bar.close - bar.open) / range))) : 0.5;
  for (let r = lo; r <= hi; r += 1) {
    const inBody = r >= bodyLo && r <= bodyHi;
    const weight = inBody ? bodyShare / bodyRows : wickRows > 0 ? (1 - bodyShare) / wickRows : 0;
    const vol = bar.volume * weight;
    let level = into.get(r);
    if (!level) {
      level = { price: r * step, bid: 0, ask: 0 };
      into.set(r, level);
    }
    level.ask += vol * buyShare;
    level.bid += vol * (1 - buyShare);
  }
}

/**
 * One FootprintCandle per chart candle (null where no sub-bars cover it).
 * `chartBars` need `time`; `subBars` are 1-minute bars; `candleSecs` is the chart timeframe.
 */
export function buildFootprints(
  chartBars: Bar[],
  subBars: Bar[],
  candleSecs: number,
  step: number,
): (FootprintCandle | null)[] {
  const result: (FootprintCandle | null)[] = new Array(chartBars.length).fill(null);
  if (!subBars.length || !chartBars.length) return result;
  let s = 0;
  for (let i = 0; i < chartBars.length; i += 1) {
    const start = chartBars[i]!.time;
    const end = i + 1 < chartBars.length ? Math.min(chartBars[i + 1]!.time, start + candleSecs) : start + candleSecs;
    while (s < subBars.length && subBars[s]!.time < start) s += 1;
    const rows = new Map<number, FootprintLevel>();
    let e = s;
    while (e < subBars.length && subBars[e]!.time < end) {
      spread(subBars[e]!, step, rows);
      e += 1;
    }
    if (!rows.size) continue;
    const levels = [...rows.entries()].sort(([a], [b]) => a - b).map(([, level]) => level);
    let poc = 0;
    let delta = 0;
    let total = 0;
    levels.forEach((level, idx) => {
      delta += level.ask - level.bid;
      total += level.ask + level.bid;
      if (level.ask + level.bid > levels[poc]!.ask + levels[poc]!.bid) poc = idx;
    });
    result[i] = { poc, levels, delta, total, step };
  }
  return result;
}
