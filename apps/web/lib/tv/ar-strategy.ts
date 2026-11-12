import {
  type AvrDay,
  type Bias,
  type EnvironmentRead,
  FIB_LEVELS,
  type Pivot,
  environment,
  structureBias,
  swings,
} from './ar.ts';
import { atr } from './indicators.ts';
import { aggregateBars, wallClock } from './timeframes.ts';
import type { Bar } from './types';
import { buildProfile } from './volume-profile.ts';

/**
 * The Auction Resolution strategy (phase 2 of research/specs/ar-model-chart-strategy-plan.md): his four steps
 * applied candle by candle, then an entry, stop, target and exit.
 *
 * Walks the candles in order and never reads a candle after the one it is deciding on, so the chart, a replay and
 * the backtest all see the same trades. Where the course is discretionary the rule here is OURS and says so:
 *
 *   1 Environment  trend = structure on 1-hour candles (or the chart's own when it is 1h+); expansion / rotation /
 *                  compression from `environment()`. Compression fails; rotation or a flat trend only warns.
 *   2 Location     the latest swing leg in the trend's direction; price must reach its 0.705–0.886 zone. A close
 *                  past 0.886 cancels it, price beyond the leg's end before any retracement retires it. A zone
 *                  inside the leg's value area warns (fails when `requireOutsideValue`).
 *   3 Path         from the day's Asia-range levels: an AVR+ / Max band between entry and target warns (the move
 *                  tends to tire there), price already past Max in the trade's direction fails.
 *   4 Confirmation ESTIMATED from candles (no bid/ask data): after the touch, a candle that closes in the trade's
 *                  direction AND beyond the extreme candle's other end (aggression failed, pressure flipped), inside
 *                  his New York window. Entry: a pull-back to the middle of that candle within a few candles (his
 *                  "entry candle pulls back into the confirmation candle"), or its close.
 *
 * Stop: beyond the extreme since the touch, plus a small buffer. Target: the leg's end. One trade per leg, one at a
 * time, flat by the end of the New York session. Results are in points, before costs.
 */

export interface ArStrategyCfg {
  /** Swing size, × ATR 14 of the chart's candles. */
  swingMult: number;
  /** Rows of the leg's volume profile (value area). */
  valueRows: number;
  /** Candles the environment read looks at. */
  envWindow: number;
  /** Confirmation candles must START inside this window, minutes after midnight New York time. */
  windowStart: number;
  windowEnd: number;
  /** Close any open trade at the close of the candle that ends at or after this minute (New York). */
  flatAt: number;
  /** Skip setups whose reward ÷ risk to the target is below this. */
  minRR: number;
  /** How to enter after confirmation. */
  entry: 'pullback' | 'close';
  /** Pull-back entry: candles the order waits before it is cancelled. */
  entryWait: number;
  /** Candles after the first touch that a confirmation may still come in. */
  confirmWithin: number;
  /** Stop buffer beyond the extreme, × ATR 14. */
  stopBuffer: number;
  /** A zone inside the value area fails instead of warning. */
  requireOutsideValue: boolean;
  /** Take a setup even when a step only warns (never when one fails). */
  allowWarnings: boolean;
}

export const AR_STRATEGY_DEFAULTS: ArStrategyCfg = {
  swingMult: 2,
  valueRows: 30,
  envWindow: 24,
  windowStart: 9 * 60 + 30,
  windowEnd: 11 * 60,
  flatAt: 16 * 60,
  minRR: 1.5,
  entry: 'pullback',
  entryWait: 3,
  confirmWithin: 6,
  stopBuffer: 0.1,
  requireOutsideValue: false,
  allowWarnings: true,
};

export type CheckState = 'pass' | 'warn' | 'fail' | 'wait';
export interface Check {
  state: CheckState;
  text: string;
}
export type StepKey = 'environment' | 'location' | 'path' | 'confirmation';
export const STEPS: { key: StepKey; label: string }[] = [
  { key: 'environment', label: 'Environment' },
  { key: 'location', label: 'Location' },
  { key: 'path', label: 'Path' },
  { key: 'confirmation', label: 'Confirmation' },
];

