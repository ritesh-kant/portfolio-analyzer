import { atr } from './indicators.ts';
import { aggregateBars, sessionDay, wallClock } from './timeframes.ts';
import type { Bar } from './types';
import { buildProfile } from './volume-profile.ts';

/**
 * The "Auction Resolution" (AR) model's mechanical pieces, from the course in
 * research/specs/orderflow-course-ar-model.md. Pure functions over bars, so the chart and the backtest
 * (phase 3) share one implementation.
 *
 * Every value carries the candle index at which it became KNOWN where that matters (a swing is only a swing once
 * price has moved away from it), so the backtest can replay it without look-ahead. The chart simply draws the
 * latest state.
 */

// ── sessions ────────────────────────────────────────────────────────────────────

export type SessionKey = 'asia' | 'london' | 'ny';

export interface SessionSpec {
  key: SessionKey;
  label: string;
  /** Minutes after midnight in the exchange's time zone; `end` is exclusive. start > end crosses midnight. */
  start: number;
  end: number;
}

/**
 * Defaults in New York time. He never states his windows; these are the common ICT "kill zones" and his own
 * trading window (the first 90 minutes after the 09:30 open). Editable per chart later.
 */
export const AR_SESSIONS: SessionSpec[] = [
  { key: 'asia', label: 'Asia', start: 20 * 60, end: 24 * 60 },
  { key: 'london', label: 'London', start: 2 * 60, end: 5 * 60 },
  { key: 'ny', label: 'New York', start: 9 * 60 + 30, end: 11 * 60 },
];

export interface SessionBox {
  key: SessionKey;
  label: string;
  /** Wall-clock date (YYYY-MM-DD) the session STARTED on. */
  day: string;
  from: number;
  to: number;
  high: number;
  low: number;
  /** Still forming: the last chart candle is inside it. */
  open: boolean;
}

const minuteOfDay = (wall: number) => Math.floor((((wall % 86400) + 86400) % 86400) / 60);

function inSession(m: number, s: SessionSpec): boolean {
  return s.start < s.end ? m >= s.start && m < s.end : m >= s.start || m < s.end;
}

/** One box per session occurrence on the chart's candles (intraday only). */
export function sessionBoxes(bars: Bar[], timeZone: string, specs: SessionSpec[] = AR_SESSIONS): SessionBox[] {
  const out: SessionBox[] = [];
  const open = new Map<SessionKey, SessionBox>();
  bars.forEach((b, i) => {
    const wall = wallClock(b.time, timeZone);
    const m = minuteOfDay(wall);
    for (const s of specs) {
      if (!inSession(m, s)) continue;
      // a session that crosses midnight belongs to the day it started on
      const startWall = s.start > s.end && m < s.end ? wall - 86400 : wall;
      const day = new Date(startWall * 1000).toISOString().slice(0, 10);
      let box = open.get(s.key);
      if (!box || box.day !== day) {
        box = { key: s.key, label: s.label, day, from: i, to: i, high: b.high, low: b.low, open: false };
        open.set(s.key, box);
        out.push(box);
      }
      box.to = i;
      box.high = Math.max(box.high, b.high);
      box.low = Math.min(box.low, b.low);
    }
  });
  const last = bars[bars.length - 1];
  if (last) {
    const m = minuteOfDay(wallClock(last.time, timeZone));
    for (const s of specs) {
      const box = open.get(s.key);
      if (box && box.to === bars.length - 1 && inSession(m, s)) box.open = true;
    }
  }
  return out.sort((a, b) => a.from - b.from);
}

// ── Asia-range volatility levels ("AVR") ────────────────────────────────────────

/**
 * His projection unit as a share of the 20-day daily ATR. Read off his indicator's own table on two days:
 * ATR 278.74 → up 41.81 (15.0 %), ATR 485.69 → up 63.0 (13.0 %); the down unit was 1.067 × up both times.
 * Interpolated linearly between those readings and clamped, which mimics his "volatility normalization".
 * Approximate: a reconstruction, not his formula.
 */
