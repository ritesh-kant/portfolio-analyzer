import type { Bar } from '../tv/types.ts';
import type { SessionClock } from './relvol.ts';
import type { ScreenerRow } from './types.ts';

/**
 * Paper-tracking a POC retest setup: record it when the scanner shows it, then replay the candles that came after
 * to see whether price did what the setup said. No money, no costs: it answers "did the long go up / the short go
 * down", in units of the setup's own risk (R = entry to stop).
 *
 * The rules, fixed in advance so a result can't be argued afterwards:
 *  - window: from the first candle that starts after the setup was added, to the close of the NEXT session (added
 *    mid-session → the rest of today and all of tomorrow; added after the close → all of the next session);
 *  - fill: a first touch is already at the entry, so it fills at the open of the first candle in the window; any
 *    other stage is a resting order at the entry and fills when a candle reaches it (at the open if it gaps past);
 *  - exits: the stop, or a target `TARGET_R` × risk beyond the fill. A candle that reaches both counts as the stop
 *    (we can't tell which came first), and the fill candle can only stop out, never hit the target;
 *  - still open at the end of the window: closed at that last close.
 */
export const TARGET_R = 2;

export interface TrackedSetup {
  /** symbol + the zone's session: the same setup can't be tracked twice, the next one on another POC can. */
  id: string;
  symbol: string;
  name: string;
  market: string;
  long: boolean;
  /** The scanner's stage when it was added, e.g. "Short · first touch". */
  stage: string;
  entry: number;
  stop: number;
  poc: number;
  priceAtAdd: number;
  /** Unix seconds. */
  addedAt: number;
  /** The scanner row as it was, so opening it on the chart draws the same zone. */
  row: ScreenerRow;
  /** Stored once the result can no longer change. */
  final?: Evaluation;
}

export type Outcome = 'waiting' | 'not filled' | 'open' | 'target' | 'stopped' | 'closed';

export interface Evaluation {
  outcome: Outcome;
  /** True once the result can no longer change: an exit, or the window has closed. */
  done: boolean;
  fillPrice: number | null;
  fillTime: number | null;
  exitPrice: number | null;
  exitTime: number | null;
  /** At the exit, or marked at the last close while open; in R. */
  r: number | null;
  /** The furthest price went in the trade's favour after the fill, in R. */
  bestR: number | null;
  /** The same as `r`, as a % move from the fill in the trade's direction. */
  pct: number | null;
  /** The session the window ends with (null until it is known). */
  endDay: string | null;
}

export const trackId = (row: Pick<ScreenerRow, 'symbol' | 'pocFrom'>) =>
  `${row.symbol}|${row.pocFrom ?? ''}`;

/** A tracked setup from a scanner row; null when the row has no side, entry or stop. */
export function trackRow(row: ScreenerRow, market: string, now: number): TrackedSetup | null {
  const long = row.pocStage?.startsWith('Long')
    ? true
    : row.pocStage?.startsWith('Short')
      ? false
      : null;
  if (long == null || row.pocEntry == null || row.pocStop == null || row.price == null) return null;
  return {
    id: trackId(row),
    symbol: row.symbol,
    name: row.name,
    market,
    long,
    stage: row.pocStage!,
    entry: row.pocEntry,
    stop: row.pocStop,
    poc: row.pocPrice ?? row.pocEntry,
    priceAtAdd: row.price,
    addedAt: Math.floor(now / 1000),
    row,
  };
}

const PENDING: Evaluation = {
  outcome: 'waiting',
  done: false,
  fillPrice: null,
  fillTime: null,
  exitPrice: null,
  exitTime: null,
  r: null,
  bestR: null,
  pct: null,
  endDay: null,
};

/**
 * Replays `bars` (regular-hours intraday candles, any interval) against a tracked setup. `now` (unix seconds) and
 * `closeMin` (the session's closing time, minutes after midnight on the exchange clock) decide whether the window
 * has finished.
 */