/** The checklist as it stood at the close of one candle. */
export interface ArRead {
  index: number;
  checks: Record<StepKey, Check>;
  /** The trade open at this candle, if any. */
  tradeId: number | null;
}

export interface ArTrade {
  id: number;
  dir: 'long' | 'short';
  /** First candle into the zone, the confirmation candle, the fill. */
  touchIndex: number;
  confirmIndex: number;
  entryIndex: number;
  entry: number;
  stop: number;
  target: number;
  exitIndex: number | null;
  exit: number | null;
  outcome: 'target' | 'stop' | 'time' | 'open';
  /** Result in price points (positive = profit) and in R (multiples of the initial risk). Before costs. */
  points: number | null;
  r: number | null;
  /** Steps that only warned when the trade was taken. */
  warnings: string[];
}

/** A zone touch that did not become a trade, and the first reason why. */
export interface ArSkip {
  index: number;
  /** Close of that candle: where the chart marks it. */
  price: number;
  dir: 'long' | 'short';
  step: StepKey | 'entry';
  reason: string;
}

export interface ArStrategyResult {
  reads: ArRead[];
  trades: ArTrade[];
  skips: ArSkip[];
}

interface Leg {
  key: string;
  from: Pivot;
  to: Pivot;
  dir: 'up' | 'down';
  zoneNear: number; // 0.705: the edge price reaches first
  zoneFar: number; // 0.886: the cancel level
  target: number;
  insideValue: boolean | null;
  touchIndex: number | null;
  /** Candle with the extreme since the touch (the "absorption" candle). */
  extremeIndex: number | null;
  dead: 'invalidated' | 'extended' | 'used' | 'expired' | null;
}

const minuteOf = (t: number, tz: string) => Math.floor((((wallClock(t, tz) % 86400) + 86400) % 86400) / 60);
const fmtTime = (t: number, tz: string) => {
  const m = minuteOf(t, tz);
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};

function makeLeg(bars: Bar[], from: Pivot, to: Pivot, rows: number): Leg {
  const span = to.price - from.price;
  const at = (lvl: number) => to.price - lvl * span;
  const zoneNear = at(FIB_LEVELS[2]); // 0.705
  const zoneFar = at(FIB_LEVELS[4]); // 0.886
  const dir = to.kind === 'H' ? 'up' : 'down';
  // value area of the leg's own candles (all before the leg ended: no look-ahead)
  const profile = buildProfile(bars, from.index, to.index, rows);
  const insideValue = profile
    ? dir === 'up'
      ? zoneNear > profile.val
      : zoneNear < profile.vah
    : null;
  return {
    key: `${from.index}-${to.index}`,
    from,
    to,
    dir,
    zoneNear,
    zoneFar,
    target: to.price,
    insideValue,
    touchIndex: null,
    extremeIndex: null,
    dead: null,
  };
}

/** Trend on 1-hour candles as known at each chart candle's close (the chart's own swings when it is 1h or coarser). */
function biasAsOf(bars: Bar[], chartSecs: number, timeZone: string, mult: number): Bias[] {
  const out: Bias[] = new Array(bars.length).fill('neutral');
  if (!bars.length) return out;
  let tfBars = bars;
  let tfSecs = chartSecs;
  if (chartSecs < 3600) {
    tfBars = aggregateBars(bars, 3600, timeZone);
    tfSecs = 3600;
  }
  const pivots = swings(tfBars, mult).pivots;
  // when each pivot becomes known, in unix seconds: the close of the candle that confirmed it
  const known = pivots.map((p) => tfBars[p.confirmedAt]!.time + tfSecs);
  let k = 0;
  let bias: Bias = 'neutral';
  for (let i = 0; i < bars.length; i++) {
    const close = bars[i]!.time + chartSecs;
    let moved = false;
    while (k < pivots.length && known[k]! <= close) {
      k++;
      moved = true;
    }
    if (moved) bias = structureBias(pivots.slice(0, k));
    out[i] = bias;
  }
  return out;
}

