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

export type SessionKey = 'asia' | 'london' | 'ny' | 'open';

export interface SessionSpec {
  key: SessionKey;
  label: string;
  /** Minutes after midnight on `tz`'s clock; `end` is exclusive. start > end crosses midnight. */
  start: number;
  end: number;
  /** The clock the times are on. Omitted: the chart's exchange time zone. */
  tz?: string;
}

const NEW_YORK = 'America/New_York';
const INDIA = 'Asia/Kolkata';

/**
 * Defaults on the New York clock, whatever market the chart shows: these are global sessions. He never states his
 * windows; these are the common ICT "kill zones" and his own trading window (the first 90 minutes after the 09:30
 * open).
 */
export const AR_SESSIONS: SessionSpec[] = [
  { key: 'asia', label: 'Asia', start: 20 * 60, end: 24 * 60, tz: NEW_YORK },
  { key: 'london', label: 'London', start: 2 * 60, end: 5 * 60, tz: NEW_YORK },
  { key: 'ny', label: 'New York', start: 9 * 60 + 30, end: 11 * 60, tz: NEW_YORK },
];

/**
 * How the AR model maps onto the market a chart shows. The course trades US index futures; elsewhere the session
 * parts are OUR adaptation:
 * - `us` (everything that is not NSE): his New York sessions; entries may confirm 09:30–16:00 and are flat by 16:00
 *   New York (the operator removed his 09:30–11:00 limit on 2026-11-25; the "New York" box still marks it).
 * - `nse`: NSE trades 09:15–15:30 India time only, so New York's open (19:00–20:00 India) and the overnight Asia
 *   session never happen on it. Entries may confirm all session (09:15–15:15: the operator removed the first-90-minutes
 *   limit on 2026-11-25; the "NSE open" box still marks 09:15–10:45), flat by 15:15 (before the
 *   broker's intraday square-off), and the Asia-range levels do not apply.
 */
export interface ArMarket {
  key: 'us' | 'nse';
  /** The clock the sessions, trade window and flat time are on. */
  tz: string;
  /** That clock in words, for the UI ("New York time"). */
  clock: string;
  sessions: SessionSpec[];
  window: { start: number; end: number };
  flatAt: number;
  /** Has an overnight Asia session to hang the AVR levels off. */
  hasAsia: boolean;
}

const US_MARKET: ArMarket = {
  key: 'us',
  tz: NEW_YORK,
  clock: 'New York',
  sessions: AR_SESSIONS,
  window: { start: 9 * 60 + 30, end: 16 * 60 },
  flatAt: 16 * 60,
  hasAsia: true,
};

const NSE_MARKET: ArMarket = {
  key: 'nse',
  tz: INDIA,
  clock: 'India',
  sessions: [{ key: 'open', label: 'NSE open', start: 9 * 60 + 15, end: 10 * 60 + 45, tz: INDIA }],
  window: { start: 9 * 60 + 15, end: 15 * 60 + 15 },
  flatAt: 15 * 60 + 15,
  hasAsia: false,
};

/** The market profile for a chart, from its exchange time zone. */
export function arMarket(timeZone: string): ArMarket {
  return timeZone === INDIA || timeZone === 'Asia/Calcutta' ? NSE_MARKET : US_MARKET;
}

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

