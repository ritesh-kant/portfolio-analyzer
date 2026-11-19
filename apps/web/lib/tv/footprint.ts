import { estimateFromBars } from './bigtrades.ts';
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

/** Wilder ATR of the last bar (same maths as indicators.atr), null until `length` bars exist. */
function wilderAtr(bars: Bar[], length: number): number | null {
  if (bars.length < length) return null;
  const tr = bars.map((b, i) =>
    i === 0
      ? b.high - b.low
      : Math.max(
          b.high - b.low,
          Math.abs(b.high - bars[i - 1]!.close),
          Math.abs(b.low - bars[i - 1]!.close),
        ),
  );
  let atr = 0;
  for (let i = 0; i < length; i += 1) atr += tr[i]!;
  atr /= length;
  for (let i = length; i < bars.length; i += 1) atr = (atr * (length - 1) + tr[i]!) / length;
  return atr;
}

/**
 * Row height from ATR(14), like TradingView's "ATR 14" row size: one candle's typical range spans about `rows`
 * rows, and the size follows volatility smoothly instead of jumping with the median. Short series fall back
 * to the median candle range.
 */
export function autoStep(bars: Bar[], rows = 14): number {
  const recent = bars.slice(-200);
  const last = wilderAtr(recent, 14);
  if (last != null && last > 0) return Math.max(0.01, niceStep(last / rows));
  const ranges = recent
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
  const buyShare =
    range > 0 ? Math.min(0.95, Math.max(0.05, 0.5 + 0.45 * ((bar.close - bar.open) / range))) : 0.5;
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
 * Yahoo reports the minute still forming with volume 0, and its 1-minute bars lag the candle's own volume
 * (a 5-minute candle 2 minutes in: 11.5K on the candle, 6.3K across its minutes). Without this the newest
 * candle has no footprint for its first minute and never shows its latest prices. The volume the minutes
 * have not reported yet goes on the forming minute's range, widened to any part of the candle the reported
 * minutes never reached (the 5-minute candle runs ahead of the 1-minute bars), or the whole candle before
 * any minute exists.
 */
function unreported(candle: Bar, subs: Bar[]): Bar | null {
  const reported = subs.filter((b) => b.volume > 0);
  const missing = candle.volume - reported.reduce((sum, b) => sum + b.volume, 0);
  if (!(missing > 0)) return null;
  if (!reported.length) return { ...candle, volume: missing };
  const forming = [...subs].reverse().find((b) => b.volume <= 0);
  const open = forming?.open ?? reported[reported.length - 1]!.close;
  const close = forming?.close ?? candle.close;
  const reachedHigh = Math.max(...reported.map((b) => b.high));
  const reachedLow = Math.min(...reported.map((b) => b.low));
  return {
    time: candle.time,
    open,
    close,
    high: Math.max(
      open,
      close,
      forming?.high ?? close,
      candle.high > reachedHigh ? candle.high : -Infinity,
    ),
    low: Math.min(
      open,
      close,
      forming?.low ?? close,
      candle.low < reachedLow ? candle.low : Infinity,
    ),
    volume: missing,
  };
}

/**
 * One FootprintCandle per chart candle (null where no sub-bars cover it).
 * `chartBars` need `time`; `subBars` are 1-minute bars; `candleSecs` is the chart timeframe.
 * `fillLast`: the newest candle also gets the volume its 1-minute bars have not reported yet (live charts).
 */
export function buildFootprints(
  chartBars: Bar[],
  subBars: Bar[],
  candleSecs: number,
  step: number,
  fillLast = false,
): (FootprintCandle | null)[] {
  const result: (FootprintCandle | null)[] = new Array(chartBars.length).fill(null);
  if (!subBars.length || !chartBars.length) return result;
  let s = 0;
  for (let i = 0; i < chartBars.length; i += 1) {
    const start = chartBars[i]!.time;
    const end =
      i + 1 < chartBars.length
        ? Math.min(chartBars[i + 1]!.time, start + candleSecs)
        : start + candleSecs;
    while (s < subBars.length && subBars[s]!.time < start) s += 1;
    const rows = new Map<number, FootprintLevel>();
    let e = s;
    while (e < subBars.length && subBars[e]!.time < end) {
      spread(subBars[e]!, step, rows);
      e += 1;
    }
    if (fillLast && i === chartBars.length - 1) {
      const extra = unreported(chartBars[i]!, subBars.slice(s, e));
      if (extra) spread(extra, step, rows);
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

/** A minute whose volume spiked inside a chart candle: the price span it traded through, its volume and side. */
export interface FootprintSweep {
  low: number;
  high: number;
  volume: number;
  side: 'buy' | 'sell';
}

/**
 * ⚠ ESTIMATE. Order-flow platforms mark a large aggressive order that swept several price levels: the levels it
 * traded through, coloured by side, with its size in a bubble. That needs the trade tape, which NQ has no source for
 * here (Yahoo 1-minute bars). The stand-in is the Big Trades estimate (lib/tv/bigtrades.ts): a 1-minute bar with at
 * least `spikeX` × the median volume of the previous hour, sided by its candle. Its span is that minute's low–high and
 * its size the whole minute's volume, which may be one order or many. Only the candle's biggest such minute is kept.
 * On NQ 5m, 4× marks ~8% of candles. 0 = off.
 */
export function footprintSweeps(
  chartTimes: number[],
  candleSecs: number,
  subBars: Bar[],
  spikeX: number,
): FootprintSweep[][] {
  const out = chartTimes.map(() => [] as FootprintSweep[]);
  if (!(spikeX > 0) || !chartTimes.length) return out;
  const byTime = new Map(subBars.map((b) => [b.time, b]));
  let i = 0;
  for (const spike of estimateFromBars(subBars, spikeX)) {
    const t = spike.t / 1000;
    const bar = byTime.get(t);
    if (!bar) continue;
    while (i + 1 < chartTimes.length && chartTimes[i + 1]! <= t) i += 1;
    const start = chartTimes[i]!;
    const end =
      i + 1 < chartTimes.length
        ? Math.min(chartTimes[i + 1]!, start + candleSecs)
        : start + candleSecs;
    if (t < start || t >= end) continue;
    // One per candle, its biggest minute: at the US open every minute beats the quiet hour before it, and five
    // overlapping blocks would paint the whole candle.
    const sweep: FootprintSweep = {
      low: bar.low,
      high: bar.high,
      volume: spike.q,
      side: spike.side === 'sell' ? 'sell' : 'buy',
    };
    const cur = out[i]![0];
    if (!cur || sweep.volume > cur.volume) out[i] = [sweep];
  }
  return out;
}