export function evaluate(
  t: Pick<TrackedSetup, 'long' | 'stage' | 'entry' | 'stop' | 'addedAt'>,
  bars: readonly Bar[],
  clock: (t: number) => SessionClock,
  now: number,
  closeMin: number,
): Evaluation {
  const after = bars.filter((b) => b.time >= t.addedAt && b.high >= b.low);
  if (!after.length) return PENDING;

  const addDay = clock(t.addedAt).day;
  const days: string[] = [];
  for (const b of after) {
    const d = clock(b.time).day;
    if (days[days.length - 1] !== d) days.push(d);
  }
  const endDay = days[0] === addDay ? (days[1] ?? null) : days[0]!;
  const window = endDay ? after.filter((b) => clock(b.time).day <= endDay) : after;
  const today = clock(now);
  const complete =
    endDay != null && (endDay < today.day || (endDay === today.day && today.min >= closeMin));

  const { long } = t;
  const dir = long ? 1 : -1;
  const risk = Math.abs(t.entry - t.stop);
  if (!(risk > 0)) return { ...PENDING, endDay };

  // the fill
  let i = 0;
  let fill: number | null = null;
  if (t.stage.endsWith('first touch')) {
    fill = window[0]!.open;
  } else {
    for (; i < window.length; i += 1) {
      const b = window[i]!;
      if (long ? b.low <= t.entry : b.high >= t.entry) {
        fill = long ? Math.min(b.open, t.entry) : Math.max(b.open, t.entry);
        break;
      }
    }
  }
  if (fill == null) {
    return { ...PENDING, outcome: complete ? 'not filled' : 'waiting', done: complete, endDay };
  }
  const fillTime = window[i]!.time;
  const rOf = (p: number) => (dir * (p - fill!)) / risk;
  const target = fill + dir * TARGET_R * risk;
  const result = (
    outcome: Outcome,
    price: number,
    time: number | null,
    best: number,
    done: boolean,
  ) => ({
    outcome,
    done,
    fillPrice: fill,
    fillTime,
    exitPrice: price,
    exitTime: time,
    r: rOf(price),
    bestR: Math.max(best, rOf(price)),
    pct: ((dir * (price - fill!)) / fill!) * 100,
    endDay,
  });

  let best = 0;
  for (let k = i; k < window.length; k += 1) {
    const b = window[k]!;
    const stopped = long ? b.low <= t.stop : b.high >= t.stop;
    if (stopped) {
      // a candle that opened past the stop exits at its open, not at the stop (a gap through the entry and the
      // stop fills and exits at the same open)
      const px = long ? Math.min(t.stop, b.open) : Math.max(t.stop, b.open);
      return result('stopped', px, b.time, best, true);
    }
    if (k > i) {
      if (long ? b.high >= target : b.low <= target) {
        const px = long ? Math.max(target, b.open) : Math.min(target, b.open);
        return result('target', px, b.time, best, true);
      }
      best = Math.max(best, rOf(long ? b.high : b.low));
    } else {
      best = Math.max(best, rOf(b.close));
    }
  }
  const last = window[window.length - 1]!;
  return complete
    ? result('closed', last.close, last.time, best, true)
    : result('open', last.close, null, best, false);
}

export interface TrackSummary {
  total: number;
  finished: number;
  targets: number;
  stops: number;
  closed: number;
  notFilled: number;
  /** Finished trades that ended in profit (in R, before costs). */
  rightWay: number;
  traded: number;
  totalR: number;
  /** Middle result in R: one trade with a tiny stop can swing the total by ±10R, the median it can't. */
  medianR: number | null;
  /** Average % move from the fill in the trade's direction: the same in every stop width. */
  avgPct: number | null;
}

export function summarize(evals: readonly Evaluation[]): TrackSummary {
  const s: TrackSummary = {
    total: evals.length,
    finished: 0,
    targets: 0,
    stops: 0,
    closed: 0,
    notFilled: 0,
    rightWay: 0,
    traded: 0,
    totalR: 0,
    medianR: null,
    avgPct: null,
  };
  const rs: number[] = [];
  let pctSum = 0;
  for (const e of evals) {
    if (!e.done) continue;
    s.finished += 1;
    if (e.outcome === 'not filled') {
      s.notFilled += 1;
      continue;
    }
    if (e.outcome === 'target') s.targets += 1;
    if (e.outcome === 'stopped') s.stops += 1;
    if (e.outcome === 'closed') s.closed += 1;
    s.traded += 1;
    s.totalR += e.r ?? 0;
    rs.push(e.r ?? 0);
    pctSum += e.pct ?? 0;
    if ((e.r ?? 0) > 0) s.rightWay += 1;
  }
  if (rs.length) {
    rs.sort((a, b) => a - b);
    const m = rs.length >> 1;
    s.medianR = rs.length % 2 ? rs[m]! : (rs[m - 1]! + rs[m]!) / 2;
    s.avgPct = pctSum / rs.length;
  }
  return s;
}