/** One box per session occurrence on the chart's candles (intraday only). Default sessions: `arMarket`'s. */
export function sessionBoxes(
  bars: Bar[],
  timeZone: string,
  specs: SessionSpec[] = arMarket(timeZone).sessions,
): SessionBox[] {
  const out: SessionBox[] = [];
  const open = new Map<SessionKey, SessionBox>();
  bars.forEach((b, i) => {
    for (const s of specs) {
      const wall = wallClock(b.time, s.tz ?? timeZone);
      const m = minuteOfDay(wall);
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
    for (const s of specs) {
      const m = minuteOfDay(wallClock(last.time, s.tz ?? timeZone));
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

/**
 * Trend per candle by break of structure, the way a trader reads a chart (OUR rule; he gives none):
 * - It starts once the swings show a pattern (`structureBias`: higher high + higher low, or the reverse).
 * - In an uptrend the "protected low" is the swing low that led to the latest higher high. The trend stays up until
 *   a candle CLOSES below it by more than `tolMult` × ATR, then turns down (protected high = the latest swing high).
 *   Downtrends mirror it.
 * A single slightly lower high, or a wick through an old low, does not flip it (`structureBias` alone would).
 * Each value uses only swings confirmed by that candle and that candle's close: no look-ahead.
 */
export function structureTrend(bars: Bar[], pivots: Pivot[], tolMult = 0.25, atrLength = 14): Bias[] {
  const out: Bias[] = new Array(bars.length).fill('neutral');
  const a = atr(bars, atrLength);
  const known: Pivot[] = [];
  const lastOf = (kind: 'H' | 'L', before = known.length) => {
    for (let j = before - 1; j >= 0; j--) if (known[j]!.kind === kind) return known[j]!;
    return null;
  };
  let state: Bias = 'neutral';
  let prot: number | null = null;
  let k = 0;
  for (let i = 0; i < bars.length; i++) {
    while (k < pivots.length && pivots[k]!.confirmedAt <= i) {
      const p = pivots[k++]!;
      known.push(p);
      if (state === 'neutral') {
        const s = structureBias(known);
        if (s !== 'neutral') {
          state = s;
          prot = lastOf(s === 'bull' ? 'L' : 'H')?.price ?? null;
        }
        continue;
      }
      const prevSame = lastOf(p.kind, known.length - 1);
      const prevOpp = lastOf(p.kind === 'H' ? 'L' : 'H', known.length - 1);
      // a new higher high (uptrend) / lower low (downtrend) moves the protected level to the swing before it
      if (state === 'bull' && p.kind === 'H' && prevSame && p.price > prevSame.price && prevOpp) prot = prevOpp.price;
      if (state === 'bear' && p.kind === 'L' && prevSame && p.price < prevSame.price && prevOpp) prot = prevOpp.price;
    }
    const tol = (a[i] ?? 0) * tolMult;
    const c = bars[i]!.close;
    if (state === 'bull' && prot != null && c < prot - tol) {
      state = 'bear';
      prot = lastOf('H')?.price ?? null;
    } else if (state === 'bear' && prot != null && c > prot + tol) {
      state = 'bull';
      prot = lastOf('L')?.price ?? null;
    }
    out[i] = state;
  }
  return out;
}

/**
 * Trend per candle from the latest swings only, with "unclear" as a real answer (OUR rule, the AR strategy's default):
 * - up   = the latest swing high is higher than the one before AND the latest swing low is higher than the one before;
 * - down = both lower;
 * - anything else (higher high + lower low, lower high + higher low) is neutral: no clear direction.
 * A candle that CLOSES past the latest swing on the other side (by more than `tolMult` × ATR) drops the trend to
 * neutral straight away. It never flips straight to the opposite direction: that takes the new swings to confirm it.
 * Each value uses only swings confirmed by that candle and that candle's close: no look-ahead.
 */
export function swingTrend(bars: Bar[], pivots: Pivot[], tolMult = 0.25, atrLength = 14): Bias[] {
  const out: Bias[] = new Array(bars.length).fill('neutral');
  const a = atr(bars, atrLength);
  const known: Pivot[] = [];
  let k = 0;
  let base: Bias = 'neutral';
  let broken = false;
  for (let i = 0; i < bars.length; i++) {
    let changed = false;
    while (k < pivots.length && pivots[k]!.confirmedAt <= i) {
      known.push(pivots[k++]!);
      changed = true;
    }
    if (changed) {
      base = structureBias(known);
      broken = false;
    }
    if (base !== 'neutral' && !broken) {
      const tol = (a[i] ?? 0) * tolMult;
      const c = bars[i]!.close;
      let lastH: Pivot | null = null;
      let lastL: Pivot | null = null;
      for (let j = known.length - 1; j >= 0 && (!lastH || !lastL); j--) {
        if (known[j]!.kind === 'H' && !lastH) lastH = known[j]!;
        if (known[j]!.kind === 'L' && !lastL) lastL = known[j]!;
      }
      if (base === 'bull' && lastL && c < lastL.price - tol) broken = true;
      if (base === 'bear' && lastH && c > lastH.price + tol) broken = true;
    }
    out[i] = broken ? 'neutral' : base;
  }
  return out;
}

/** Bias on daily plus his four timeframes, built from the chart's candles (a timeframe finer than the chart is null). */
export function biasLadder(
  bars: Bar[],
  chartSecs: number,
  timeZone: string,
  mult = 2,
): { label: string; secs: number; bias: Bias | null }[] {
  const tfs = [
    { label: '1D', secs: 86400 },
    { label: '4h', secs: 14400 },
    { label: '1h', secs: 3600 },
    { label: '15m', secs: 900 },
    { label: '5m', secs: 300 },
  ];
  return tfs.map(({ label, secs }) => {
    if (secs < chartSecs || !bars.length) return { label, secs, bias: null };
    const tfBars = secs === chartSecs ? bars : aggregateBars(bars, secs, timeZone);
    return {
      label,
      secs,
      bias: tfBars.length > 30 ? (structureTrend(tfBars, swings(tfBars, mult).pivots).at(-1) ?? 'neutral') : null,
    };
  });
}

// ── range Fibonacci zone, value area, premium / discount ────────────────────────

/** His retracement levels; 0.705–0.886 is the zone he trades, a close beyond 0.886 cancels it. */
export const FIB_LEVELS = [0.5, 0.62, 0.705, 0.788, 0.886, 1.1] as const;

/**
 * Stand-in for his private "dark pool decoder" (research/specs/orderflow-course-ar-model.md §2a′): a box inside the
 * leg's retracement with three levels, the near edge, the line where his shading changes, and the far edge. A close
 * past the far edge cancels it.
 *
 * NOT his formula. These are the medians of five boxes measured off his screen (1:25–1:42 in the video): near 0.54–0.73,
 * change 0.71–0.83, far 0.77–0.87. His real box moves between those ranges for a reason the video does not show.
 */
export const DECODER_LEVELS = { near: 0.62, mid: 0.75, far: 0.81 } as const;

export interface DecoderBox {
  /** Prices of the three levels, measured back from the leg's end like the Fib levels. */
  near: number;
  mid: number;
  far: number;
  top: number;
  bottom: number;
}

export interface DecoderLevels {
  near: number;
  mid: number;
  far: number;
}

/** The decoder stand-in for the leg `from` → `to`. */
export function decoderBox(from: Pivot, to: Pivot, levels: DecoderLevels = DECODER_LEVELS): DecoderBox {
  const span = to.price - from.price;
  const at = (lvl: number) => to.price - lvl * span;
  const near = at(levels.near);
  const mid = at(levels.mid);
  const far = at(levels.far);
  return { near, mid, far, top: Math.max(near, far), bottom: Math.min(near, far) };
}

/**
 * waiting: price has not come back to the box yet. in box: it has, and nothing is decided.
 * held: after touching the box, price went back to the leg's end (his target). broken: a candle closed past the far
 * edge. extended: price ran past the leg's end before ever retracing, so a new leg forms and the box never came
 * into play.
 */
export type DecoderStatus = 'waiting' | 'in box' | 'held' | 'broken' | 'extended';

export interface DecoderZone {
  from: Pivot;
  to: Pivot;
  /** up leg → box below, a long idea (discount); down leg → box above, a short idea (premium). */
  dir: 'up' | 'down';
  box: DecoderBox;
  status: DecoderStatus;
  /** First candle that reached the box. */
  touchedAt: number | null;
  /** Candle that decided it (held / broken / extended); null while it is still open. */
  endAt: number | null;
}

/**
 * The "Dark Pool Decoder" indicator: a decoder box on each of the last `count` swing legs, the way he drags it by
 * hand from one swing to the next, followed candle by candle until it is decided. `side` keeps only up legs (longs),
 * only down legs (shorts) or both. A box exists only once its end swing is confirmed (`swings` has no look-ahead).
 */
export function decoderZones(
  bars: Bar[],
  pivots: Pivot[],
  opts: { levels?: DecoderLevels; count?: number; side?: 'both' | 'up' | 'down' } = {},
): DecoderZone[] {
  const levels = opts.levels ?? DECODER_LEVELS;
  const count = Math.max(1, opts.count ?? 3);
  const side = opts.side ?? 'both';
  const out: DecoderZone[] = [];
  for (let k = pivots.length - 1; k >= 1 && out.length < count; k--) {
    const to = pivots[k]!;
    const from = pivots[k - 1]!;
    const dir = to.kind === 'H' ? 'up' : 'down';
    if (side !== 'both' && side !== dir) continue;
    if (to.price === from.price) continue;
    const box = decoderBox(from, to, levels);
    let status: DecoderStatus = 'waiting';
    let touchedAt: number | null = null;
    let endAt: number | null = null;
    for (let i = to.index + 1; i < bars.length && endAt == null; i++) {
      const b = bars[i]!;
      if (dir === 'up') {
        if (touchedAt == null && b.high > to.price) {
          status = 'extended';
          endAt = i;
        } else if (touchedAt != null && i > touchedAt && b.high >= to.price) {
          status = 'held';
          endAt = i;
        } else {
          if (touchedAt == null && b.low <= box.top) {
            touchedAt = i;
            status = 'in box';
          }
          if (b.close < box.bottom) {
            status = 'broken';
            endAt = i;
          }
        }
      } else if (touchedAt == null && b.low < to.price) {
        status = 'extended';
        endAt = i;
      } else if (touchedAt != null && i > touchedAt && b.low <= to.price) {
        status = 'held';
        endAt = i;
      } else {
        if (touchedAt == null && b.high >= box.bottom) {
          touchedAt = i;
          status = 'in box';
        }
        if (b.close > box.top) {
          status = 'broken';
          endAt = i;
        }
      }
    }
    out.push({ from, to, dir, box, status, touchedAt, endAt });
  }
  return out.reverse();
}

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
  /** The "dark pool decoder" stand-in box for this leg (`decoderBox`). */
  decoder: DecoderBox;
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
 * The leg the market structure is building, in `dir`, from swings in time order (his "latest clear leg" made of
 * "higher highs and higher lows"; OUR rule for which swings count):
 * - a swing beyond the current leg's end (a higher high for an up leg) starts or extends the leg, from the most
 *   extreme opposite swing since the previous leg end (the lowest low before the breakout);
 * - a swing that stays inside the leg (a lower high in an up leg) is an internal pull-back and changes nothing;
 * - a swing past the leg's origin (a lower low under an up leg's start) ends the structure until a new leg forms.
 * Pass only swings already confirmed; the strategy passes those of the current session.
 */
export function structureLeg(pivots: Pivot[], dir: 'up' | 'down'): [Pivot, Pivot] | null {
  const up = dir === 'up';
  let leg: [Pivot, Pivot] | null = null;
  let since = 0;
  for (let j = 0; j < pivots.length; j++) {
    const p = pivots[j]!;
    if (up ? p.kind === 'H' : p.kind === 'L') {
      if (!leg || (up ? p.price > leg[1].price : p.price < leg[1].price)) {
        let o: Pivot | null = null;
        for (let q = since; q < j; q++) {
          const c = pivots[q]!;
          if ((up ? c.kind === 'L' : c.kind === 'H') && (!o || (up ? c.price < o.price : c.price > o.price))) o = c;
        }
        if (o) {
          leg = [o, p];
          since = j;
        }
      }
    } else if (leg && (up ? p.price < leg[0].price : p.price > leg[0].price)) {
      leg = null;
      since = j;
    }
  }
  return leg;
}

/**
 * OUR adaptation for markets that close overnight (NSE), for `structureLeg`: a session that gaps and runs straight
 * from the open has no swing at its start (the ZigZag carries yesterday's leg across the gap), so its first swing has
 * no origin to measure from. This puts the session's opening extreme in front of `pivots` (the session's own swings,
 * in time order): the lowest low before a first swing high, or the highest high before a first swing low. It counts
 * from when that first swing is confirmed. Unchanged when the session has no swing yet, or the extreme is the swing.
 */
export function withSessionOpen(bars: Bar[], sessionStart: number, pivots: Pivot[]): Pivot[] {
  const first = pivots[0];
  if (!first) return pivots;
  const high = first.kind === 'L';
  let k = sessionStart;
  for (let j = sessionStart; j < first.index; j++) if (high ? bars[j]!.high > bars[k]!.high : bars[j]!.low < bars[k]!.low) k = j;
  if (k >= first.index) return pivots;
  const open: Pivot = { index: k, price: high ? bars[k]!.high : bars[k]!.low, kind: high ? 'H' : 'L', confirmedAt: first.confirmedAt };
  return [open, ...pivots];
}

/**
 * OUR adaptation for markets that close overnight (NSE): a swing leg may not start before the session its end swing
 * is in. A leg from yesterday's low to today's high (with an opening gap between) starts instead at today's lowest
 * low before that high; a down leg at today's highest high. The ZigZag cannot see this on its own: an opening gap
 * is not a pull-back, so yesterday's swing and today's run merge into one leg.
 *
 * Kept as it was when the leg already starts in the session, or when the session's own part is smaller than
 * `minSpan` (the move was mostly the gap). A session break is a gap of over an hour between candles. Reads only
 * candles up to the leg's end: no look-ahead.
 */
export function sessionAnchoredFrom(bars: Bar[], from: Pivot, to: Pivot, minSpan = 0): Pivot {
  let start = to.index;
  while (start > 0 && bars[start]!.time - bars[start - 1]!.time <= 3600) start--;
  if (from.index >= start) return from;
  const up = to.kind === 'H';
  let k = start;
  for (let j = start; j < to.index; j++) if (up ? bars[j]!.low < bars[k]!.low : bars[j]!.high > bars[k]!.high) k = j;
  if (k >= to.index) return from;
  const price = up ? bars[k]!.low : bars[k]!.high;
  if (Math.abs(to.price - price) < minSpan) return from;
  return { index: k, price, kind: from.kind, confirmedAt: from.confirmedAt };
}

/** The week range at one candle: indices of its two extremes, `from` the earlier one, `to` the later one. */
export interface WeekRangeAt {
  from: number;
  to: number;
  /** The high came first: a down leg (premium above, a short idea). */
  down: boolean;
}

/**
 * His "most active range" (condensed course `WEHgjZhg5bI` 17:59–18:59, research/specs/orderflow-course-ar-model.md
 * "Second source"): the swing high and swing low on the hourly over about the last week. OUR codification: the
 * highest high and the lowest low of the candles that started in the last `days` calendar days, up to and including
 * candle i. The leg runs from the earlier extreme to the later one, so a high that came first makes a down leg
 * (premium above, a short idea). Checked against his 20–28 Oct 2026 MNQ example: it gives his zone within 2 points.
 *
 * null until the candles reach back `days` days. Reads only candles up to i: no look-ahead. O(n) (monotonic queues).
 */
export function weekRanges(bars: Bar[], days = 7): (WeekRangeAt | null)[] {
  const out: (WeekRangeAt | null)[] = new Array(bars.length).fill(null);
  if (!bars.length) return out;
  const span = days * 86400;
  const hi: number[] = []; // indices, highs decreasing; the front is the window's high
  const lo: number[] = []; // indices, lows increasing; the front is the window's low
  let start = 0;
  let hh = 0;
  let hl = 0;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]!;
    while (hi.length > hh && bars[hi[hi.length - 1]!]!.high <= b.high) hi.pop();
    hi.push(i);
    while (lo.length > hl && bars[lo[lo.length - 1]!]!.low >= b.low) lo.pop();
    lo.push(i);
    while (bars[start]!.time <= b.time - span) start++;
    while (hi[hh]! < start) hh++;
    while (lo[hl]! < start) hl++;
    if (bars[0]!.time > b.time - span) continue; // not a full window yet
    const h = hi[hh]!;
    const l = lo[hl]!;
    if (h === l) continue;
    out[i] = h < l ? { from: h, to: l, down: true } : { from: l, to: h, down: false };
  }
  return out;
}

/** A week range as the two pivots `rangeFib` and the strategy take (the high an H pivot, the low an L pivot). */
export function weekPivots(bars: Bar[], r: WeekRangeAt, confirmedAt: number): [Pivot, Pivot] {
  const piv = (i: number, high: boolean): Pivot => ({
    index: i,
    price: high ? bars[i]!.high : bars[i]!.low,
    kind: high ? 'H' : 'L',
    confirmedAt,
  });
  return [piv(r.from, r.down), piv(r.to, !r.down)];
}

/**
 * The latest leg in the wanted direction (`prefer`: the higher-timeframe bias; neutral = simply the latest leg)
 * and how price has behaved since it ended. A leg is only used once its end swing is confirmed.
 */
export function rangeFib(
  bars: Bar[],
  pivots: Pivot[],
  prefer: Bias = 'neutral',
  rows = 30,
  /** Start legs inside the end swing's session (`sessionAnchoredFrom`): markets that close overnight. */
  session?: { minSpanAt: (index: number) => number },
): RangeFib | null {
  for (let k = pivots.length - 1; k >= 1; k--) {
    const to = pivots[k]!;
    const from = session ? sessionAnchoredFrom(bars, pivots[k - 1]!, to, session.minSpanAt(to.index)) : pivots[k - 1]!;
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
      decoder: decoderBox(from, to),
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
 * Only candles before `end` are read.
 */
export function environment(bars: Bar[], window = 24, end = bars.length): EnvironmentRead | null {
  // `end`: read as of the candle before it (exclusive), so a replay never sees later candles
  const n = Math.min(end, bars.length);
  if (n < window * 3) return null;
  const slice = bars.slice(n - window, n);
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

/** Market structure at one candle's close (`structureReads`). */
export interface StructureRead {
  /** Up / down from the swings, or neutral when they overlap (a range). */
  dir: Bias;
  /** What the swings did, in words, for the checklist. */
  why: string;
}

/**
 * Plain market structure (his step 1a, `[S 0:07, 1:20]`), per candle as of its close: only swings confirmed by then
 * and that candle's close, so no look-ahead. OUR wording of his rule:
 * - a close beyond the latest swing high (low) by more than `tolMult` × ATR = up (down): price accepted outside the
 *   last range, before the swings have had time to show it;
 * - otherwise higher high + higher low = up, lower high + lower low = down;
 * - anything else (a higher high with a lower low, or the reverse) = the swings overlap = sideways.
 * Null until there are two swing highs and two swing lows.
 */
export function structureReads(bars: Bar[], pivots: Pivot[], tolMult = 0.25, atrLength = 14): (StructureRead | null)[] {
  const out: (StructureRead | null)[] = new Array(bars.length).fill(null);
  const a = atr(bars, atrLength);
  const highs: Pivot[] = [];
  const lows: Pivot[] = [];
  const px = (p: number) => p.toFixed(2);
  let k = 0;
  for (let i = 0; i < bars.length; i++) {
    while (k < pivots.length && pivots[k]!.confirmedAt <= i) {
      const p = pivots[k++]!;
      (p.kind === 'H' ? highs : lows).push(p);
    }
    if (highs.length < 2 || lows.length < 2) continue;
    const h = highs[highs.length - 1]!;
    const h0 = highs[highs.length - 2]!;
    const l = lows[lows.length - 1]!;
    const l0 = lows[lows.length - 2]!;
    const c = bars[i]!.close;
    const tol = (a[i] ?? 0) * tolMult;
    if (c > h.price + tol) out[i] = { dir: 'bull', why: `closed above the last swing high ${px(h.price)}` };
    else if (c < l.price - tol) out[i] = { dir: 'bear', why: `closed below the last swing low ${px(l.price)}` };
    else if (h.price > h0.price && l.price > l0.price) out[i] = { dir: 'bull', why: 'higher highs and higher lows' };
    else if (h.price < h0.price && l.price < l0.price) out[i] = { dir: 'bear', why: 'lower highs and lower lows' };
    else
      out[i] = {
        dir: 'neutral',
        why: `the swings overlap (${h.price > h0.price ? 'higher' : 'lower'} high, ${l.price > l0.price ? 'higher' : 'lower'} low)`,
      };
  }
  return out;
}

/**
 * The last trading day's range ÷ a normal day's, per candle as of its close: the high-low of the last `perDay`
 * candles over the median of earlier `perDay`-candle windows (up to 40 of them, half a day apart). Counting candles
 * rather than hours makes a day mean the same on NSE (about 7 hourly candles) as on CME (about 23), and a weekend
 * gap does not shrink it. Null until 3 days of candles exist. Below about 0.6 = compression (his "chop": no room for
 * a decent reward-to-risk).
 */
export function dayRangeRatio(bars: Bar[], perDay: number): (number | null)[] {
  const m = Math.max(2, Math.round(perDay));
  const n = bars.length;
  // span[k] = high-low of the m candles ending at k (monotonic deques)
  const span: number[] = new Array(n).fill(0);
  const hi: number[] = [];
  const lo: number[] = [];
  for (let k = 0; k < n; k++) {
    while (hi.length && bars[hi[hi.length - 1]!]!.high <= bars[k]!.high) hi.pop();
    hi.push(k);
    while (lo.length && bars[lo[lo.length - 1]!]!.low >= bars[k]!.low) lo.pop();
    lo.push(k);
    while (hi[0]! <= k - m) hi.shift();
    while (lo[0]! <= k - m) lo.shift();
    span[k] = bars[hi[0]!]!.high - bars[lo[0]!]!.low;
  }
  const step = Math.max(1, Math.floor(m / 2));
  const out: (number | null)[] = new Array(n).fill(null);
  for (let k = 3 * m - 1; k < n; k++) {
    const past: number[] = [];
    for (let e = k - m; e >= m - 1 && past.length < 40; e -= step) past.push(span[e]!);
    past.sort((x, y) => x - y);
    const median = past[Math.floor(past.length / 2)] ?? 0;
    out[k] = median > 0 ? span[k]! / median : null;
  }
  return out;
}

/** Median number of candles per trading day (days by the market's clock), for `dayRangeRatio`. */
export function candlesPerDay(bars: Bar[], timeZone: string): number {
  const counts = new Map<number, number>();
  for (const b of bars) {
    const d = Math.floor(wallClock(b.time, timeZone) / 86400);
    counts.set(d, (counts.get(d) ?? 0) + 1);
  }
  const c = [...counts.values()].sort((x, y) => x - y);
  return c.length ? c[Math.floor(c.length / 2)]! : 1;
}
