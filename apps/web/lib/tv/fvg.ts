import type { Bar } from './types';

/**
 * Volumetric fair value gaps, after Flux Charts' "Fair Value Gaps Volumetric" / the FVG module of its Price
 * Action Toolkit (a closed-source TradingView script, rebuilt here from its documentation and a walkthrough video,
 * so thresholds and edge cases are ours, not a port).
 *
 *   A gap is three candles c1, c2 (the impulse), c3:
 *     bullish  c1.high < c3.low   → zone [c1.high, c3.low]
 *     bearish  c1.low  > c3.high  → zone [c3.high, c1.low]
 *   The zone is open until price invalidates it: a candle that CLOSES through the far side (`close`) or whose
 *   wick reaches it (`wick`). Before that it is 'tested' once any later candle has traded back into it.
 *
 *   Volume: the three candles' volume is split into the part that drove the gap (c2 + c3) and the part that sat
 *   against it (c1). `against` is that against-part as a share of the total: low = the move had the volume behind
 *   it, high = the gap was formed with little participation and is the likelier to fail on the way back.
 *
 * ⚠ Volume comes from the chart's own candles. Bars with no volume (some indices and FX) give no split: `against`
 * is null and the volume filter keeps the gap. A gap is judged on its third candle, so on the live candle it can
 * still change until that candle closes.
 */

export interface FvgConfig {
  /** 'close': a candle has to close through the far side of the zone; 'wick': touching it is enough. */
  invalidation: 'close' | 'wick';
  /** What decides a gap is worth listing: its size against ATR, its volume against the recent average, or nothing. */
  filter: 'atr' | 'volume' | 'off';
  /** filter 'atr': the gap must be at least this many ATRs (14) tall. */
  atrMult: number;
  /** filter 'volume': the three candles' volume must be at least this percent of three average candles (prior 20). */
  volumeThreshold: number;
  /** 'same': c1, c2 and c3 all point the gap's way; 'all': candles of either colour may form it. */
  detection: 'same' | 'all';
}

export const FVG_DEFAULTS: FvgConfig = {
  invalidation: 'close',
  filter: 'volume',
  atrMult: 0.5,
  volumeThreshold: 100,
  detection: 'same',
};

export type FvgStatus = 'open' | 'tested' | 'invalidated';

export interface Fvg {
  /** Index of c3, the candle that completes the gap (where the gap is known), and its start time (unix s). */
  index: number;
  time: number;
  bullish: boolean;
  /** Zone edges: top > bottom. */
  top: number;
  bottom: number;
  /** Volume of the three candles, and the part against the gap as a percent of it; null when no candle had volume. */
  volume: number;
  against: number | null;
  status: FvgStatus;
  /** First later candle that traded into the zone / invalidated it; null when none did. */
  testedIndex: number | null;
  invalidIndex: number | null;
}

/** ATR length the 'atr' filter expects in `atrs` (indicators.ts `atr(bars, 14)`). */
export const FVG_ATR_LEN = 14;
const VOL_LOOKBACK = 20;
const VOL_MIN_PRIOR = 10;

/** `atrs[i]` is the ATR at candle i (null until it exists); only read by the 'atr' filter. */
export function findFvgs(bars: Bar[], cfg: FvgConfig = FVG_DEFAULTS, atrs: (number | null)[] = []): Fvg[] {
  const out: Fvg[] = [];
  for (let i = 2; i < bars.length; i += 1) {
    const c1 = bars[i - 2]!;
    const c2 = bars[i - 1]!;
    const c3 = bars[i]!;
    const bullish = c1.high < c3.low;
    const bearish = c1.low > c3.high;
    if (!bullish && !bearish) continue;
    if (cfg.detection === 'same') {
      const up = (b: Bar) => b.close > b.open;
      const down = (b: Bar) => b.close < b.open;
      if (bullish ? !(up(c1) && up(c2) && up(c3)) : !(down(c1) && down(c2) && down(c3))) continue;
    }
    const top = bullish ? c3.low : c1.low;
    const bottom = bullish ? c1.high : c3.high;
    const volume = c1.volume + c2.volume + c3.volume;
    const hasVolume = volume > 0;

    if (cfg.filter === 'atr') {
      // no ATR yet (fewer than 14 candles): nothing to judge it against, so keep it
      const a = atrs[i - 1];
      if (a != null && top - bottom < cfg.atrMult * a) continue;
    } else if (cfg.filter === 'volume' && hasVolume) {
      const from = Math.max(0, i - 2 - VOL_LOOKBACK);
      if (i - 2 - from >= VOL_MIN_PRIOR) {
        let sum = 0;
        for (let j = from; j < i - 2; j += 1) sum += bars[j]!.volume;
        const mean = sum / (i - 2 - from);
        if (mean > 0 && volume < (cfg.volumeThreshold / 100) * 3 * mean) continue;
      }
    }

    let testedIndex: number | null = null;
    let invalidIndex: number | null = null;
    for (let j = i + 1; j < bars.length; j += 1) {
      const n = bars[j]!;
      const through = bullish
        ? cfg.invalidation === 'close' ? n.close < bottom : n.low < bottom
        : cfg.invalidation === 'close' ? n.close > top : n.high > top;
      if (through) {
        invalidIndex = j;
        break;
      }
      if (testedIndex == null && (bullish ? n.low <= top : n.high >= bottom)) testedIndex = j;
    }
    out.push({
      index: i,
      time: c3.time,
      bullish,
      top,
      bottom,
      volume,
      against: hasVolume ? (c1.volume / volume) * 100 : null,
      status: invalidIndex != null ? 'invalidated' : testedIndex != null ? 'tested' : 'open',
      testedIndex,
      invalidIndex,
    });
  }
  return out;
}