export function avrUnits(dailyAtr: number): { up: number; down: number } {
  const [a1, s1] = [278.74, 41.81 / 278.74];
  const [a2, s2] = [485.69, 63.0 / 485.69];
  const share = Math.min(0.16, Math.max(0.12, s1 + ((dailyAtr - a1) * (s2 - s1)) / (a2 - a1)));
  const up = dailyAtr * share;
  return { up, down: up * (44.6 / 41.81) };
}

export type AvrLevelKey = 'l1' | 'avrMinus' | 'avr' | 'avrPlus' | 'max';

/** Distance from the Asia high (up) / low (down) in projection units, measured off his chart (± ~5 points). */
export const AVR_LEVELS: { key: AvrLevelKey; label: string; at: number; band: number }[] = [
  { key: 'l1', label: 'Level 1', at: 0.5, band: 0.25 },
  { key: 'avrMinus', label: 'AVR−', at: 1.25, band: 0 },
  { key: 'avr', label: 'AVR', at: 2.5, band: 0.25 },
  { key: 'avrPlus', label: 'AVR+', at: 4.5, band: 0.25 },
  { key: 'max', label: 'Max', at: 6.5, band: 0.25 },
];

export interface AvrLevel {
  key: AvrLevelKey;
  label: string;
  side: 'up' | 'down';
  /** Band edges (equal for a line). */
  lo: number;
  hi: number;
}

export interface AvrDay {
  /** The Asia session the levels hang off. */
  asia: SessionBox;
  /** Candle indices the levels are drawn over: from the end of Asia to the candle before the next Asia starts. */
  from: number;
  to: number;
  atr: number;
  up: number;
  down: number;
  eq: number;
  q1: number;
  q3: number;
  levels: AvrLevel[];
}

/**
 * Wilder ATR of daily candles, keyed by the wall-clock date of each daily candle. The value for date D uses
 * candles up to and including D.
 */
export function dailyAtrByDay(daily: Bar[], timeZone: string, length = 20): Map<string, number> {
  const series = atr(daily, length);
  const out = new Map<string, number>();
  daily.forEach((b, i) => {
    const v = series[i];
    if (v != null) out.set(sessionDay(b.time, timeZone), v);
  });
  return out;
}

