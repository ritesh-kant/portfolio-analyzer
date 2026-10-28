import { buildProfile, DEFAULT_ROWS, VALUE_AREA } from '../tv/volume-profile.ts';
import type { Bar } from '../tv/types.ts';
import type { SessionClock } from './relvol.ts';

/**
 * The "untested POC first retest" setup (volume-profile video, 2026-10-27):
 *  1. every recent session's volume profile has a point of control (POC): the price with the most volume;
 *  2. its heavy-volume zone is the run of rows around the POC holding at least `HVN_SHARE` of the POC row;
 *  3. after that session, price must move clearly AWAY from the zone (a candle CLOSING `AWAY_PCT` beyond its
 *     edge; a wick is not enough), on any later day: a move over several sessions counts;
 *  4. while price has not come back, the POC is "untested" (a naked POC). The trade is the FIRST pullback into the
 *     zone's near edge: the top edge for a long (price left upward), the bottom edge for a short;
 *  5. the stop goes behind the zone (its far edge).
 *
 * Each of the last `LOOKBACK_SESSIONS` full sessions is a candidate; the one reported is the live setup closest to
 * happening (first touch, then approaching, pulling back, away; ties go to the nearest zone).
 *
 * The profile is the chart's own (`buildProfile`, 24 rows, 70% value area), built from 1-minute bars where they
 * cover the session (see `pocCandidates`), so each POC here is the POC the chart's session profile draws. The thresholds are the video's description turned into numbers, never
 * tested: this finds the pattern, it does not say the pattern pays.
 */

/** Rows next to the POC belong to its zone while they hold at least this share of the POC row's volume. */
export const HVN_SHARE = 0.5;
/** "Moved away": a candle closed at least this far (%) beyond the zone edge. */
export const AWAY_PCT = 1;
/** "Approaching": not touched yet, and price is within this (%) of the entry edge. */
export const APPROACH_PCT = 0.5;
/** "Pulling back": price has given back at least this share of the move from the entry edge to its furthest point. */
export const PULLBACK_SHARE = 0.3;
/** A first touch is still live for this many bars after it began (5-minute bars: 30 minutes). */
export const LIVE_BARS = 6;
/** A session needs this many bars for its profile to mean anything (a half-day is skipped). */
export const MIN_PRIOR_BARS = 12;
/** How many full sessions back a POC can come from. */
export const LOOKBACK_SESSIONS = 8;

/** The knobs the chart indicator exposes; the scanner always uses the defaults. */
export interface PocConfig {
  /** Full sessions back a POC can come from. */
  lookback: number;
  /** % beyond the zone edge that counts as moving away. */
  awayPct: number;
  /** % from the entry edge that counts as approaching. */
  approachPct: number;
  /** % of the move away given back that counts as pulling back. */
  pullbackPct: number;
}
export const POC_DEFAULTS: PocConfig = {
  lookback: LOOKBACK_SESSIONS,
  awayPct: AWAY_PCT,
  approachPct: APPROACH_PCT,
  pullbackPct: PULLBACK_SHARE * 100,
};

export const POC_STAGES = [
  'Long · first touch',
  'Short · first touch',
  'Long · approaching',
  'Short · approaching',
  'Long · pulling back',
  'Short · pulling back',
  'Long · away',
  'Short · away',
  'Touched earlier',
  'Retested',
  'Broken',
  'Not departed',
] as const;
export type PocStage = (typeof POC_STAGES)[number];

export interface PocSetup {
  stage: PocStage;
  /** The POC, and the heavy-volume zone around it. */
  poc: number;
  zoneLo: number;
  zoneHi: number;
  /** The zone edge a trade enters at (top for a long, bottom for a short); null before price departs. */
  entry: number | null;
  /** The far edge of the zone: where the setup is wrong. */
  stop: number | null;
  /** Entry → stop as a % of entry. */
  riskPct: number | null;
  /** Price now vs the entry edge, % of price: positive = still above it (long side) / below it (short side). */
  edgeDistPct: number | null;
  /** Price now vs the POC, %. */
  pocDistPct: number;
  /** Sessions between the profile's session and today: 1 = yesterday. */
  sessionsAgo: number;
  /** Furthest price reached after moving away (high for a long, low for a short), and its bar. */
  extreme: number | null;
  extremeTime: number | null;
  /** Share (%) of the move from the entry edge to the extreme that price has given back: 0 = at the extreme. */
  pullbackPct: number | null;
  /** Unix seconds: first and last bar of the session the profile was built from (where the chart draws the zone). */
  fromTime: number;
  priorEndTime: number;
  /** Unix seconds of the bar that moved away from the zone, and of the first bar back at its edge. */
  departTime: number | null;
  touchTime: number | null;
}

