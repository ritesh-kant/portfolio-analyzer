import {
  type ArMarket,
  type AvrDay,
  type Bias,
  type EnvironmentRead,
  DECODER_LEVELS,
  FIB_LEVELS,
  type Pivot,
  arMarket,
  environment,
  sessionAnchoredFrom,
  structureTrend,
  swings,
} from './ar.ts';
import { type FlowSide, absorption, deltaFlip, lvnPrice } from './ar-orderflow.ts';
import { type GammaRegime, type GammaSnap, firstLevelBefore, gammaRegime, snapAt } from './gamma-history.ts';
import { atr } from './indicators.ts';
import { type StackZone, zoneAt } from './stacked-imbalance.ts';
import { aggregateBars, wallClock } from './timeframes.ts';
import type { Bar, FootprintCandle } from './types';
import { buildProfile } from './volume-profile.ts';

/**
 * The Auction Resolution strategy (phase 2 of research/specs/ar-model-chart-strategy-plan.md): his four steps
 * applied candle by candle, then an entry, stop, target and exit.
 *
 * Walks the candles in order and never reads a candle after the one it is deciding on, so the chart, a replay and
 * the backtest all see the same trades. Where the course is discretionary the rule here is OURS and says so:
 *
 *   1 Environment  trend = break of structure on 1-hour candles (`structureTrend`; the chart's own when it is 1h+); expansion / rotation /
 *                  compression from `environment()`. Compression fails; rotation or a flat trend only warns.
 *   2 Location     the latest swing leg in the trend's direction (on NSE it starts inside today's session:
 *                  `sessionLegs`); price must reach its 0.705–0.886 zone. A close
 *                  past 0.886 cancels it, price beyond the leg's end before any retracement retires it. A zone
 *                  inside the leg's value area warns (fails when `requireOutsideValue`). `zone: 'decoder'` uses the
 *                  "dark pool decoder" stand-in instead (0.62–0.81, fitted to his screen; `DECODER_LEVELS`).
 *   3 Path         from the day's Asia-range levels: an AVR+ / Max band between entry and target warns (the move
 *                  tends to tire there), price already past Max in the trade's direction fails.
 *   4 Confirmation ESTIMATED from candles (no bid/ask data): after the touch, a candle that closes in the trade's
 *                  direction AND beyond the extreme candle's other end (aggression failed, pressure flipped), inside
 *                  the window (New York 09:30–16:00; NSE 09:15–15:15: the whole session up to the flat time, see `arMarket`). Entry: a pull-back to the middle of that candle within a few candles (his
 *                  "entry candle pulls back into the confirmation candle"), or its close.
 *                  `confirm: 'orderflow'` (phase 4) uses the footprint instead: absorption on the candle holding the
 *                  extreme, then a delta flip (lib/tv/ar-orderflow.ts), and `entry: 'lvn'` rests the order at the
 *                  confirmation candle's low-volume node. Each trade says whether its footprint was recorded ticks
 *                  or estimated from 1-minute bars (`flow.source`).
 *
 * Optional (off by default, his "stacked imbalances at that spot make it better"): the fill must sit in a stacked-
 * imbalance zone on the trade's side (lib/tv/stacked-imbalance.ts) that stood when the order was placed. Every trade
 * records whether it did (`stack`), so the two groups can be compared.
 *
 * Gamma (phase 5, lib/tv/gamma-history.ts): given recorded snapshots, every trade and every candle's read carries the
 * regime as known at that candle's close (price vs the gamma flip of the newest snapshot read before it). With
 * `gamma: 'targets'`, his "positive gamma: take profit quickly" becomes OUR rule: in positive gamma the target moves to
 * the first gamma level (flip, call/put wall, gamma wall) between entry and the leg's end; negative gamma keeps the
 * leg's end ("hold winners toward the targets"). The reward ÷ risk check uses the target the trade will have.
 *
 * Stop: beyond the extreme since the touch, plus a small buffer. Target: the leg's end. One trade per leg, one at a
 * time, flat by the end of the session. Results are in points, before costs.
 */