/** The ATR known on `day`: the latest daily candle dated on or before it. */
function atrAsOf(byDay: Map<string, number>, sortedDays: string[], day: string): number | null {
  let lo = 0;
  let hi = sortedDays.length - 1;
  let best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (sortedDays[mid]! <= day) {
      best = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return best < 0 ? null : (byDay.get(sortedDays[best]!) ?? null);
}

/**
 * AVR levels for every COMPLETED Asia session on the chart. The ATR is the one known when Asia started
 * (daily candles dated on or before that evening), so a level never uses the day it is drawn on.
 */
export function avrDays(bars: Bar[], boxes: SessionBox[], atrByDay: Map<string, number>): AvrDay[] {
  const days = [...atrByDay.keys()].sort();
  const asia = boxes.filter((b) => b.key === 'asia');
  const out: AvrDay[] = [];
  asia.forEach((box, k) => {
    if (box.open) return;
    const a = atrAsOf(atrByDay, days, box.day);
    if (a == null) return;
    const { up, down } = avrUnits(a);
    const range = box.high - box.low;
    const levels: AvrLevel[] = [];
    for (const l of AVR_LEVELS) {
      levels.push({
        key: l.key,
        label: l.label,
        side: 'up',
        lo: box.high + (l.at - l.band) * up,
        hi: box.high + (l.at + l.band) * up,
      });
      levels.push({
        key: l.key,
        label: l.label,
        side: 'down',
        lo: box.low - (l.at + l.band) * down,
        hi: box.low - (l.at - l.band) * down,
      });
    }
    const next = asia[k + 1];
    out.push({
      asia: box,
      from: Math.min(bars.length - 1, box.to + 1),
      to: next ? next.from - 1 : bars.length - 1,
      atr: a,
      up,
      down,
      eq: box.low + range / 2,
      q1: box.low + range / 4,
      q3: box.low + (3 * range) / 4,
      levels,
    });
  });
  return out.filter((d) => d.to >= d.from);
}

// ── swings and market structure ─────────────────────────────────────────────────

export interface Pivot {
  index: number;
  price: number;
  kind: 'H' | 'L';
  /** Candle index at which price had moved far enough away for this to count as a swing (no look-ahead). */
  confirmedAt: number;
}

export interface Swings {
  pivots: Pivot[];
  /** The running extreme of the leg still in progress (not a swing yet). */
  tentative: { index: number; price: number; kind: 'H' | 'L' } | null;
}

/**
 * ZigZag swings: a high/low becomes a swing once price moves `mult` × ATR(`atrLength`) away from it. This is our
 * reading of his "significant swing"; he never defines one.
 */
export function swings(bars: Bar[], mult = 2, atrLength = 14): Swings {
  const pivots: Pivot[] = [];
  const a = atr(bars, atrLength);
  let trend = 0;
  let hi = 0;
  let lo = 0;
  const lowestSince = (from: number, to: number) => {
    let k = to;
    for (let j = from; j <= to; j++) if (bars[j]!.low < bars[k]!.low) k = j;
    return k;
  };
  const highestSince = (from: number, to: number) => {
    let k = to;
    for (let j = from; j <= to; j++) if (bars[j]!.high > bars[k]!.high) k = j;
    return k;
  };
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]!;
    const t = (a[i] ?? null) == null ? null : a[i]! * mult;
    if (trend >= 0 && b.high > bars[hi]!.high) hi = i;
    if (trend <= 0 && b.low < bars[lo]!.low) lo = i;
    if (t == null) continue;
    if (trend === 0) {
      if (bars[hi]!.high - b.low >= t && hi < i) {
        pivots.push({ index: hi, price: bars[hi]!.high, kind: 'H', confirmedAt: i });
        trend = -1;
        lo = lowestSince(hi + 1, i);
      } else if (b.high - bars[lo]!.low >= t && lo < i) {
        pivots.push({ index: lo, price: bars[lo]!.low, kind: 'L', confirmedAt: i });
        trend = 1;
        hi = highestSince(lo + 1, i);
      }
    } else if (trend === 1) {
      if (bars[hi]!.high - b.low >= t && hi < i) {
        pivots.push({ index: hi, price: bars[hi]!.high, kind: 'H', confirmedAt: i });
        trend = -1;
        lo = lowestSince(hi + 1, i);
      }
    } else if (b.high - bars[lo]!.low >= t && lo < i) {
      pivots.push({ index: lo, price: bars[lo]!.low, kind: 'L', confirmedAt: i });
      trend = 1;
      hi = highestSince(lo + 1, i);
    }
  }
  const tentative =
    trend === 1
      ? { index: hi, price: bars[hi]!.high, kind: 'H' as const }
      : trend === -1
        ? { index: lo, price: bars[lo]!.low, kind: 'L' as const }
        : null;
  return { pivots, tentative };
}

export type StructureLabel = 'HH' | 'LH' | 'HL' | 'LL';

/** HH/LH for each swing high vs the previous swing high; HL/LL for lows. The first of each kind has none. */
export function structureLabels(pivots: Pivot[]): (StructureLabel | null)[] {
  let lastH: number | null = null;
  let lastL: number | null = null;
  return pivots.map((p) => {
    if (p.kind === 'H') {
      const label = lastH == null ? null : p.price > lastH ? 'HH' : 'LH';
      lastH = p.price;
      return label;
    }
    const label = lastL == null ? null : p.price > lastL ? 'HL' : 'LL';
    lastL = p.price;
    return label;
  });
}

export type Bias = 'bull' | 'bear' | 'neutral';

/** Higher high + higher low = bull, lower high + lower low = bear, anything else neutral (his plain structure read). */
export function structureBias(pivots: Pivot[]): Bias {
  const labels = structureLabels(pivots);
  let h: StructureLabel | null = null;
  let l: StructureLabel | null = null;
  for (let i = pivots.length - 1; i >= 0 && (h == null || l == null); i--) {
    if (pivots[i]!.kind === 'H' && h == null) h = labels[i]!;
    if (pivots[i]!.kind === 'L' && l == null) l = labels[i]!;
  }
  if (h === 'HH' && l === 'HL') return 'bull';
  if (h === 'LH' && l === 'LL') return 'bear';
  return 'neutral';
}