/** The AVR day whose levels are live at candle i. */
function avrAt(days: AvrDay[], i: number): AvrDay | null {
  for (let k = days.length - 1; k >= 0; k--) {
    const d = days[k]!;
    if (d.from <= i && i <= d.to) return d;
    if (d.to < i) return null;
  }
  return null;
}

export function runArStrategy(
  bars: Bar[],
  opts: {
    timeZone: string;
    chartSecs: number;
    avr?: AvrDay[];
    /** The last candle is still forming (live): it may not confirm anything yet. */
    formingLast?: boolean;
    cfg?: Partial<ArStrategyCfg>;
  },
): ArStrategyResult {
  const cfg = { ...AR_STRATEGY_DEFAULTS, ...opts.cfg };
  const { timeZone: tz, chartSecs } = opts;
  const avr = opts.avr ?? [];
  const reads: ArRead[] = [];
  const trades: ArTrade[] = [];
  const skips: ArSkip[] = [];
  if (bars.length < 3) return { reads, trades, skips };

  const pivots = swings(bars, cfg.swingMult).pivots;
  const bias = biasAsOf(bars, chartSecs, tz, cfg.swingMult);
  const a14 = atr(bars, 14);
  const legs = new Map<string, Leg>();
  let known = 0; // pivots confirmed so far
  let active: Leg | null = null;
  let open: ArTrade | null = null;
  let pending: { leg: Leg; dir: 'long' | 'short'; limit: number; stop: number; confirmIndex: number; until: number; warnings: string[] } | null = null;
  let nextId = 1;

  const lastDecidable = opts.formingLast ? bars.length - 2 : bars.length - 1;

  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]!;
    const minute = minuteOf(b.time, tz);
    const closesAt = minute + chartSecs / 60;
    while (known < pivots.length && pivots[known]!.confirmedAt <= i) known++;
    const trend = bias[i]!;

    // ── a session break (gap over an hour, e.g. after an early close) flattens at the last candle before it ──
    if (open && i > open.entryIndex && b.time - bars[i - 1]!.time > 3600) {
      closeTrade(open, i - 1, bars[i - 1]!.close, 'time');
      open = null;
    }

    // ── manage the open trade ──
    if (open) {
      const long = open.dir === 'long';
      const hitStop = long ? b.low <= open.stop : b.high >= open.stop;
      const hitTarget = long ? b.high >= open.target : b.low <= open.target;
      const onEntryBar = i === open.entryIndex;
      let exit: number | null = null;
      if (hitStop) {
        exit = long ? Math.min(open.stop, b.open) : Math.max(open.stop, b.open);
        if (onEntryBar) exit = open.stop;
        open.outcome = 'stop';
      } else if (hitTarget && !onEntryBar) {
        exit = long ? Math.max(open.target, b.open) : Math.min(open.target, b.open);
        open.outcome = 'target';
      } else if (closesAt >= cfg.flatAt) {
        exit = b.close;
        open.outcome = 'time';
      }
      if (exit != null) {
        closeTrade(open, i, exit, open.outcome as ArTrade['outcome']);
        open = null;
      }
    }

    // ── fill a pending pull-back order ──
    if (!open && pending) {
      const long = pending.dir === 'long';
      if (i > pending.until) {
        skips.push({ index: pending.confirmIndex, price: bars[pending.confirmIndex]!.close, dir: pending.dir, step: 'entry', reason: `pull-back to ${pending.limit.toFixed(2)} never came` });
        pending.leg.dead = 'expired';
        pending = null;
      } else if (long ? b.low <= pending.limit : b.high >= pending.limit) {
        const fill = long ? Math.min(pending.limit, b.open) : Math.max(pending.limit, b.open);
        open = openTrade(pending.leg, pending.dir, fill, pending.stop, pending.confirmIndex, i, pending.warnings);
        pending = null;
        // the fill candle can still stop out (conservatively, never reach the target on the same candle)
        const hitStop = long ? b.low <= open.stop : b.high >= open.stop;
        if (hitStop) {
          closeTrade(open, i, open.stop, 'stop');
          open = null;
        }
      }
    }

    // ── choose the active leg: sticky once touched, else the latest leg in the trend's direction ──
    const keepActive = active && !active.dead && active.touchIndex != null;
    if (!keepActive) {
      active = null;
      for (let k = known - 1; k >= 1; k--) {
        const to = pivots[k]!;
        const dir = to.kind === 'H' ? 'up' : 'down';
        if (trend === 'bull' && dir !== 'up') continue;
        if (trend === 'bear' && dir !== 'down') continue;
        const key = `${pivots[k - 1]!.index}-${to.index}`;
        let leg = legs.get(key);
        if (!leg) {
          leg = makeLeg(bars, pivots[k - 1]!, to, cfg.valueRows);
          // catch up on the candles between the leg's end and now (all in the past)
          for (let j = to.index + 1; j < i; j++) stepLeg(leg, bars, j);
          legs.set(key, leg);
        }
        active = leg;
        break;
      }
    }
    const leg = active;
    if (leg && !leg.dead) stepLeg(leg, bars, i);

    // ── the four steps at this candle ──
    const env = environment(bars, cfg.envWindow, i + 1);
    const checks: Record<StepKey, Check> = {
      environment: envCheck(env, trend, leg),
      location: locationCheck(leg, b, cfg),
      path: pathCheck(leg, b.close, avrAt(avr, i)),
      confirmation: { state: 'wait', text: 'waiting for price to reach the zone' },
    };
    if (open) {
      checks.confirmation = { state: 'pass', text: `in a ${open.dir} from ${open.entry.toFixed(2)}` };
    } else if (pending) {
      checks.confirmation = { state: 'pass', text: `confirmed; order waiting at ${pending.limit.toFixed(2)}` };
    } else if (leg && !leg.dead && leg.touchIndex != null && leg.extremeIndex != null) {
      const long = leg.dir === 'up';
      const ext = bars[leg.extremeIndex]!;
      const flips = i > leg.extremeIndex && (long ? b.close > b.open && b.close > ext.high : b.close < b.open && b.close < ext.low);
      const inWindow = minute >= cfg.windowStart && minute < cfg.windowEnd;
      if (i - leg.touchIndex > cfg.confirmWithin) {
        leg.dead = 'expired';
        skips.push({ index: i, price: bars[i]!.close, dir: long ? 'long' : 'short', step: 'confirmation', reason: `no confirmation within ${cfg.confirmWithin} candles of the touch` });
        checks.confirmation = { state: 'fail', text: 'no confirmation came in time' };
      } else if (!flips || i > lastDecidable) {
        checks.confirmation = {
          state: 'wait',
          text: long
            ? `in the zone; waiting for a green candle closing above ${ext.high.toFixed(2)}`
            : `in the zone; waiting for a red candle closing below ${ext.low.toFixed(2)}`,
        };
      } else if (!inWindow) {
        checks.confirmation = { state: 'fail', text: `confirmed at ${fmtTime(b.time, tz)}, outside his ${windowText(cfg)} window` };
        skips.push({ index: i, price: bars[i]!.close, dir: long ? 'long' : 'short', step: 'confirmation', reason: `confirmation outside ${windowText(cfg)} New York` });
        leg.dead = 'used';
      } else {
        checks.confirmation = { state: 'pass', text: `pressure flipped at ${fmtTime(b.time, tz)} (estimated from candles)` };
        const decision = decide(checks, cfg);
        const dir = long ? 'long' : 'short';
        const buffer = (a14[i] ?? Math.abs(ext.high - ext.low)) * cfg.stopBuffer;
        const stop = long ? ext.low - buffer : ext.high + buffer;
        const entryPx = cfg.entry === 'close' ? b.close : (b.high + b.low) / 2;
        const rr = Math.abs(leg.target - entryPx) / Math.max(1e-9, Math.abs(entryPx - stop));
        leg.dead = 'used';
        if (decision.fail) {
          skips.push({ index: i, price: bars[i]!.close, dir, step: decision.fail.step, reason: decision.fail.text });
        } else if (rr < cfg.minRR) {
          skips.push({ index: i, price: bars[i]!.close, dir, step: 'entry', reason: `reward ÷ risk ${rr.toFixed(2)} below ${cfg.minRR}` });
        } else if (cfg.entry === 'close') {
          // market order at this (complete) close: managed from the next candle
          open = openTrade(leg, dir, b.close, stop, i, i, decision.warnings);
        } else {
          pending = { leg, dir, limit: entryPx, stop, confirmIndex: i, until: i + cfg.entryWait, warnings: decision.warnings };
        }
      }
    } else if (leg?.dead === 'invalidated') {
      checks.confirmation = { state: 'fail', text: 'zone cancelled' };
    }

    reads.push({ index: i, checks, tradeId: open?.id ?? null });
  }
  return { reads, trades, skips };

  function closeTrade(t: ArTrade, index: number, exit: number, outcome: ArTrade['outcome']) {
    t.exitIndex = index;
    t.exit = exit;
    t.outcome = outcome;
    const risk = Math.abs(t.entry - t.stop);
    t.points = t.dir === 'long' ? exit - t.entry : t.entry - exit;
    t.r = risk > 0 ? t.points / risk : null;
  }

  function openTrade(leg: Leg, dir: 'long' | 'short', entry: number, stop: number, confirmIndex: number, entryIndex: number, warnings: string[]): ArTrade {
    const t: ArTrade = {
      id: nextId++,
      dir,
      touchIndex: leg.touchIndex ?? confirmIndex,
      confirmIndex,
      entryIndex,
      entry,
      stop,
      target: leg.target,
      exitIndex: null,
      exit: null,
      outcome: 'open',
      points: null,
      r: null,
      warnings,
    };
    trades.push(t);
    return t;
  }
}