/** The zone of rows around the POC that each hold ≥ `share` of the POC row's volume. */
export function pocZone(
  bars: Bar[],
  rows = DEFAULT_ROWS,
  share = HVN_SHARE,
): { poc: number; lo: number; hi: number } | null {
  const p = buildProfile(bars, 0, bars.length - 1, rows, VALUE_AREA);
  if (!p || p.total <= 0) return null;
  const vol = (i: number) => p.rows[i]!.up + p.rows[i]!.down;
  const floor = p.maxRow * share;
  let a = p.poc;
  let b = p.poc;
  while (a > 0 && vol(a - 1) >= floor) a -= 1;
  while (b < p.rows.length - 1 && vol(b + 1) >= floor) b += 1;
  return { poc: p.pocPrice, lo: p.rows[a]!.price, hi: p.rows[b]!.price + p.step };
}

/** Order in which candidate setups are reported: the closest to a trade first. */
const RANK: Record<string, number> = {
  'first touch': 0,
  approaching: 1,
  'pulling back': 2,
  away: 3,
};

/**
 * One session's zone against every bar after that session. The first departure sets the side for good: price
 * crossing back through the zone touches it, so the POC is tested and cannot turn into a fresh setup the other way.
 */
function judge(
  prior: Bar[],
  profileBars: Bar[],
  after: Bar[],
  todayStart: number,
  sessionsAgo: number,
  price: number | undefined | null,
  cfg: PocConfig,
): PocSetup | null {
  const zone = pocZone(profileBars);
  if (!zone) return null;
  const last = price ?? after[after.length - 1]!.close;
  const upAt = zone.hi * (1 + cfg.awayPct / 100);
  const downAt = zone.lo * (1 - cfg.awayPct / 100);

  let side: 'long' | 'short' | null = null;
  let touches = 0;
  let inside = false;
  let touchedAt = -1;
  let departedAt = -1;
  let broken = false;
  let extreme = Number.NaN;
  let extremeAt = -1;
  after.forEach((b, i) => {
    if (!side) {
      // a CLOSE beyond the line: one wick (a single print on a thin name) is not price leaving the zone
      const up = b.close >= upAt;
      const down = b.close <= downAt;
      if (!up && !down) return;
      side = up ? 'long' : 'short';
      departedAt = i;
      extreme = side === 'long' ? b.high : b.low;
      extremeAt = i;
      return;
    }
    const long = side === 'long';
    // the furthest point only counts until the first touch: after that the setup is the touch itself
    if (touches === 0 && (long ? b.high > extreme : b.low < extreme)) {
      extreme = long ? b.high : b.low;
      extremeAt = i;
    }
    const touching = long ? b.low <= zone.hi : b.high >= zone.lo;
    if (touching && !inside) {
      touches += 1;
      if (touches === 1) touchedAt = i;
    }
    inside = touching;
    if (long ? b.close < zone.lo : b.close > zone.hi) broken = true;
  });

  const base = {
    poc: zone.poc,
    zoneLo: zone.lo,
    zoneHi: zone.hi,
    pocDistPct: (last / zone.poc - 1) * 100,
    sessionsAgo,
    fromTime: prior[0]!.time,
    priorEndTime: prior[prior.length - 1]!.time,
    departTime: departedAt >= 0 ? after[departedAt]!.time : null,
    touchTime: touchedAt >= 0 ? after[touchedAt]!.time : null,
  };
  if (!side) {
    return {
      ...base,
      stage: 'Not departed',
      entry: null,
      stop: null,
      riskPct: null,
      edgeDistPct: null,
      extreme: null,
      extremeTime: null,
      pullbackPct: null,
    };
  }
  const long = side === 'long';
  const entry = long ? zone.hi : zone.lo;
  const stop = long ? zone.lo : zone.hi;
  const edgeDistPct = long ? (last / entry - 1) * 100 : (1 - last / entry) * 100;
  const reach = Math.abs(extreme - entry);
  const pullback = reach > 0 ? Math.max(0, Math.min(1, Math.abs(extreme - last) / reach)) : 0;
  const label = long ? 'Long' : 'Short';
  let stage: PocStage;
  if (broken) stage = 'Broken';
  else if (touches > 1) stage = 'Retested';
  else if (touches === 1)
    stage =
      after.length - 1 - touchedAt < LIVE_BARS && touchedAt >= todayStart
        ? `${label} · first touch`
        : 'Touched earlier';
  else if (edgeDistPct <= cfg.approachPct) stage = `${label} · approaching`;
  else if (pullback >= cfg.pullbackPct / 100) stage = `${label} · pulling back`;
  else stage = `${label} · away`;

  return {
    ...base,
    stage,
    entry,
    stop,
    riskPct: (Math.abs(entry - stop) / entry) * 100,
    edgeDistPct,
    extreme,
    extremeTime: after[extremeAt]!.time,
    pullbackPct: pullback * 100,
  };
}