/** Bias on each of his four timeframes, built from the chart's candles (a timeframe finer than the chart is null). */
export function biasLadder(
  bars: Bar[],
  chartSecs: number,
  timeZone: string,
  mult = 2,
): { label: string; secs: number; bias: Bias | null }[] {
  const tfs = [
    { label: '4h', secs: 14400 },
    { label: '1h', secs: 3600 },
    { label: '15m', secs: 900 },
    { label: '5m', secs: 300 },
  ];
  return tfs.map(({ label, secs }) => {
    if (secs < chartSecs || !bars.length) return { label, secs, bias: null };
    const tfBars = secs === chartSecs ? bars : aggregateBars(bars, secs, timeZone);
    return { label, secs, bias: tfBars.length > 30 ? structureBias(swings(tfBars, mult).pivots) : null };
  });
}

// ── range Fibonacci zone, value area, premium / discount ────────────────────────

/** His retracement levels; 0.705–0.886 is the zone he trades, a close beyond 0.886 cancels it. */
export const FIB_LEVELS = [0.5, 0.62, 0.705, 0.788, 0.886, 1.1] as const;

/** extended: price ran past the leg's end before retracing, so this zone never came into play. */
export type ZoneStatus = 'waiting' | 'in zone' | 'reacted' | 'target hit' | 'invalidated' | 'extended';

export interface RangeFib {
  /** The leg: `from` is where it started, `to` the swing it ended at (the retracement runs away from `to`). */
  from: Pivot;
  to: Pivot;
  /** up leg → discount zone below, a long idea; down leg → premium zone above, a short idea. */
  dir: 'up' | 'down';
  /** Price of each retracement level, measured back from `to`. */
  levels: { level: number; price: number }[];
  zoneTop: number;
  zoneBottom: number;
  /** Where he aims: the leg's end (the opposite liquidity, "DOL"). */
  target: number;
  /** Where a close cancels the idea, and where price then tends to run (1.1). */
  invalidation: number;
  extension: number;
  /** Fixed-range volume profile over the leg. */
  vah: number | null;
  val: number | null;
  poc: number | null;
  /** True when the zone sits outside value (below VAL for a long, above VAH for a short), as he requires. */
  outsideValue: boolean | null;
  status: ZoneStatus;
  touchedAt: number | null;
  invalidatedAt: number | null;
  targetAt: number | null;
}

/**
 * The latest leg in the wanted direction (`prefer`: the higher-timeframe bias; neutral = simply the latest leg)
 * and how price has behaved since it ended. A leg is only used once its end swing is confirmed.
 */