export interface ArStrategyCfg {
  /** Swing size, × ATR 14 of the chart's candles. */
  swingMult: number;
  /** Rows of the leg's volume profile (value area). */
  valueRows: number;
  /** Candles the environment read looks at. */
  envWindow: number;
  /** Confirmation candles must START inside this window, minutes after midnight on the market's clock (`arMarket`). */
  windowStart: number;
  windowEnd: number;
  /** Close any open trade at the close of the candle that ends at or after this minute (market's clock). */
  flatAt: number;
  /** Skip setups whose reward ÷ risk to the target is below this. */
  minRR: number;
  /** How to enter after confirmation: mid-candle pull-back, the close, or the confirmation candle's LVN. */
  entry: 'pullback' | 'close' | 'lvn';
  /** Pull-back / LVN entry: candles the order waits before it is cancelled. */
  entryWait: number;
  /** Candles after the first touch that a confirmation may still come in. */
  confirmWithin: number;
  /** Stop buffer beyond the extreme, × ATR 14. */
  stopBuffer: number;
  /** A zone inside the value area fails instead of warning. */
  requireOutsideValue: boolean;
  /** Take a setup even when a step only warns (never when one fails). */
  allowWarnings: boolean;
  /** Only fill inside a same-side stacked-imbalance zone (needs footprint data on the confirmation candle). */
  requireStack: boolean;
  /** Confirmation from candles (estimated) or from the footprint: absorption, then a delta flip. */
  confirm: 'candles' | 'orderflow';
  /** Order flow: the absorption candle trades at least this × the usual volume. */
  absorbVol: number;
  /** Order flow: the flip candle's delta is at least this share of its volume on the trade's side. */
  flipShare: number;
  /**
   * Which zone is the location: his 0.705–0.886, or the "dark pool decoder" stand-in (0.62–0.81 of the leg, fitted
   * to his screen, see `DECODER_LEVELS`). Either way a close past the far edge cancels it.
   */
  zone: 'fib' | 'decoder';
  /**
   * Gamma regime (needs recorded snapshots, `opts.gamma`): 'off' only labels trades with the regime; 'targets' takes
   * profit at the first gamma level before the leg's end when gamma is positive.
   */
  gamma: 'off' | 'targets';
  /**
   * Legs may not cross an overnight gap: a leg starts at the extreme of its end swing's own session
   * (`sessionAnchoredFrom`). OUR adaptation, on by default where the market closes overnight (NSE).
   */
  sessionLegs: boolean;
}

/** His defaults (US: New York clock). For a chart, use `arStrategyDefaults(timeZone)`. */
export const AR_STRATEGY_DEFAULTS: ArStrategyCfg = {
  swingMult: 2,
  valueRows: 30,
  envWindow: 24,
  windowStart: 9 * 60 + 30,
  windowEnd: 16 * 60,
  flatAt: 16 * 60,
  minRR: 1.5,
  entry: 'pullback',
  entryWait: 3,
  confirmWithin: 6,
  stopBuffer: 0.1,
  requireOutsideValue: false,
  allowWarnings: true,
  requireStack: false,
  confirm: 'candles',
  absorbVol: 1.5,
  flipShare: 0.1,
  zone: 'fib',
  gamma: 'off',
  sessionLegs: false,
};

/** The defaults for a chart's market: the trade window and flat time follow `arMarket`. */
export function arStrategyDefaults(timeZone: string): ArStrategyCfg {
  const m = arMarket(timeZone);
  return { ...AR_STRATEGY_DEFAULTS, windowStart: m.window.start, windowEnd: m.window.end, flatAt: m.flatAt, sessionLegs: !m.hasAsia };
}

export type CheckState = 'pass' | 'warn' | 'fail' | 'wait';
export interface Check {
  state: CheckState;
  /** The whole read in one line (logs, tooltips, tests). */
  text: string;
  /** Optional split of `text` for the panel: one marked line per part, then an unmarked detail line. */
  lines?: { state: CheckState; text: string }[];
  detail?: string;
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
  /** Gamma regime at this candle's close; null without a snapshot that covers it (absent when the run had none). */
  gamma?: GammaRead | null;
}

export interface GammaRead {
  regime: GammaRegime;
  /** 'flip': price vs the gamma flip; 'net': no flip in range, the sign of the net GEX. */
  basis: 'flip' | 'net';
  /** The flip in chart prices, when there is one. */
  flip: number | null;
  /** When the snapshot behind it was read (unix seconds). */
  asOf: number;
}