/** Advance a leg's state by one candle: touch, extreme since touch, cancel, retire. */
function stepLeg(leg: Leg, bars: Bar[], i: number) {
  if (leg.dead) return;
  const b = bars[i]!;
  const up = leg.dir === 'up';
  if (leg.touchIndex == null) {
    if (up ? b.high > leg.to.price : b.low < leg.to.price) {
      leg.dead = 'extended';
      return;
    }
    if (up ? b.low <= leg.zoneNear : b.high >= leg.zoneNear) {
      leg.touchIndex = i;
      leg.extremeIndex = i;
    }
  } else if (leg.extremeIndex != null && (up ? b.low < bars[leg.extremeIndex]!.low : b.high > bars[leg.extremeIndex]!.high)) {
    leg.extremeIndex = i;
  }
  if (leg.touchIndex != null && (up ? b.close < leg.zoneFar : b.close > leg.zoneFar)) leg.dead = 'invalidated';
}

const windowText = (cfg: ArStrategyCfg) => {
  const f = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  return `${f(cfg.windowStart)}–${f(cfg.windowEnd)}`;
};

const trendWord: Record<Bias, string> = { bull: 'up', bear: 'down', neutral: 'mixed' };

function envCheck(env: EnvironmentRead | null, trend: Bias, leg: Leg | null): Check {
  const t = `1h trend ${trendWord[trend]}`;
  if (!env) return { state: 'wait', text: `${t}; not enough candles to read the environment` };
  if (env.env === 'compression') return { state: 'fail', text: `${t}; choppy (compression): his rule is no trade` };
  if (trend === 'neutral') return { state: 'warn', text: `${t}; ${env.env === 'expansion' ? 'trending' : 'ranging'} but no clear direction` };
  const aligned = leg && ((trend === 'bull' && leg.dir === 'up') || (trend === 'bear' && leg.dir === 'down'));
  if (env.env === 'rotation') return { state: 'warn', text: `${t}; ranging, so pullbacks follow through less` };
  return { state: aligned ? 'pass' : 'warn', text: `${t}; trending (expansion)` };
}