export function rangeFib(bars: Bar[], pivots: Pivot[], prefer: Bias = 'neutral', rows = 30): RangeFib | null {
  for (let k = pivots.length - 1; k >= 1; k--) {
    const to = pivots[k]!;
    const from = pivots[k - 1]!;
    const dir = to.kind === 'H' ? 'up' : 'down';
    if (prefer === 'bull' && dir !== 'up') continue;
    if (prefer === 'bear' && dir !== 'down') continue;
    const span = to.price - from.price; // + for up legs
    const at = (lvl: number) => to.price - lvl * span;
    const levels = FIB_LEVELS.map((level) => ({ level, price: at(level) }));
    const z1 = at(0.705);
    const z2 = at(0.886);
    const zoneTop = Math.max(z1, z2);
    const zoneBottom = Math.min(z1, z2);
    const invalidation = at(0.886);
    const profile = buildProfile(bars, from.index, to.index, rows);
    const outsideValue = profile ? (dir === 'up' ? zoneTop <= profile.val : zoneBottom >= profile.vah) : null;
    // walk forward from the end of the leg
    let touchedAt: number | null = null;
    let invalidatedAt: number | null = null;
    let targetAt: number | null = null;
    let extended = false;
    for (let i = to.index + 1; i < bars.length; i++) {
      const b = bars[i]!;
      if (dir === 'up') {
        if (b.high > to.price && touchedAt == null) {
          extended = true; // ran past the leg's end before retracing: a new leg will form
          break;
        }
        if (touchedAt == null && b.low <= zoneTop) touchedAt = i;
        if (b.close < invalidation) {
          invalidatedAt = i;
          break;
        }
        if (touchedAt != null && i > touchedAt && b.high >= to.price) {
          targetAt = i;
          break;
        }
      } else {
        if (b.low < to.price && touchedAt == null) {
          extended = true;
          break;
        }
        if (touchedAt == null && b.high >= zoneBottom) touchedAt = i;
        if (b.close > invalidation) {
          invalidatedAt = i;
          break;
        }
        if (touchedAt != null && i > touchedAt && b.low <= to.price) {
          targetAt = i;
          break;
        }
      }
    }
    const last = bars[bars.length - 1];
    const inZone = !!last && last.close <= zoneTop && last.close >= zoneBottom;
    const status: ZoneStatus =
      invalidatedAt != null
        ? 'invalidated'
        : extended
          ? 'extended'
        : targetAt != null
          ? 'target hit'
          : touchedAt == null
            ? 'waiting'
            : inZone
              ? 'in zone'
              : 'reacted';
    return {
      from,
      to,
      dir,
      levels,
      zoneTop,
      zoneBottom,
      target: to.price,
      invalidation,
      extension: at(1.1),
      vah: profile?.vah ?? null,
      val: profile?.val ?? null,
      poc: profile?.pocPrice ?? null,
      outsideValue,
      status,
      touchedAt,
      invalidatedAt,
      targetAt,
    };
  }
  return null;
}

// ── environment: expansion / rotation / compression ─────────────────────────────

export type Environment = 'expansion' | 'rotation' | 'compression';

export interface EnvironmentRead {
  env: Environment;
  /** Kaufman efficiency: net move ÷ path length over the window (1 = straight line, 0 = pure chop). */
  efficiency: number;
  /** The window's high-low range ÷ the median range of earlier windows of the same length. */
  rangeRatio: number;
  /** Average share of each candle's range that overlaps the candle before it. */
  overlap: number;
}

/**
 * Our definition of his three environments (he gives no numbers): over the last `window` candles,
 * expansion = efficient AND wide, compression = narrow AND inefficient, rotation = everything else.
 */
export function environment(bars: Bar[], window = 24): EnvironmentRead | null {
  if (bars.length < window * 3) return null;
  const n = bars.length;
  const slice = bars.slice(n - window);
  let path = 0;
  let overlapSum = 0;
  for (let i = 1; i < slice.length; i++) {
    const b = slice[i]!;
    const p = slice[i - 1]!;
    path += Math.abs(b.close - p.close);
    const range = b.high - b.low;
    const ov = Math.max(0, Math.min(b.high, p.high) - Math.max(b.low, p.low));
    overlapSum += range > 0 ? Math.min(1, ov / range) : 1;
  }
  const net = Math.abs(slice[slice.length - 1]!.close - slice[0]!.close);
  const efficiency = path > 0 ? net / path : 0;
  const spanOf = (from: number) => {
    let hi = -Infinity;
    let lo = Infinity;
    for (let i = from; i < from + window; i++) {
      hi = Math.max(hi, bars[i]!.high);
      lo = Math.min(lo, bars[i]!.low);
    }
    return hi - lo;
  };
  const past: number[] = [];
  for (let s = n - 2 * window; s >= 0 && past.length < 40; s -= Math.max(1, Math.floor(window / 2))) past.push(spanOf(s));
  past.sort((x, y) => x - y);
  const median = past[Math.floor(past.length / 2)] ?? 0;
  const rangeRatio = median > 0 ? spanOf(n - window) / median : 1;
  const overlap = overlapSum / (slice.length - 1);
  const env: Environment =
    efficiency >= 0.35 && rangeRatio >= 1
      ? 'expansion'
      : rangeRatio < 0.7 && efficiency < 0.35
        ? 'compression'
        : 'rotation';
  return { env, efficiency, rangeRatio, overlap };
}