export interface TradeGamma extends GammaRead {
  /** The leg's end: the target without the gamma rule. */
  legTarget: number;
  /** The gamma level the target moved to (`gamma: 'targets'`, positive gamma), else null. */
  level: string | null;
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
  /**
   * The fill was inside a same-side stacked-imbalance zone ('zone'), was not ('none'), or the confirmation candle had
   * no footprint to tell ('no data'). Null when the run got no stacked-imbalance data at all.
   */
  stack: StackFlag | null;
  /** Where the order rested: mid-candle, the close, or the confirmation candle's LVN ('mid' when it had none). */
  entryRule: 'mid' | 'close' | 'lvn';
  /** Order-flow confirmation (`confirm: 'orderflow'`); null for the candle rule. */
  flow: TradeFlow | null;
  /** Gamma regime when it was confirmed; null when no snapshot covers it (or the run had none). */
  gamma: TradeGamma | null;
}

export interface TradeFlow {
  /** Recorded ticks on both the absorption and the flip candle, else estimated from 1-minute bars. */
  source: 'ticks' | 'estimated';
  absorbIndex: number;
  /** The absorption candle's extreme: its low for a long, its high for a short. */
  absorbPrice: number;
  /** Absorption candle volume ÷ usual. */
  volRatio: number;
  /** Flip candle delta as a share of its volume, on the trade's side. */
  flipShare: number;
}

export type StackFlag = 'zone' | 'none' | 'no data';

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
  zoneNear: number; // 0.705 (decoder: 0.62): the edge price reaches first
  zoneFar: number; // 0.886 (decoder: 0.81): the cancel level
  /** Name of the far edge in the checklist: "0.886" or "the decoder's far edge". */
  farName: string;
  target: number;
  insideValue: boolean | null;
  touchIndex: number | null;
  /** Candle with the extreme since the touch (the "absorption" candle). */
  extremeIndex: number | null;
  /** Order flow: the last check lacked a footprint. */
  flowNoData: boolean;
  dead: 'invalidated' | 'extended' | 'used' | 'expired' | null;
  /** Candle at which it died. */
  deadAt: number | null;
}

const minuteOf = (t: number, tz: string) => Math.floor((((wallClock(t, tz) % 86400) + 86400) % 86400) / 60);
const fmtTime = (t: number, tz: string) => {
  const m = minuteOf(t, tz);
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};
/** "11-25 09:15" on the market's clock. */
const fmtWhen = (t: number, tz: string) => new Date(wallClock(t, tz) * 1000).toISOString().slice(5, 16).replace('T', ' ');

/** What the checklist needs to name a leg in words. */
interface LegCtx {
  bars: Bar[];
  tz: string;
  /** Index of the first candle of candle i's session (a gap over an hour starts one). */
  sessionStart: number;
}

/** "swing 25730.00 (11-25 09:15) → 26114.10 (11-25 11:45)" */
const legName = (leg: Leg, c: LegCtx) =>
  `swing ${leg.from.price.toFixed(2)} (${fmtWhen(c.bars[leg.from.index]!.time, c.tz)}) → ${leg.to.price.toFixed(2)} (${fmtWhen(c.bars[leg.to.index]!.time, c.tz)})`;

