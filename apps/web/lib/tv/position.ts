import type { Bar } from './types';
import type { Drawing, DrawingKind, Pt, TimeAxis } from './drawings';

/**
 * TradingView-style Long / Short position tools.
 *
 * A position is one drawing with four absolute anchors:
 *   pts[0] entry, left edge          (t0, entry price)
 *   pts[1] entry, right edge         (t1, entry price)
 *   pts[2] profit target             (t0, target price)   only the price matters
 *   pts[3] stop loss                 (t0, stop price)     only the price matters
 * so translating every anchor by the same (dt, dp) moves the whole tool, exactly like the other drawings.
 */
export const isPosition = (kind: DrawingKind): kind is 'long' | 'short' =>
  kind === 'long' || kind === 'short';

export const POSITION_PROFIT = '#089981';
export const POSITION_STOP = '#f23645';

/** TradingView's default risk model. */
export const POSITION_DEFAULTS = { account: 1000, lot: 1, riskPct: 25 };

export function positionPts(
  kind: 'long' | 'short',
  anchor: Pt,
  widthSecs: number,
  riskDist: number,
  rewardDist: number,
): Pt[] {
  const dir = kind === 'long' ? 1 : -1;
  return [
    { t: anchor.t, p: anchor.p },
    { t: anchor.t + widthSecs, p: anchor.p },
    { t: anchor.t, p: anchor.p + dir * rewardDist },
    { t: anchor.t, p: anchor.p - dir * riskDist },
  ];
}

/** Box width in candles; legacy drawings (no `span`) derive it from their right anchor. */
export function positionSpan(d: Drawing, axis: TimeAxis): number {
  if (d.span != null && d.span > 0) return d.span;
  const [a, b] = d.pts as [Pt, Pt, Pt, Pt];
  return Math.max(1, axis.toLogical(b.t) - axis.toLogical(a.t));
}

/** Time of the box's right edge, `span` candles after the entry. */
export function positionEnd(d: Drawing, axis: TimeAxis): number {
  const a = d.pts[0]!;
  return axis.toTime(axis.toLogical(a.t) + positionSpan(d, axis));
}

/** Re-measures `span` after a handle drag, while the on-screen right edge is still where the user put it. */
export function withSpan(d: Drawing, axis: TimeAxis): Drawing {
  const [a, b] = d.pts as [Pt, Pt, Pt, Pt];
  return { ...d, span: Math.max(1, axis.toLogical(b.t) - axis.toLogical(a.t)) };
}

export type PositionStatus = 'open' | 'target' | 'stop';

export interface PositionStats {
  isLong: boolean;
  entry: number;
  target: number;
  stop: number;
  /** Absolute price distances. */
  reward: number;
  risk: number;
  /** reward / risk; 0 when there is no stop distance. */
  rr: number;
  targetPct: number;
  stopPct: number;
  targetTicks: number;
  stopTicks: number;
  qty: number;
  status: PositionStatus;
  /** Currency P&L: realised once a level was touched, otherwise marked to the last bar in range. */
  pnl: number;
}

/**
 * Walks the candles after the entry candle, up to the right edge of the box. The first one to
 * touch the target or the stop closes the trade; when one candle spans both we cannot know the
 * order, so the stop is assumed (the pessimistic reading).
 */