function locationCheck(leg: Leg | null, b: Bar, cfg: ArStrategyCfg): Check {
  if (!leg) return { state: 'wait', text: 'no swing leg in the trend’s direction yet' };
  const side = leg.dir === 'up' ? 'discount' : 'premium';
  if (leg.dead === 'invalidated') return { state: 'fail', text: `closed past 0.886 (${leg.zoneFar.toFixed(2)}): zone cancelled` };
  if (leg.dead === 'extended') return { state: 'wait', text: 'price ran past the swing before retracing: waiting for a new leg' };
  if (leg.dead === 'used' || leg.dead === 'expired') return { state: 'wait', text: 'this leg’s setup is done: waiting for a new leg' };
  const value = leg.insideValue
    ? { state: (cfg.requireOutsideValue ? 'fail' : 'warn') as CheckState, note: 'zone overlaps the value area' }
    : { state: 'pass' as CheckState, note: `zone outside value (${leg.dir === 'up' ? 'below VAL' : 'above VAH'})` };
  if (leg.touchIndex == null) {
    const away = Math.abs(b.close - leg.zoneNear);
    return { state: 'wait', text: `${side} zone ${Math.min(leg.zoneNear, leg.zoneFar).toFixed(2)}–${Math.max(leg.zoneNear, leg.zoneFar).toFixed(2)}, ${away.toFixed(2)} away` };
  }
  return { state: value.state, text: `price reached the ${side} zone; ${value.note}` };
}