function makeLeg(bars: Bar[], from: Pivot, to: Pivot, rows: number, zone: ArStrategyCfg['zone']): Leg {
  const span = to.price - from.price;
  const at = (lvl: number) => to.price - lvl * span;
  const decoder = zone === 'decoder';
  const zoneNear = at(decoder ? DECODER_LEVELS.near : FIB_LEVELS[2]);
  const zoneFar = at(decoder ? DECODER_LEVELS.far : FIB_LEVELS[4]);
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
    farName: decoder ? "the decoder's far edge" : '0.886',
    target: to.price,
    insideValue,
    touchIndex: null,
    extremeIndex: null,
    flowNoData: false,
    dead: null,
    deadAt: null,
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
  const trend = structureTrend(tfBars, swings(tfBars, mult).pivots);
  // each 1h value is known at that 1h candle's close
  let k = -1;
  for (let i = 0; i < bars.length; i++) {
    const close = bars[i]!.time + chartSecs;
    while (k + 1 < tfBars.length && tfBars[k + 1]!.time + tfSecs <= close) k++;
    out[i] = k >= 0 ? trend[k]! : 'neutral';
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
    /** Stacked-imbalance zones and which candles had a footprint to find them in (index-aligned with `bars`). */
    stacks?: { zones: StackZone[]; covered: boolean[] };
    /** Footprint per candle (index-aligned with `bars`, null where none): needed by `confirm: 'orderflow'` / `entry: 'lvn'`. */
    footprints?: (FootprintCandle | null)[];
    /** Recorded gamma snapshots for this symbol, oldest first (`parseSnapshots`); levels are scaled to the chart. */
    gamma?: GammaSnap[];
    cfg?: Partial<ArStrategyCfg>;
  },
): ArStrategyResult {
  const cfg = { ...arStrategyDefaults(opts.timeZone), ...opts.cfg };
  const { timeZone, chartSecs } = opts;
  const market = arMarket(timeZone);
  // the window, flat time and every time shown are on the market's clock, not the chart's
  const tz = market.tz;
  const avr = opts.avr ?? [];
  const reads: ArRead[] = [];
  const trades: ArTrade[] = [];
  const skips: ArSkip[] = [];
  if (bars.length < 3) return { reads, trades, skips };

  const pivots = swings(bars, cfg.swingMult).pivots;
  const bias = biasAsOf(bars, chartSecs, timeZone, cfg.swingMult);
  const a14 = atr(bars, 14);
  const legs = new Map<string, Leg>();
  let known = 0; // pivots confirmed so far
  let active: Leg | null = null;
  let open: ArTrade | null = null;
  let pending: {
    leg: Leg;
    dir: 'long' | 'short';
    limit: number;
    stop: number;
    target: number;
    confirmIndex: number;
    until: number;
    warnings: string[];
    rule: ArTrade['entryRule'];
    flow: TradeFlow | null;
    gamma: TradeGamma | null;
  } | null = null;
  const fps = opts.footprints;
  const snaps = opts.gamma;
  /** The newest snapshot read by candle i's close, and the regime it gives at that close. */
  const snapFor = (i: number) => (snaps ? snapAt(snaps, bars[i]!.time + chartSecs) : null);
  const readOf = (snap: GammaSnap | null, price: number): GammaRead | null =>
    snap ? { ...gammaRegime(snap, price), asOf: snap.asOf } : null;
  let nextId = 1;

  const lastDecidable = opts.formingLast ? bars.length - 2 : bars.length - 1;

  /** Was a same-side zone standing at the close of `asOf` under (long) / over (short) `price`? */
  const stackAt = (dir: 'long' | 'short', price: number, confirmIndex: number, asOf: number): StackFlag | null => {
    if (!opts.stacks) return null;
    if (!opts.stacks.covered[confirmIndex]) return 'no data';
    // about one footprint row of slack: 0.1 × ATR 14
    const tol = (a14[asOf] ?? 0) * 0.1;
    return zoneAt(opts.stacks.zones, dir === 'long' ? 'buy' : 'sell', price, asOf, tol) ? 'zone' : 'none';
  };
  const stackSkip = (flag: StackFlag | null) =>
    !cfg.requireStack
      ? null
      : flag === 'zone'
        ? null
        : flag === 'none'
          ? 'no stacked-imbalance zone at the fill'
          : 'no footprint data to check for stacked imbalances';

  let sessionStart = 0;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]!;
    if (i > 0 && b.time - bars[i - 1]!.time > 3600) sessionStart = i;
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
        pending.leg.deadAt = i;
        pending = null;
      } else if (long ? b.low <= pending.limit : b.high >= pending.limit) {
        const fill = long ? Math.min(pending.limit, b.open) : Math.max(pending.limit, b.open);
        // zones known when the order was resting: at the previous candle's close
        const flag = stackAt(pending.dir, fill, pending.confirmIndex, i - 1);
        const why = stackSkip(flag);
        if (why) {
          skips.push({ index: i, price: fill, dir: pending.dir, step: 'entry', reason: why });
          pending = null;
        } else {
          open = openTrade(pending.leg, pending.dir, fill, pending.stop, pending.target, pending.confirmIndex, i, pending.warnings, flag, pending.rule, pending.flow, pending.gamma);
          pending = null;
          // the fill candle can still stop out (conservatively, never reach the target on the same candle)
          const hitStop = long ? b.low <= open.stop : b.high >= open.stop;
          if (hitStop) {
            closeTrade(open, i, open.stop, 'stop');
            open = null;
          }
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
          // NSE: the leg starts inside today's session, not across the overnight gap
          const from = cfg.sessionLegs
            ? sessionAnchoredFrom(bars, pivots[k - 1]!, to, (a14[to.index] ?? 0) * cfg.swingMult)
            : pivots[k - 1]!;
          leg = makeLeg(bars, from, to, cfg.valueRows, cfg.zone);
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
      location: locationCheck(leg, b, cfg, { bars, tz, sessionStart }),
      path: pathCheck(leg, b.close, avrAt(avr, i), market),
      confirmation: !leg
        ? { state: 'wait', text: 'no zone yet' }
        : leg.dead
          ? { state: 'wait', text: 'no live zone (see Location)' }
          : { state: 'wait', text: 'waiting for price to reach the zone' },
    };
    if (open) {
      checks.confirmation = { state: 'pass', text: `in a ${open.dir} from ${open.entry.toFixed(2)}` };
    } else if (pending) {
      checks.confirmation = { state: 'pass', text: `confirmed; order waiting at ${pending.limit.toFixed(2)}` };
    } else if (leg && !leg.dead && leg.touchIndex != null && leg.extremeIndex != null) {
      const long = leg.dir === 'up';
      const side: FlowSide = long ? 'long' : 'short';
      const ext = bars[leg.extremeIndex]!;
      let flips: boolean;
      let waitText: string;
      let flow: TradeFlow | null = null;
      if (cfg.confirm === 'orderflow') {
        const r = flowRead(i, leg, side);
        flips = r.flow != null;
        waitText = r.text;
        flow = r.flow;
      } else {
        flips = i > leg.extremeIndex && (long ? b.close > b.open && b.close > ext.high : b.close < b.open && b.close < ext.low);
        waitText = long
          ? `in the zone; waiting for a green candle closing above ${ext.high.toFixed(2)}`
          : `in the zone; waiting for a red candle closing below ${ext.low.toFixed(2)}`;
      }
      const inWindow = minute >= cfg.windowStart && minute < cfg.windowEnd;
      if (i - leg.touchIndex > cfg.confirmWithin) {
        leg.dead = 'expired';
        leg.deadAt = i;
        const reason =
          cfg.confirm !== 'orderflow'
            ? `no confirmation within ${cfg.confirmWithin} candles of the touch`
            : leg.flowNoData
              ? 'no footprint data for the order-flow check'
              : `no absorption + delta flip within ${cfg.confirmWithin} candles of the touch`;
        skips.push({ index: i, price: bars[i]!.close, dir: long ? 'long' : 'short', step: 'confirmation', reason });
        checks.confirmation = { state: 'fail', text: 'no confirmation came in time' };
      } else if (!flips || i > lastDecidable) {
        checks.confirmation = { state: 'wait', text: waitText };
      } else if (!inWindow) {
        checks.confirmation = { state: 'fail', text: `confirmed at ${fmtTime(b.time, tz)}, outside the ${windowText(cfg)} ${market.clock} window` };
        skips.push({ index: i, price: bars[i]!.close, dir: long ? 'long' : 'short', step: 'confirmation', reason: `confirmation outside ${windowText(cfg)} ${market.clock} time` });
        leg.dead = 'used';
        leg.deadAt = i;
      } else {
        checks.confirmation = {
          state: 'pass',
          text: flow ? waitText : `pressure flipped at ${fmtTime(b.time, tz)} (estimated from candles)`,
        };
        const decision = decide(checks, cfg);
        const dir = long ? 'long' : 'short';
        const buffer = (a14[i] ?? Math.abs(ext.high - ext.low)) * cfg.stopBuffer;
        const stop = long ? ext.low - buffer : ext.high + buffer;
        const lvn = cfg.entry === 'lvn' ? lvnPrice(b, fps?.[i], side) : null;
        const rule: ArTrade['entryRule'] = cfg.entry === 'close' ? 'close' : lvn != null ? 'lvn' : 'mid';
        const entryPx = rule === 'close' ? b.close : (lvn ?? (b.high + b.low) / 2);
        // gamma: the regime at this close; in positive gamma, 'targets' takes profit at the first level on the way
        const snap = snapFor(i);
        const g = readOf(snap, b.close);
        let target = leg.target;
        let gamma: TradeGamma | null = null;
        if (snap && g) {
          const lvl = cfg.gamma === 'targets' && g.regime === 'positive' ? firstLevelBefore(snap, dir, entryPx, leg.target) : null;
          if (lvl) target = lvl.price;
          gamma = { ...g, legTarget: leg.target, level: lvl?.name ?? null };
        }
        const rr = Math.abs(target - entryPx) / Math.max(1e-9, Math.abs(entryPx - stop));
        leg.dead = 'used';
        leg.deadAt = i;
        if (decision.fail) {
          skips.push({ index: i, price: bars[i]!.close, dir, step: decision.fail.step, reason: decision.fail.text });
        } else if (rr < cfg.minRR) {
          const why = gamma?.level ? ` (target moved to ${gamma.level} ${target.toFixed(2)}: positive gamma)` : '';
          skips.push({ index: i, price: bars[i]!.close, dir, step: 'entry', reason: `reward ÷ risk ${rr.toFixed(2)} below ${cfg.minRR}${why}` });
        } else if (cfg.entry === 'close') {
          // market order at this (complete) close: managed from the next candle
          const flag = stackAt(dir, b.close, i, i);
          const why = stackSkip(flag);
          if (why) skips.push({ index: i, price: b.close, dir, step: 'entry', reason: why });
          else open = openTrade(leg, dir, b.close, stop, target, i, i, decision.warnings, flag, rule, flow, gamma);
        } else {
          pending = { leg, dir, limit: entryPx, stop, target, confirmIndex: i, until: i + cfg.entryWait, warnings: decision.warnings, rule, flow, gamma };
        }
      }
    }

    reads.push(snaps ? { index: i, checks, tradeId: open?.id ?? null, gamma: readOf(snapFor(i), b.close) } : { index: i, checks, tradeId: open?.id ?? null });
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

  function openTrade(
    leg: Leg,
    dir: 'long' | 'short',
    entry: number,
    stop: number,
    target: number,
    confirmIndex: number,
    entryIndex: number,
    warnings: string[],
    stack: StackFlag | null,
    entryRule: ArTrade['entryRule'],
    flow: TradeFlow | null,
    gamma: TradeGamma | null,
  ): ArTrade {
    const t: ArTrade = {
      id: nextId++,
      dir,
      touchIndex: leg.touchIndex ?? confirmIndex,
      confirmIndex,
      entryIndex,
      entry,
      stop,
      target,
      exitIndex: null,
      exit: null,
      outcome: 'open',
      points: null,
      r: null,
      warnings,
      stack,
      entryRule,
      flow,
      gamma,
    };
    trades.push(t);
    return t;
  }

  /**
   * Order-flow confirmation at candle i (lib/tv/ar-orderflow.ts): is the extreme candle since the touch absorption,
   * and does candle i flip the delta? `flow` is set only when both hold; `text` is the checklist line either way.
   */
  function flowRead(i: number, leg: Leg, side: FlowSide): { flow: TradeFlow | null; text: string } {
    const e = leg.extremeIndex!;
    const end = side === 'long' ? 'low' : 'high';
    const at = fmtTime(bars[e]!.time, tz);
    const abs = absorption(bars, fps?.[e], e, side, cfg.absorbVol);
    if (!abs) {
      leg.flowNoData = true;
      return { flow: null, text: `in the zone; no footprint for the ${end} candle (${at}) to check for absorption` };
    }
    leg.flowNoData = false;
    if (!abs.ok) return { flow: null, text: `in the zone; the ${end} candle (${at}) is not absorption: ${abs.why.join(', ')}` };
    const absText = `absorption at ${at} (${abs.volRatio.toFixed(1)}× volume, POC at the ${end})`;
    if (i <= e) return { flow: null, text: `${absText}; waiting for the delta to flip` };
    const flip = deltaFlip(bars, fps?.[i], i, e, side, cfg.flipShare);
    if (!flip) {
      leg.flowNoData = true;
      return { flow: null, text: `${absText}; no footprint for this candle` };
    }
    if (!flip.ok) return { flow: null, text: `${absText}; waiting for the flip: ${flip.why}` };
    const ticks = fps?.[e]?.source === 'ticks' && fps[i]?.source === 'ticks';
    const d = fps![i]!.delta;
    return {
      flow: {
        source: ticks ? 'ticks' : 'estimated',
        absorbIndex: e,
        absorbPrice: side === 'long' ? bars[e]!.low : bars[e]!.high,
        volRatio: abs.volRatio,
        flipShare: flip.share,
      },
      text: `${absText}, delta flipped ${d >= 0 ? '+' : '−'}${Math.abs(Math.round(d))} at ${fmtTime(bars[i]!.time, tz)} (${ticks ? 'recorded ticks' : 'estimated footprint'})`,
    };
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
      leg.deadAt = i;
      return;
    }
    if (up ? b.low <= leg.zoneNear : b.high >= leg.zoneNear) {
      leg.touchIndex = i;
      leg.extremeIndex = i;
    }
  } else if (leg.extremeIndex != null && (up ? b.low < bars[leg.extremeIndex]!.low : b.high > bars[leg.extremeIndex]!.high)) {
    leg.extremeIndex = i;
  }
  if (leg.touchIndex != null && (up ? b.close < leg.zoneFar : b.close > leg.zoneFar)) {
    leg.dead = 'invalidated';
    leg.deadAt = i;
  }
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

