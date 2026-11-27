import {
  type ArMarket,
  type AvrDay,
  type Bias,
  type EnvironmentRead,
  DECODER_LEVELS,
  FIB_LEVELS,
  type Pivot,
  type StructureRead,
  arMarket,
  candlesPerDay,
  dayRangeRatio,
  environment,
  sessionAnchoredFrom,
  structureLeg,
  withSessionOpen,
  structureReads,
  structureTrend,
  swingTrend,
  swings,
  weekPivots,
  weekRanges,
} from './ar.ts';
import { type FlowRule, type FlowSide, absorption, deltaFlip, failedAggression, lvnPrice, usualVolume } from './ar-orderflow.ts';
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
 *   1 Environment  `envRule: 'structure'` (default since 2026-11-26, his step 1a/1b): market structure on 4-hour
 *                  candles (`structureReads`): higher highs + higher lows (or a close above the last swing high) =
 *                  trending up, the mirror = trending down, overlapping swings = sideways. Choppy = the last trading
 *                  day's 1-hour range under `compress` × a normal day's (`dayRangeRatio`): no trade. Trending against
 *                  the zone's direction fails; sideways warns (his "fade the extremes", `sideways: 'block'` to refuse
 *                  it). The 1h structure is shown, never decides (during a pull-back it points the other way).
 *                  `envRule: 'efficiency'` (the older rule): trend on 1-hour candles (the chart's own when it is 1h+): by default up / down / unclear from the
 *                  latest swings (`swingTrend`: higher high + higher low = up, lower high + lower low = down, anything
 *                  else unclear; a close through the latest swing drops it to unclear), or break of structure
 *                  (`trendRule: 'break'`, `structureTrend`, never unclear once set). Expansion / rotation / compression
 *                  from `environment()` on the last `envWindow` 1-hour and `envWindow4h` 4-hour candles (`envAsOf`);
 *                  sideways = both read rotation (`sidewaysWhen: 'either'`: either one). An unclear trend, compression
 *                  on either timeframe, or sideways fails (sideways only warns with `sideways: 'warn'`). Longs only in
 *                  an up trend (discount zone), shorts only in a down trend (premium zone).
 *                  With the week range (his rule from the condensed course) the trend is the week
 *                  range's own direction instead (see Location): down when its high came first, up when its low did,
 *                  unclear once a candle closes past its 0.886 until a new high or low resets the range.
 *   2 Location     `range: 'both'` (default since 2026-11-26): the week range below AND the latest swing leg on the
 *                  chart's candles in the 4h trend's direction (none while the 4h is sideways; with the older
 *                  efficiency rule, the 1h trend's), each followed every candle; the zone price reaches first is
 *                  traded (the week range when both), else the checklist shows the one nearer to price and names the
 *                  other. `range: 'week'`: his "most active range", the highest high and lowest low of the last
 *                  `rangeDays` days (`weekRanges`), drawn from the earlier extreme to the later one; while price is in
 *                  its zone the setup re-arms after each confirmation window, up to `legTrades` trades per range.
 *                  `range: 'swing'` (the older rule): the latest swing leg in the trend's direction (on NSE it starts
 *                  inside today's session: `sessionLegs`). Either way price must reach the 0.705–0.886 zone. A close
 *                  past 0.886 cancels it, price beyond the leg's end before any retracement retires it. A zone
 *                  inside the leg's value area warns (fails when `requireOutsideValue`). `zone: 'decoder'` uses the
 *                  "dark pool decoder" stand-in instead (0.62–0.81, fitted to his screen; `DECODER_LEVELS`).
 *   3 Path         from the day's Asia-range levels: an AVR+ / Max band between entry and target warns (the move
 *                  tends to tire there), price already past Max in the trade's direction fails.
 *   4 Confirmation `confirm: 'orderflow'` (default since 2026-11-26, his step 4) with `flowRule: 'video'` (his words
 *                  only, see lib/tv/ar-orderflow.ts): needs a footprint, recorded ticks where there are any, else
 *                  estimated from 1-minute bars (about the last 4 weeks), so older setups skip as "no footprint data".
 *                  `confirm: 'candles'`: ESTIMATED from candles (no bid/ask data): after the touch, a candle that closes in the trade's
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
 * Stop: beyond the extreme since the touch, plus a small buffer. Target: the leg's end. One trade at a time, flat by
 * the end of the session. Results are in points, before costs.
 *
 * Trade management, his live trades (4 h course 3:33–3:48: "we're 25 points in profit, stop's going to break even",
 * "stop is going to actually trail behind this 5-minute candle", "right above … the POC of the last 15-minute
 * candle"). OUR codification:
 *   - breakeven: once a COMPLETED candle has gone `breakeven` points (or R, `breakevenUnit`) past the entry, the stop
 *     moves to the entry from the NEXT candle on;
 *   - trail ('candle', default): after breakeven (from the first candle after the fill when breakeven is off), the stop
 *     follows each completed candle, just past its low (long) / high (short) by `stopBuffer` × ATR; 'poc15' uses the
 *     POC of the last completed 15-minute block from the footprint instead (the candle when there is none);
 *   - a stop only ever tightens, never moves past the candle's close (no stop order could rest there), and a candle
 *     never moves the stop it is itself tested against: no same-candle look-ahead. The fill candle moves nothing (its
 *     high/low may come before the fill).
 * His other trailing references (large resting orders on the DOM, tighter where buyers already defended once,
 * tighter in positive gamma) need data we don't have. R stays measured against the initial stop.
 */

export interface ArStrategyCfg {
  /** Swing size, × ATR 14 of the chart's candles. */
  swingMult: number;
  /** Rows of the leg's volume profile (value area). */
  valueRows: number;
  /**
   * Expansion / rotation / compression is read on 1-hour AND 4-hour candles (built from the chart's; the chart's own
   * when it is that coarse or coarser), completed candles only: this many 1h candles …
   */
  envWindow: number;
  /** … and this many 4h candles. */
  envWindow4h: number;
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
  /**
   * Candle confirmation: a candle in the trade's direction closing past the extreme candle's other end ('beyond'),
   * or past its middle ('midpoint', looser).
   */
  candleRule: 'beyond' | 'midpoint';
  /** Stop buffer beyond the extreme, × ATR 14. */
  stopBuffer: number;
  /** A zone inside the value area fails instead of warning. */
  requireOutsideValue: boolean;
  /** Take a setup even when a step only warns (never when one fails). */
  allowWarnings: boolean;
  /** Only fill inside a same-side stacked-imbalance zone (needs footprint data on the confirmation candle). */
  requireStack: boolean;
  /** Confirmation from the footprint (his: absorption, then a delta flip; default) or from candles (our stand-in). */
  confirm: 'candles' | 'orderflow';
  /** Order flow: the absorption candle trades at least this × the usual volume. */
  absorbVol: number;
  /** Order flow: the flip candle's delta is at least this share of its volume on the trade's side. */
  flipShare: number;
  /**
   * Order flow: his decision tree ('tree', default: aggression at the level failed, then the delta flipped), one
   * absorption candle by his signs ('video': POC in the wick, close against the push), or our thresholds ('ours').
   */
  flowRule: FlowRule;
  /**
   * Order flow: read absorption on the candle holding the extreme since the touch ('extreme', ours), or on any
   * candle at the level since the touch that price has not gone past since ('zone', his "at the level").
   */
  absorbAt: 'extreme' | 'zone';
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
  /**
   * How the 1h trend is read: 'swings' = up / down / unclear from the latest swing high and low (`swingTrend`);
   * 'break' = the older break-of-structure rule (`structureTrend`), which flips straight between up and down.
   */
  trendRule: 'swings' | 'break';
  /**
   * How the environment is read: 'structure' = 4h swings (trending / sideways) plus the last day's range (choppy),
   * his step 1a/1b; 'efficiency' = the older net-move ÷ path-length read on `envWindow` 1h and `envWindow4h` 4h candles.
   */
  envRule: 'structure' | 'efficiency';
  /** Structure rule: choppy (no trade) when the last day's range is below this × a normal day's. */
  compress: number;
  /** A sideways (rotation) environment: no trade ('block'), or only a warning ('warn': fade the range's edge). */
  sideways: 'block' | 'warn';
  /** Sideways means 1h AND 4h both read rotation ('both'), or either of them does ('either', stricter). */
  sidewaysWhen: 'both' | 'either';
  /**
   * Which leg the Fib is drawn on: 'week' = his "most active range", the highest high and lowest low of the last
   * `rangeDays` days, which also sets the trend (condensed course, 17:59); 'swing' = the latest swing leg of
   * `swingMult` × ATR in the trend's direction (4h structure, or the 1h trend with `envRule: 'efficiency'`;
   * `sessionLegs` applies only to it); 'both' (default) = watch both, trade the zone price reaches first.
   */
  range: 'week' | 'swing' | 'both';
  /** Week range: how many calendar days back it reaches (his "past week" on the hourly). */
  rangeDays: number;
  /**
   * Which swing leg is the intraday leg: 'structure' = the leg today's swings are building (internal pull-backs
   * inside it do not replace it; `structureLeg`), 'latest' = the most recent swing leg (the older rule).
   */
  legRule: 'structure' | 'latest';
  /** Week range: at most this many trades per range (he re-takes the same idea after a stop-out). */
  legTrades: number;
  /** Move the stop to the entry once a completed candle has gone this far our way (0 = never). */
  breakeven: number;
  /** … in price points (his "25 points" on NQ) or in R (multiples of the initial risk). */
  breakevenUnit: 'points' | 'R';
  /** Trail the stop after breakeven: past each completed candle, past the last 15-minute POC, or not at all. */
  trail: 'candle' | 'poc15' | 'off';
}

/** His defaults (US: New York clock). For a chart, use `arStrategyDefaults(timeZone)`. */
export const AR_STRATEGY_DEFAULTS: ArStrategyCfg = {
  swingMult: 2,
  valueRows: 30,
  envWindow: 12,
  envWindow4h: 6,
  windowStart: 9 * 60 + 30,
  windowEnd: 16 * 60,
  flatAt: 16 * 60,
  minRR: 1.5,
  entry: 'pullback',
  entryWait: 3,
  confirmWithin: 6,
  candleRule: 'beyond',
  stopBuffer: 0.1,
  requireOutsideValue: false,
  allowWarnings: true,
  requireStack: false,
  confirm: 'orderflow',
  absorbVol: 1.5,
  flipShare: 0.1,
  flowRule: 'tree',
  absorbAt: 'extreme',
  zone: 'fib',
  gamma: 'off',
  sessionLegs: false,
  trendRule: 'swings',
  envRule: 'structure',
  compress: 0.6,
  sideways: 'warn',
  sidewaysWhen: 'both',
  range: 'both',
  rangeDays: 7,
  legRule: 'structure',
  legTrades: 3,
  breakeven: 25,
  breakevenUnit: 'points',
  trail: 'candle',
};

/** The defaults for a chart's market: the trade window and flat time follow `arMarket`. */
export function arStrategyDefaults(timeZone: string): ArStrategyCfg {
  const m = arMarket(timeZone);
  return {
    ...AR_STRATEGY_DEFAULTS,
    windowStart: m.window.start,
    windowEnd: m.window.end,
    flatAt: m.flatAt,
    sessionLegs: !m.hasAsia,
    // NSE: "25 points" means very different things on a ₹300 stock and on NIFTY, so breakeven is in R there
    ...(m.hasAsia ? {} : { breakeven: 0.6, breakevenUnit: 'R' as const }),
  };
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
  /** 'breakeven' / 'trail': stopped out after the stop was moved (`stopMoves`); 'stop': at the initial stop. */
  outcome: 'target' | 'stop' | 'breakeven' | 'trail' | 'time' | 'open';
  /** The stop in force now (moves with breakeven and the trail); `stop` stays the initial one, which R is measured against. */
  stopNow: number;
  /** Every move of the stop: the candle whose close decided it (it applies from the next candle), the new price, why. */
  stopMoves: StopMove[];
  /** Candle at whose close the stop went to breakeven, or null. */
  breakevenAt: number | null;
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

export interface StopMove {
  index: number;
  price: number;
  why: 'breakeven' | 'trail';
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
  /** What the checklist calls it: a swing leg or the week range. */
  noun: 'swing' | 'week range';
  /** Trades taken on it (the week range allows `legTrades`). */
  trades: number;
  /** Last candle its state was advanced to (`bring`), so a leg picked again later catches up first. */
  seen: number;
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
/** "11-25 09:15" on the clock `tz`. */
const fmtWhen = (t: number, tz: string) => new Date(wallClock(t, tz) * 1000).toISOString().slice(5, 16).replace('T', ' ');

/** What the checklist needs to name a leg in words. */
interface LegCtx {
  bars: Bar[];
  /** The clock times are written in (the chart's). */
  tz: string;
  /** Index of the first candle of candle i's session (a gap over an hour starts one). */
  sessionStart: number;
}

/** "swing 25730.00 (11-25 09:15) → 26114.10 (11-25 11:45)" */
const legName = (leg: Leg, c: LegCtx) =>
  `${leg.noun} ${leg.from.price.toFixed(2)} (${fmtWhen(c.bars[leg.from.index]!.time, c.tz)}) → ${leg.to.price.toFixed(2)} (${fmtWhen(c.bars[leg.to.index]!.time, c.tz)})`;

function makeLeg(bars: Bar[], from: Pivot, to: Pivot, rows: number, zone: ArStrategyCfg['zone'], noun: Leg['noun'] = 'swing'): Leg {
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
    noun,
    trades: 0,
    seen: to.index,
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
function biasAsOf(bars: Bar[], chartSecs: number, timeZone: string, mult: number, rule: ArStrategyCfg['trendRule']): Bias[] {
  const out: Bias[] = new Array(bars.length).fill('neutral');
  if (!bars.length) return out;
  let tfBars = bars;
  let tfSecs = chartSecs;
  if (chartSecs < 3600) {
    tfBars = aggregateBars(bars, 3600, timeZone);
    tfSecs = 3600;
  }
  const piv = swings(tfBars, mult).pivots;
  const trend = rule === 'break' ? structureTrend(tfBars, piv) : swingTrend(tfBars, piv);
  // each 1h value is known at that 1h candle's close
  let k = -1;
  for (let i = 0; i < bars.length; i++) {
    const close = bars[i]!.time + chartSecs;
    while (k + 1 < tfBars.length && tfBars[k + 1]!.time + tfSecs <= close) k++;
    out[i] = k >= 0 ? trend[k]! : 'neutral';
  }
  return out;
}

/**
 * Expansion / rotation / compression as known at each chart candle's close, read on `tfSecs` candles built from the
 * chart's (the chart's own when it is that coarse or coarser) over the last `window` completed ones, so it judges the
 * market rather than the pull-back itself (a pull-back and turn always reads as rotation on a couple of hours of
 * 5-minute candles).
 */
function envAsOf(bars: Bar[], chartSecs: number, timeZone: string, tfSecs: number, window: number): (EnvironmentRead | null)[] {
  if (chartSecs >= tfSecs) return bars.map((_, i) => environment(bars, window, i + 1));
  const tf = aggregateBars(bars, tfSecs, timeZone);
  const byCount: (EnvironmentRead | null)[] = [];
  for (let k = 0; k <= tf.length; k++) byCount.push(environment(tf, window, k));
  const out: (EnvironmentRead | null)[] = new Array(bars.length).fill(null);
  let done = 0; // tfSecs candles completed by this chart candle's close
  for (let i = 0; i < bars.length; i++) {
    const close = bars[i]!.time + chartSecs;
    while (done < tf.length && tf[done]!.time + tfSecs <= close) done++;
    out[i] = byCount[done]!;
  }
  return out;
}

/**
 * A per-candle read made on `tfSecs` candles (built from the chart's; the chart's own when it is that coarse or
 * coarser), mapped to each chart candle as known at its close: the read of the last COMPLETED `tfSecs` candle.
 */
function tfAsOf<T>(bars: Bar[], chartSecs: number, timeZone: string, tfSecs: number, read: (tf: Bar[]) => (T | null)[]): (T | null)[] {
  if (chartSecs >= tfSecs) return read(bars);
  const tf = aggregateBars(bars, tfSecs, timeZone);
  const byTf = read(tf);
  const out: (T | null)[] = new Array(bars.length).fill(null);
  let done = 0; // tfSecs candles completed by this chart candle's close
  for (let i = 0; i < bars.length; i++) {
    const close = bars[i]!.time + chartSecs;
    while (done < tf.length && tf[done]!.time + tfSecs <= close) done++;
    out[i] = done > 0 ? byTf[done - 1]! : null;
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
    /**
     * The clock the chart shows (its display time zone): times written in the checklist use it, so they match the
     * chart's time axis. The rules themselves (trade window, flat time) stay on the market's clock. Default: market's.
     */
    displayTimeZone?: string;
    cfg?: Partial<ArStrategyCfg>;
  },
): ArStrategyResult {
  const cfg = { ...arStrategyDefaults(opts.timeZone), ...opts.cfg };
  const { timeZone, chartSecs } = opts;
  const market = arMarket(timeZone);
  // the window, flat time and every time shown are on the market's clock, not the chart's
  const tz = market.tz;
  // …but times written in the checklist are on the chart's clock
  const shownTz = opts.displayTimeZone ?? tz;
  const avr = opts.avr ?? [];
  const reads: ArRead[] = [];
  const trades: ArTrade[] = [];
  const skips: ArSkip[] = [];
  if (bars.length < 3) return { reads, trades, skips };

  const pivots = swings(bars, cfg.swingMult).pivots;
  const bias = biasAsOf(bars, chartSecs, timeZone, cfg.swingMult, cfg.trendRule);
  // his week range at each candle (null with range: 'swing')
  const weeks = cfg.range !== 'swing' ? weekRanges(bars, cfg.rangeDays) : null;
  const swingLegs = cfg.range !== 'week';
  const structural = cfg.envRule === 'structure';
  const envs1h = structural ? null : envAsOf(bars, chartSecs, timeZone, 3600, cfg.envWindow);
  const envs4h = structural ? null : envAsOf(bars, chartSecs, timeZone, 14400, cfg.envWindow4h);
  const structOn = (tf: Bar[]) => structureReads(tf, swings(tf, cfg.swingMult).pivots);
  const struct4h = structural ? tfAsOf(bars, chartSecs, timeZone, 14400, structOn) : null;
  const struct1h = structural ? tfAsOf(bars, chartSecs, timeZone, 3600, structOn) : null;
  const dayRange = structural
    ? tfAsOf(bars, chartSecs, timeZone, 3600, (tf) => dayRangeRatio(tf, candlesPerDay(tf, timeZone)))
    : null;
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
    /** The zone touch this order came from (the week range re-arms, so the leg's own touch moves on). */
    touch: number;
  } | null = null;
  const fps = opts.footprints;
  const snaps = opts.gamma;
  /** The newest snapshot read by candle i's close, and the regime it gives at that close. */
  const snapFor = (i: number) => (snaps ? snapAt(snaps, bars[i]!.time + chartSecs) : null);
  const readOf = (snap: GammaSnap | null, price: number): GammaRead | null =>
    snap ? { ...gammaRegime(snap, price), asOf: snap.asOf } : null;
  let nextId = 1;
  /** Trades whose breakeven distance has been reached (applied once the entry is a placeable stop). */
  const beArmed = new Set<number>();

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
  let prevSessionStart = 0;
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i]!;
    if (i > 0 && b.time - bars[i - 1]!.time > 3600) {
      prevSessionStart = sessionStart;
      sessionStart = i;
    }
    const minute = minuteOf(b.time, tz);
    const closesAt = minute + chartSecs / 60;
    while (known < pivots.length && pivots[known]!.confirmedAt <= i) known++;
    let trend = bias[i]!;

    // ── a session break (gap over an hour, e.g. after an early close) flattens at the last candle before it ──
    if (open && i > open.entryIndex && b.time - bars[i - 1]!.time > 3600) {
      closeTrade(open, i - 1, bars[i - 1]!.close, 'time');
      open = null;
    }

    // ── manage the open trade ──
    if (open) {
      const long = open.dir === 'long';
      // the stop in force: moved only by candles before this one
      const stopPx = open.stopNow;
      const hitStop = long ? b.low <= stopPx : b.high >= stopPx;
      const hitTarget = long ? b.high >= open.target : b.low <= open.target;
      const onEntryBar = i === open.entryIndex;
      let exit: number | null = null;
      if (hitStop) {
        exit = long ? Math.min(stopPx, b.open) : Math.max(stopPx, b.open);
        if (onEntryBar) exit = stopPx;
        open.outcome = open.stopMoves.length ? open.stopMoves[open.stopMoves.length - 1]!.why : 'stop';
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
      } else if (i > open.entryIndex) {
        // still open at this close: breakeven / trail for the candles after it
        manageStop(open, i);
      }
    }

    // ── fill a pending pull-back order ──
    if (!open && pending) {
      const long = pending.dir === 'long';
      if (i > pending.until) {
        skips.push({ index: pending.confirmIndex, price: bars[pending.confirmIndex]!.close, dir: pending.dir, step: 'entry', reason: `pull-back to ${pending.limit.toFixed(2)} never came` });
        retire(pending.leg, 'expired', i);
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
          open = openTrade(pending.leg, pending.touch, pending.dir, fill, pending.stop, pending.target, pending.confirmIndex, i, pending.warnings, flag, pending.rule, pending.flow, pending.gamma);
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

    // ── choose the active leg: sticky once touched, else the week range and/or the latest swing leg ──
    const keepActive = active && !active.dead && active.touchIndex != null;
    /** Advance a leg's state to this candle (catching up on candles it was not followed for). */
    const bring = (l: Leg) => {
      for (let j = l.seen + 1; j <= i; j++) stepLeg(l, bars, j);
      l.seen = Math.max(l.seen, i);
    };
    let weekLeg: Leg | null = null;
    if (weeks) {
      const r = weeks[i];
      if (r) {
        const key = `w${r.from}-${r.to}`;
        weekLeg = legs.get(key) ?? null;
        if (!weekLeg) {
          const [from, to] = weekPivots(bars, r, i);
          weekLeg = makeLeg(bars, from, to, cfg.valueRows, cfg.zone, 'week range');
          legs.set(key, weekLeg);
        }
      }
    }
    // the swing leg follows the 4h structure (structure rule; none while it is sideways) or the 1h trend (older rule)
    const legTrend: Bias = structural ? (struct4h![i]?.dir ?? 'neutral') : trend;
    let swingLeg: Leg | null = null;
    if (swingLegs && !(structural && legTrend === 'neutral')) {
      let pair: [Pivot, Pivot] | null = null;
      if (cfg.legRule === 'structure' && legTrend !== 'neutral') {
        // the leg today's swings are building (a lower high inside an up leg is a pull-back, not a new leg);
        // before today has one, yesterday's
        const dir = legTrend === 'bull' ? 'up' : 'down';
        // NSE (`sessionLegs`): a session that ran straight from the open starts its leg at the opening extreme
        const inRange = (from: number, to: number) => {
          const ps = pivots.slice(0, known).filter((p) => p.index >= from && p.index < to);
          return cfg.sessionLegs ? withSessionOpen(bars, from, ps) : ps;
        };
        pair =
          structureLeg(inRange(sessionStart, Infinity), dir) ??
          (sessionStart > 0 ? structureLeg(inRange(prevSessionStart, sessionStart), dir) : null);
      } else {
        for (let k = known - 1; k >= 1; k--) {
          const to = pivots[k]!;
          const dir = to.kind === 'H' ? 'up' : 'down';
          if (legTrend === 'bull' && dir !== 'up') continue;
          if (legTrend === 'bear' && dir !== 'down') continue;
          // NSE: the leg starts inside today's session, not across the overnight gap
          const from = cfg.sessionLegs
            ? sessionAnchoredFrom(bars, pivots[k - 1]!, to, (a14[to.index] ?? 0) * cfg.swingMult)
            : pivots[k - 1]!;
          pair = [from, to];
          break;
        }
      }
      if (pair) {
        const key = `${pair[0].index}-${pair[1].index}`;
        let l = legs.get(key);
        if (!l) {
          l = makeLeg(bars, pair[0], pair[1], cfg.valueRows, cfg.zone);
          legs.set(key, l);
        }
        swingLeg = l;
      }
    }
    for (const l of new Set([keepActive ? active : null, weekLeg, swingLeg])) if (l) bring(l);
    if (!keepActive) {
      const live = (l: Leg | null) => (l && !l.dead ? l : null);
      const touched = (l: Leg | null) => (l && !l.dead && l.touchIndex != null ? l : null);
      if (cfg.range === 'week') active = weekLeg;
      else if (cfg.range === 'swing') active = swingLeg;
      else {
        // both: a zone price has reached wins (the week range first); otherwise show the live one nearer to price
        const w = live(weekLeg);
        const s = live(swingLeg);
        const dist = (l: Leg) => Math.abs(b.close - l.zoneNear);
        active =
          touched(weekLeg) ?? touched(swingLeg) ?? (w && s ? (dist(s) < dist(w) ? s : w) : (w ?? s ?? weekLeg ?? swingLeg));
      }
    }
    const leg = active;
    // the week range sets its own trend: its direction, unclear once a candle closed past its 0.886
    const weekTrend: Bias = !weekLeg || weekLeg.dead === 'invalidated' ? 'neutral' : weekLeg.dir === 'up' ? 'bull' : 'bear';
    if (cfg.range === 'week') trend = weekTrend;
    else if (leg?.noun === 'week range' || (!leg && cfg.range === 'both')) trend = weekTrend;
    else if (structural) trend = legTrend;

    // ── the four steps at this candle ──
    const checks: Record<StepKey, Check> = {
      environment: structural
        ? structureCheck(struct4h![i]!, struct1h![i]!, dayRange![i]!, trend, leg, cfg)
        : envCheck(envs1h![i]!, envs4h![i]!, trend, leg, cfg),
      location: alsoWatching(locationCheck(leg, b, cfg, { bars, tz: shownTz, sessionStart }), leg, weekLeg, swingLeg),
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
        // 'beyond': past the extreme candle's other end; 'midpoint': past its middle (his "closes away from the
        // level"; the candle cannot have made a new extreme, or it would be the extreme candle itself)
        const lvl = cfg.candleRule === 'midpoint' ? (ext.high + ext.low) / 2 : long ? ext.high : ext.low;
        flips = i > leg.extremeIndex && (long ? b.close > b.open && b.close > lvl : b.close < b.open && b.close < lvl);
        const where = cfg.candleRule === 'midpoint' ? ' (the high candle’s middle)' : '';
        waitText = long
          ? `in the zone; waiting for a green candle closing above ${lvl.toFixed(2)}${where}`
          : `in the zone; waiting for a red candle closing below ${lvl.toFixed(2)}${where}`;
      }
      const inWindow = minute >= cfg.windowStart && minute < cfg.windowEnd;
      if (i - leg.touchIndex > cfg.confirmWithin) {
        const rearmed = retire(leg, 'expired', i);
        const reason =
          cfg.confirm !== 'orderflow'
            ? `no confirmation within ${cfg.confirmWithin} candles of the touch`
            : leg.flowNoData
              ? 'no footprint data for the order-flow check'
              : `no absorption + delta flip within ${cfg.confirmWithin} candles of the touch`;
        if (rearmed) {
          // the week range: price may still be in the zone, so watch again (not a decision, so no skip mark)
          checks.confirmation = { state: 'wait', text: `no confirmation within ${cfg.confirmWithin} candles; watching the zone again` };
        } else {
          skips.push({ index: i, price: bars[i]!.close, dir: long ? 'long' : 'short', step: 'confirmation', reason });
          checks.confirmation = { state: 'fail', text: 'no confirmation came in time' };
        }
      } else if (!flips || i > lastDecidable) {
        checks.confirmation = { state: 'wait', text: waitText };
      } else if (!inWindow) {
        checks.confirmation = { state: 'fail', text: `confirmed at ${fmtTime(b.time, tz)} ${market.clock}, outside the ${windowText(cfg)} ${market.clock} window` };
        skips.push({ index: i, price: bars[i]!.close, dir: long ? 'long' : 'short', step: 'confirmation', reason: `confirmation outside ${windowText(cfg)} ${market.clock} time` });
        retire(leg, 'used', i);
      } else {
        checks.confirmation = {
          state: 'pass',
          text: flow ? waitText : `pressure flipped at ${fmtTime(b.time, shownTz)} (estimated from candles)`,
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
        const touch = leg.touchIndex ?? i;
        retire(leg, 'used', i);
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
          else open = openTrade(leg, touch, dir, b.close, stop, target, i, i, decision.warnings, flag, rule, flow, gamma);
        } else {
          pending = { leg, dir, limit: entryPx, stop, target, confirmIndex: i, until: i + cfg.entryWait, warnings: decision.warnings, rule, flow, gamma, touch };
        }
      }
    }

    reads.push(snaps ? { index: i, checks, tradeId: open?.id ?? null, gamma: readOf(snapFor(i), b.close) } : { index: i, checks, tradeId: open?.id ?? null });
  }
  return { reads, trades, skips };

  /**
   * A setup on this leg is over (confirmation expired, decided, or its order never filled). The week range re-arms,
   * since price may still be in its zone, until it has had `legTrades` trades; a swing leg is done. True = re-armed.
   */
  function retire(leg: Leg, why: 'expired' | 'used', i: number): boolean {
    if (leg.noun === 'week range' && leg.trades < cfg.legTrades) {
      leg.touchIndex = null;
      leg.extremeIndex = null;
      leg.flowNoData = false;
      return true;
    }
    leg.dead = why;
    leg.deadAt = i;
    return false;
  }

  /**
   * `flowRule: 'tree'`, his decision tree: aggression at the level that failed to produce a result
   * (`failedAggression`), then a candle whose delta flips to our side and closes our way without trading past the
   * aggression candle. POC in the wick is shown when it is there, never required.
   */
  function treeRead(i: number, leg: Leg, side: FlowSide): { flow: TradeFlow | null; text: string } {
    const long = side === 'long';
    const who = long ? 'selling' : 'buying';
    const past = long ? 'lower low' : 'higher high';
    const r = failedAggression(bars, fps, leg.touchIndex!, i, side, leg.zoneNear);
    if (r.noData) {
      leg.flowNoData = true;
      return { flow: null, text: `in the zone; no footprint for the candles at the level to read the ${who}` };
    }
    leg.flowNoData = false;
    if (!r.agg) return { flow: null, text: `in the zone; waiting for ${who} at the level that fails (no ${past} after it)` };
    const j = r.agg.index;
    const d = Math.round(r.agg.delta);
    const wick = absorption(bars, fps?.[j], j, side, 0, 'video')?.ok ? ', POC in the wick' : '';
    const aggText = `${who} at ${fmtTime(bars[j]!.time, shownTz)} (delta ${d >= 0 ? '+' : '−'}${Math.abs(d).toLocaleString('en-US')} on ${Math.round(r.agg.volume).toLocaleString('en-US')}${wick}) failed: no ${past} since`;
    const flip = deltaFlip(bars, fps?.[i], i, j, side, 0, 'video');
    if (!flip) {
      leg.flowNoData = true;
      return { flow: null, text: `${aggText}; no footprint for this candle` };
    }
    if (!flip.ok) return { flow: null, text: `${aggText}; waiting for the flip: ${flip.why}` };
    const ticks = fps?.[j]?.source === 'ticks' && fps[i]?.source === 'ticks';
    const fd = Math.round(fps![i]!.delta);
    const usual = usualVolume(bars, j);
    return {
      flow: {
        source: ticks ? 'ticks' : 'estimated',
        absorbIndex: j,
        absorbPrice: long ? bars[j]!.low : bars[j]!.high,
        volRatio: usual > 0 ? bars[j]!.volume / usual : 0,
        flipShare: flip.share,
      },
      text: `${aggText}, delta flipped ${fd >= 0 ? '+' : '−'}${Math.abs(fd).toLocaleString('en-US')} at ${fmtTime(bars[i]!.time, shownTz)} (${ticks ? 'recorded ticks' : 'estimated footprint'})`,
    };
  }

  /**
   * Which candle to read as the absorption: the one holding the extreme since the touch ('extreme'), or
   * (`absorbAt: 'zone'`, his "at the level") the latest candle since the touch that traded into the zone, that is
   * absorption, and that no candle since (this one included) has traded past ("next candle fails to continue").
   * Falls back to the extreme candle, so the checklist explains that one when none qualifies.
   */
  function absorbIndex(i: number, leg: Leg, side: FlowSide): number {
    const e = leg.extremeIndex!;
    if (cfg.absorbAt !== 'zone' || leg.touchIndex == null) return e;
    const long = side === 'long';
    for (let j = i - 1; j >= leg.touchIndex; j--) {
      const c = bars[j]!;
      if (!(long ? c.low <= leg.zoneNear : c.high >= leg.zoneNear)) continue;
      let held = true;
      for (let k = j + 1; k <= i && held; k++) if (long ? bars[k]!.low < c.low : bars[k]!.high > c.high) held = false;
      if (!held) continue;
      if (absorption(bars, fps?.[j], j, side, cfg.absorbVol, cfg.flowRule)?.ok) return j;
    }
    return e;
  }

  /** POC of the last 15-minute block completed by candle i's close, from the footprint; null without one. */
  function poc15(i: number): number | null {
    if (!fps) return null;
    if (chartSecs >= 900) {
      const f = fps[i];
      return f ? (f.levels[f.poc]?.price ?? null) : null;
    }
    const start = Math.floor((bars[i]!.time + chartSecs) / 900) * 900 - 900;
    const vol = new Map<number, number>();
    for (let j = i; j >= 0 && bars[j]!.time >= start; j--) {
      if (bars[j]!.time >= start + 900) continue;
      const f = fps[j];
      if (!f) return null;
      for (const l of f.levels) vol.set(l.price, (vol.get(l.price) ?? 0) + l.bid + l.ask);
    }
    let best: number | null = null;
    let most = -1;
    for (const [price, v] of vol) if (v > most) [best, most] = [price, v];
    return best;
  }

  /**
   * After candle i closed with trade t still open: breakeven, then the trail. Each move applies from candle i + 1 on
   * (the caller tests candle i against the stop that stood before it). Only tightens, and never past i's close.
   */
  function manageStop(t: ArTrade, i: number) {
    const b = bars[i]!;
    const long = t.dir === 'long';
    const tighter = (p: number) => (long ? p > t.stopNow + 1e-9 : p < t.stopNow - 1e-9);
    const placeable = (p: number) => (long ? p < b.close : p > b.close);
    const move = (p: number, why: StopMove['why']) => {
      t.stopNow = p;
      t.stopMoves.push({ index: i, price: p, why });
    };
    if (cfg.breakeven > 0 && t.breakevenAt == null) {
      const need = cfg.breakevenUnit === 'R' ? cfg.breakeven * Math.abs(t.entry - t.stop) : cfg.breakeven;
      // armed by any completed candle since the fill that went far enough; applied once the entry is placeable
      if (!beArmed.has(t.id) && (long ? b.high - t.entry : t.entry - b.low) >= need) beArmed.add(t.id);
      if (beArmed.has(t.id) && placeable(t.entry)) {
        t.breakevenAt = i;
        if (tighter(t.entry)) move(t.entry, 'breakeven');
      }
    }
    const trailing = cfg.trail !== 'off' && (cfg.breakeven <= 0 || t.breakevenAt != null);
    if (!trailing) return;
    const buf = (a14[i] ?? 0) * cfg.stopBuffer;
    const poc = cfg.trail === 'poc15' ? poc15(i) : null;
    const p = poc != null ? (long ? poc - buf : poc + buf) : long ? b.low - buf : b.high + buf;
    if (tighter(p) && placeable(p)) move(p, 'trail');
  }

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
    touch: number,
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
      touchIndex: touch,
      confirmIndex,
      entryIndex,
      entry,
      stop,
      target,
      exitIndex: null,
      exit: null,
      outcome: 'open',
      stopNow: stop,
      stopMoves: [],
      breakevenAt: null,
      points: null,
      r: null,
      warnings,
      stack,
      entryRule,
      flow,
      gamma,
    };
    trades.push(t);
    leg.trades++;
    if (leg.noun === 'week range' && leg.trades >= cfg.legTrades && !leg.dead) {
      leg.dead = 'used';
      leg.deadAt = entryIndex;
    }
    return t;
  }

  /**
   * Order-flow confirmation at candle i (lib/tv/ar-orderflow.ts): is the extreme candle since the touch absorption,
   * and does candle i flip the delta? `flow` is set only when both hold; `text` is the checklist line either way.
   */
  function flowRead(i: number, leg: Leg, side: FlowSide): { flow: TradeFlow | null; text: string } {
    if (cfg.flowRule === 'tree') return treeRead(i, leg, side);
    const e = absorbIndex(i, leg, side);
    const end = side === 'long' ? 'low' : 'high';
    const at = fmtTime(bars[e]!.time, shownTz);
    const abs = absorption(bars, fps?.[e], e, side, cfg.absorbVol, cfg.flowRule);
    if (!abs) {
      leg.flowNoData = true;
      return { flow: null, text: `in the zone; no footprint for the ${end} candle (${at}) to check for absorption` };
    }
    leg.flowNoData = false;
    if (!abs.ok) return { flow: null, text: `in the zone; the ${end} candle (${at}) is not absorption: ${abs.why.join(', ')}` };
    const absText =
      cfg.flowRule === 'video'
        ? `absorption at ${at} (POC in the ${side === 'long' ? 'lower' : 'upper'} wick, ${abs.volRatio.toFixed(1)}× volume)`
        : `absorption at ${at} (${abs.volRatio.toFixed(1)}× volume, POC at the ${end})`;
    if (i <= e) return { flow: null, text: `${absText}; waiting for the delta to flip` };
    const flip = deltaFlip(bars, fps?.[i], i, e, side, cfg.flipShare, cfg.flowRule);
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
      text: `${absText}, delta flipped ${d >= 0 ? '+' : '−'}${Math.abs(Math.round(d))} at ${fmtTime(bars[i]!.time, shownTz)} (${ticks ? 'recorded ticks' : 'estimated footprint'})`,
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

const trendWord: Record<Bias, string> = { bull: 'up', bear: 'down', neutral: 'unclear' };

const envWord: Record<EnvironmentRead['env'], string> = { expansion: 'trending', rotation: 'sideways', compression: 'choppy' };

function envCheck(
  env1h: EnvironmentRead | null,
  env4h: EnvironmentRead | null,
  trend: Bias,
  leg: Leg | null,
  cfg: ArStrategyCfg,
): Check {
  const week = leg ? leg.noun === 'week range' : cfg.range !== 'swing';
  const t = week ? `week range ${trendWord[trend]}` : `1h trend ${trendWord[trend]}`;
  if (!env1h || !env4h) return { state: 'wait', text: `${t}; not enough 1h/4h candles to read the market yet` };
  const m = `1h ${envWord[env1h.env]}, 4h ${envWord[env4h.env]}`;
  if (env1h.env === 'compression' || env4h.env === 'compression') return { state: 'fail', text: `${t}; ${m}: choppy, no trade` };
  if (trend === 'neutral')
    return {
      state: 'fail',
      text: week
        ? `${t}: no week range yet, or price closed past its 0.886 (structure may be turning), so no trade until a new high or low · ${m}`
        : `${t}: the latest swings show no clear direction, so no trade · ${m}`,
    };
  const side1 = env1h.env === 'rotation';
  const side4 = env4h.env === 'rotation';
  const sideways = cfg.sidewaysWhen === 'either' ? side1 || side4 : side1 && side4;
  if (sideways)
    return cfg.sideways === 'block'
      ? { state: 'fail', text: `${t}; ${m}: sideways, no trade` }
      : { state: 'warn', text: `${t}; ${m}: sideways, so pullbacks follow through less` };
  const aligned = leg && ((trend === 'bull' && leg.dir === 'up') || (trend === 'bear' && leg.dir === 'down'));
  if (leg && !aligned) return { state: 'fail', text: `${t}; this swing goes against it: no trade` };
  return { state: 'pass', text: `${t}; ${m}` };
}

const dirWord: Record<Bias, string> = { bull: 'up', bear: 'down', neutral: 'sideways' };

/** Step 1 with `envRule: 'structure'`: 4h structure decides, the 1h is shown, the last day's range catches chop. */
function structureCheck(
  s4: StructureRead | null,
  s1: StructureRead | null,
  day: number | null,
  trend: Bias,
  leg: Leg | null,
  cfg: ArStrategyCfg,
): Check {
  const week = leg ? leg.noun === 'week range' : cfg.range !== 'swing';
  const t = week ? `week range ${trendWord[trend]}` : leg ? `intraday swing ${leg.dir}` : 'no intraday swing';
  const one = s1 ? ` · 1h ${dirWord[s1.dir]}` : '';
  if (day != null && day < cfg.compress)
    return {
      state: 'fail',
      text: `${t}; choppy: the last day's range is ${day.toFixed(2)}× a normal day's, no room for the reward, no trade${one}`,
    };
  if (!s4) return { state: 'wait', text: `${t}; not enough 4h swings yet to read the market` };
  if (trend === 'neutral')
    return {
      state: 'fail',
      text: week
        ? `${t}: no week range yet, or price closed past its 0.886 (structure may be turning), so no trade until a new high or low · 4h ${dirWord[s4.dir]}`
        : `4h sideways (${s4.why}): an intraday swing needs a 4h trend to follow, so no trade${one}`,
    };
  const legDir: Bias = leg ? (leg.dir === 'up' ? 'bull' : 'bear') : trend;
  if (s4.dir === 'neutral') {
    return cfg.sideways === 'block'
      ? { state: 'fail', text: `${t}; 4h sideways: ${s4.why}, no trade${one}` }
      : { state: 'warn', text: `${t}; 4h sideways: ${s4.why}, so only a fade of the range's edge${one}` };
  }
  if (legDir !== s4.dir)
    return {
      state: 'fail',
      text: `${t}; 4h trending ${dirWord[s4.dir]} (${s4.why}) but the ${leg ? 'zone' : 'trend'} points ${dirWord[legDir]}: no trade${one}`,
    };
  if (!week && leg && trend !== legDir) return { state: 'fail', text: `${t}; this swing goes against it: no trade` };
  return { state: 'pass', text: `${t}; 4h trending ${dirWord[s4.dir]}: ${s4.why}${one}` };
}

/** `range: 'both'`: name the other live zone too, so the checklist shows every zone being watched. */
function alsoWatching(check: Check, leg: Leg | null, weekLeg: Leg | null, swingLeg: Leg | null): Check {
  const other = leg === weekLeg ? swingLeg : leg === swingLeg ? weekLeg : null;
  if (!other || other.dead || other === leg) return check;
  const [lo, hi] = [Math.min(other.zoneNear, other.zoneFar), Math.max(other.zoneNear, other.zoneFar)];
  const what = other.noun === 'week range' ? 'week range' : 'intraday swing';
  return { ...check, text: `${check.text} · also watching the ${what} ${other.dir === 'up' ? 'discount' : 'premium'} zone ${lo.toFixed(2)}–${hi.toFixed(2)}` };
}

function locationCheck(leg: Leg | null, b: Bar, cfg: ArStrategyCfg, c: LegCtx): Check {
  if (!leg)
    return {
      state: 'wait',
      text: cfg.range !== 'swing' ? `building the week range (needs ${cfg.rangeDays} days of candles)` : 'no swing in the trend’s direction yet',
    };
  const next = leg.noun === 'week range' ? 'a new high or low' : 'a new swing';
  const side = leg.dir === 'up' ? 'discount' : 'premium';
  const idea = leg.dir === 'up' ? 'long' : 'short';
  const name = legName(leg, c);
  const when = leg.deadAt != null ? fmtWhen(c.bars[leg.deadAt]!.time, c.tz) : '';
  if (leg.dead === 'invalidated') {
    // cancelled in this session: a red mark; an older cancel is just history, so wait for the next swing
    if (leg.deadAt != null && leg.deadAt >= c.sessionStart)
      return { state: 'fail', text: `closed past ${leg.farName} (${leg.zoneFar.toFixed(2)}) at ${when}: ${idea} zone cancelled · ${name}` };
    return { state: 'wait', text: `the last ${idea} zone was cancelled on ${when} (${name}): waiting for ${next}` };
  }
  if (leg.dead === 'extended')
    return { state: 'wait', text: `price ran past the ${leg.dir === 'up' ? 'high' : 'low'} ${leg.to.price.toFixed(2)} at ${when} before pulling back: waiting for ${next}` };
  if (leg.dead === 'used')
    return {
      state: 'wait',
      text:
        leg.noun === 'week range'
          ? `this week range has had its ${cfg.legTrades} trades (last at ${when}, ${name}): waiting for ${next}`
          : `this swing’s setup was decided at ${when} (${name}): waiting for ${next}`,
    };
  if (leg.dead === 'expired') return { state: 'wait', text: `this ${leg.noun}’s setup ran out of time at ${when} (${name}): waiting for ${next}` };
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