function pathCheck(leg: Leg | null, price: number, day: AvrDay | null): Check {
  if (!leg) return { state: 'wait', text: 'no target yet' };
  if (!day) return { state: 'warn', text: 'no Asia-range levels for today (needs daily candles)' };
  const up = leg.dir === 'up';
  const max = day.levels.find((l) => l.key === 'max' && l.side === (up ? 'up' : 'down'));
  if (max && (up ? price > max.hi : price < max.lo)) return { state: 'fail', text: 'already past the Max level: an outlier day' };
  const lo = Math.min(price, leg.target);
  const hi = Math.max(price, leg.target);
  const block = day.levels.find(
    (l) => (l.key === 'avrPlus' || l.key === 'max') && l.side === (up ? 'up' : 'down') && l.hi > lo && l.lo < hi,
  );
  if (block) return { state: 'warn', text: `${block.label} sits before the target: the move may tire there` };
  return { state: 'pass', text: `clear run to the target ${leg.target.toFixed(2)}` };
}

function decide(checks: Record<StepKey, Check>, cfg: ArStrategyCfg): { fail: { step: StepKey; text: string } | null; warnings: string[] } {
  const warnings: string[] = [];
  for (const { key } of STEPS) {
    const c = checks[key];
    if (c.state === 'fail' || c.state === 'wait') return { fail: { step: key, text: c.text }, warnings };
    if (c.state === 'warn') {
      if (!cfg.allowWarnings) return { fail: { step: key, text: c.text }, warnings };
      warnings.push(`${key}: ${c.text}`);
    }
  }
  return { fail: null, warnings };
}

/** Totals for the panel: trades closed, wins, points and R (before costs). */
export function arSummary(trades: ArTrade[]) {
  const closed = trades.filter((t) => t.outcome !== 'open');
  const wins = closed.filter((t) => (t.points ?? 0) > 0).length;
  const points = closed.reduce((s, t) => s + (t.points ?? 0), 0);
  const rs = closed.map((t) => t.r ?? 0).sort((a, b) => a - b);
  const median = rs.length ? (rs.length % 2 ? rs[(rs.length - 1) / 2]! : (rs[rs.length / 2 - 1]! + rs[rs.length / 2]!) / 2) : null;
  return {
    trades: closed.length,
    open: trades.length - closed.length,
    wins,
    points,
    avgR: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
    medianR: median,
  };
}