function locationCheck(leg: Leg | null, b: Bar, cfg: ArStrategyCfg, c: LegCtx): Check {
  if (!leg) return { state: 'wait', text: 'no swing in the trend’s direction yet' };
  const side = leg.dir === 'up' ? 'discount' : 'premium';
  const idea = leg.dir === 'up' ? 'long' : 'short';
  const name = legName(leg, c);
  const when = leg.deadAt != null ? fmtWhen(c.bars[leg.deadAt]!.time, c.tz) : '';
  if (leg.dead === 'invalidated') {
    // cancelled in this session: a red mark; an older cancel is just history, so wait for the next swing
    if (leg.deadAt != null && leg.deadAt >= c.sessionStart)
      return { state: 'fail', text: `closed past ${leg.farName} (${leg.zoneFar.toFixed(2)}) at ${when}: ${idea} zone cancelled · ${name}` };
    return { state: 'wait', text: `the last ${idea} zone was cancelled on ${when} (${name}): waiting for a new swing` };
  }
  if (leg.dead === 'extended')
    return { state: 'wait', text: `price ran past the swing ${leg.dir === 'up' ? 'high' : 'low'} ${leg.to.price.toFixed(2)} at ${when} before pulling back: waiting for a new swing` };
  if (leg.dead === 'used') return { state: 'wait', text: `this swing’s setup was decided at ${when} (${name}): waiting for a new swing` };
  if (leg.dead === 'expired') return { state: 'wait', text: `this swing’s setup ran out of time at ${when} (${name}): waiting for a new swing` };
  const value = leg.insideValue
    ? { state: (cfg.requireOutsideValue ? 'fail' : 'warn') as CheckState, note: 'zone overlaps the value area: a busy price, less ideal' }
    : { state: 'pass' as CheckState, note: `zone is ${leg.dir === 'up' ? 'below' : 'above'} the value area, as he prefers` };
  if (leg.touchIndex == null) {
    const away = Math.abs(b.close - leg.zoneNear);
    return { state: 'wait', text: `${side} ${leg.farName === '0.886' ? 'zone' : 'decoder box'} ${Math.min(leg.zoneNear, leg.zoneFar).toFixed(2)}–${Math.max(leg.zoneNear, leg.zoneFar).toFixed(2)} (${idea}), ${away.toFixed(2)} away · ${name}` };
  }
  return {
    state: value.state,
    text: `price reached the ${side} zone (${idea}); ${value.note} · ${name}`,
    lines: [
      { state: 'pass', text: `price reached the ${side} zone (${idea})` },
      { state: value.state, text: value.note },
    ],
    detail: `Fib drawn on ${name}`,
  };
}

function pathCheck(leg: Leg | null, price: number, day: AvrDay | null, market: ArMarket): Check {
  if (!leg) return { state: 'wait', text: 'no target yet' };
  if (!market.hasAsia) return { state: 'warn', text: 'not checked: Asia-range levels need an overnight session, which NSE does not have' };
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