export function positionStats(d: Drawing, bars: Bar[], tick: number, end?: number): PositionStats {
  const [a, b, tp, sl] = d.pts as [Pt, Pt, Pt, Pt];
  const isLong = d.kind === 'long';
  const dir = isLong ? 1 : -1;
  const entry = a.p;
  const target = tp.p;
  const stop = sl.p;
  const reward = Math.abs(target - entry);
  const risk = Math.abs(entry - stop);
  const account = d.account ?? POSITION_DEFAULTS.account;
  const lot = d.lot && d.lot > 0 ? d.lot : POSITION_DEFAULTS.lot;
  const riskPct = d.riskPct ?? POSITION_DEFAULTS.riskPct;
  const qty = risk > 0 ? Math.floor((account * (riskPct / 100)) / risk / lot) * lot : 0;

  let status: PositionStatus = 'open';
  let exit: number | null = null;
  const t0 = Math.min(a.t, b.t);
  const t1 = end ?? Math.max(a.t, b.t);
  let last: number | null = null;
  for (const bar of bars) {
    if (bar.time <= t0) continue;
    if (bar.time > t1) break;
    last = bar.close;
    const hitStop = isLong ? bar.low <= stop : bar.high >= stop;
    const hitTarget = isLong ? bar.high >= target : bar.low <= target;
    if (hitStop) {
      status = 'stop';
      exit = stop;
      break;
    }
    if (hitTarget) {
      status = 'target';
      exit = target;
      break;
    }
  }
  const mark = exit ?? last ?? entry;
  return {
    isLong,
    entry,
    target,
    stop,
    reward,
    risk,
    rr: risk > 0 ? reward / risk : 0,
    targetPct: entry ? (reward / entry) * 100 : 0,
    stopPct: entry ? (risk / entry) * 100 : 0,
    targetTicks: tick > 0 ? Math.round(reward / tick) : 0,
    stopTicks: tick > 0 ? Math.round(risk / tick) : 0,
    qty,
    status,
    pnl: (mark - entry) * dir * qty,
  };
}

/** Smallest distance a level may keep from the entry, so the box never collapses. */
const MIN_GAP = 1e-9;

/**
 * Drag one handle (0 entry-left, 1 entry-right, 2 target, 3 stop) to `pt`. The target and the stop
 * stay on their own side of the entry, as in TradingView; moving the entry leaves both levels where
 * they are (the risk/reward changes), and the right edge only ever changes the width.
 */
export function dragPositionHandle(d: Drawing, handle: number, pt: Pt): Drawing {
  const [a, b, tp, sl] = d.pts as [Pt, Pt, Pt, Pt];
  const long = d.kind === 'long';
  const lo = Math.min(tp.p, sl.p);
  const hi = Math.max(tp.p, sl.p);
  const t0 = a.t;
  if (handle === 0) {
    const entry = Math.min(hi - MIN_GAP, Math.max(lo + MIN_GAP, pt.p));
    const t = Math.min(pt.t, b.t - 1);
    return {
      ...d,
      pts: [
        { t, p: entry },
        { t: b.t, p: entry },
        { t, p: tp.p },
        { t, p: sl.p },
      ],
    };
  }
  if (handle === 1) {
    return { ...d, pts: [a, { t: Math.max(pt.t, t0 + 1), p: a.p }, tp, sl] };
  }
  if (handle === 2) {
    const p = long ? Math.max(pt.p, a.p + MIN_GAP) : Math.min(pt.p, a.p - MIN_GAP);
    return { ...d, pts: [a, b, { t: tp.t, p }, sl] };
  }
  if (handle === 3) {
    const p = long ? Math.min(pt.p, a.p - MIN_GAP) : Math.max(pt.p, a.p + MIN_GAP);
    return { ...d, pts: [a, b, tp, { t: sl.t, p }] };
  }
  return d;
}

/** Type levels in the settings dialog: any of entry / target / stop, validated against the side. */
export function editPositionLevels(
  d: Drawing,
  edit: { entry?: number; target?: number; stop?: number },
): Drawing {
  const [a, b, tp, sl] = d.pts as [Pt, Pt, Pt, Pt];
  const long = d.kind === 'long';
  const entry = edit.entry ?? a.p;
  const target = edit.target ?? tp.p;
  const stop = edit.stop ?? sl.p;
  const ok = long ? stop < entry && entry < target : target < entry && entry < stop;
  if (!ok || ![entry, target, stop].every(Number.isFinite)) return d;
  return {
    ...d,
    pts: [
      { t: a.t, p: entry },
      { t: b.t, p: entry },
      { t: tp.t, p: target },
      { t: sl.t, p: stop },
    ],
  };
}