/** A live setup (first touch, approaching, pulling back, away): its rank, nearest to a trade first; else null. */
export function liveRank(stage: string): number | null {
  return RANK[stage.split(' · ')[1] ?? ''] ?? null;
}

function groupByDay(bars: readonly Bar[], clock: (t: number) => SessionClock): Map<string, Bar[]> {
  const byDay = new Map<string, Bar[]>();
  for (const b of bars) {
    if (!(b.high >= b.low) || !(b.volume >= 0)) continue;
    const day = clock(b.time).day;
    let list = byDay.get(day);
    if (!list) byDay.set(day, (list = []));
    list.push(b);
  }
  return byDay;
}

/**
 * Every recent session's POC zone judged against the bars after it, newest session first: one entry per full
 * session among the last `cfg.lookback`. `bars` are intraday candles (any interval, regular hours) spanning at
 * least two sessions; `price` is the last price (defaults to the last close).
 *
 * `fine` (1-minute bars, regular hours) builds the profile of every session they fully cover, as the chart's
 * session volume profile does, so both draw the same POC; other sessions use `bars`. The move away and the
 * retest are always judged on `bars`.
 */
export function pocCandidates(
  bars: readonly Bar[],
  clock: (t: number) => SessionClock,
  price?: number | null,
  cfg: PocConfig = POC_DEFAULTS,
  fine?: readonly Bar[] | null,
): PocSetup[] {
  const byDay = groupByDay(bars, clock);
  const fineByDay = fine?.length ? groupByDay(fine, clock) : null;
  const fineFrom = fine?.length ? fine[0]!.time : Infinity;
  const fineTo = fine?.length ? fine[fine.length - 1]!.time : -Infinity;
  const days = [...byDay.keys()].sort();
  if (days.length < 2) return [];
  const today = byDay.get(days[days.length - 1]!)!;

  const out: PocSetup[] = [];
  let full = 0;
  // newest first; `after` grows backwards one session at a time
  let after: Bar[] = today;
  for (let d = days.length - 2; d >= 0 && full < cfg.lookback; d -= 1) {
    const session = byDay.get(days[d]!)!;
    if (session.length >= MIN_PRIOR_BARS) {
      full += 1;
      const covered = fineFrom <= session[0]!.time && fineTo >= session[session.length - 1]!.time;
      const profileBars = (covered && fineByDay?.get(days[d]!)) || session;
      const s = judge(
        session,
        profileBars,
        after,
        after.length - today.length,
        days.length - 1 - d,
        price,
        cfg,
      );
      if (s) out.push(s);
    }
    after = [...session, ...after];
  }
  return out;
}

/** Of several candidates, the live setup closest to a trade (ties: the nearest zone); null if none is live. */
export function bestSetup(list: readonly PocSetup[]): PocSetup | null {
  let best: PocSetup | null = null;
  let bestRank = Infinity;
  for (const s of list) {
    const rank = liveRank(s.stage);
    if (rank == null) continue;
    if (
      rank < bestRank ||
      (rank === bestRank && (s.edgeDistPct ?? Infinity) < (best!.edgeDistPct ?? Infinity))
    ) {
      best = s;
      bestRank = rank;
    }
  }
  return best;
}

/**
 * The best untested-POC setup over the last `LOOKBACK_SESSIONS` full sessions. Without a live setup it reports the
 * latest session's zone and why it is not one (touched, retested, broken, not departed). Returns null without a
 * usable previous session.
 */
export function pocSetup(
  bars: readonly Bar[],
  clock: (t: number) => SessionClock,
  price?: number | null,
  fine?: readonly Bar[] | null,
): PocSetup | null {
  const list = pocCandidates(bars, clock, price, POC_DEFAULTS, fine);
  return bestSetup(list) ?? list[0] ?? null;
}
