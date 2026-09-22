/** One trade's session: which stored bars belong to it, and the indicators
 * drawn on them.
 *
 * Deliberately derived rather than stored: the formulas stay visible and can
 * change without rewriting recorded trades (see ledger.chart_bars_doc). They
 * mirror the engine's own definitions in `indicators.py` — where they drift,
 * the operator is reading a chart that does not describe the decision, which
 * is what happened to VWAP and to the MACD signal line before 2026-09-22.
 */

import type { MomentumBar, MomentumTrade } from './momentum-api';

const MINUTE_MS = 60_000;
const DAY_MS = 24 * 60 * MINUTE_MS;
/** NSE runs on IST; bar timestamps may arrive with any offset (5-minute bars
 * are re-serialised as UTC), so the session day is derived by shifting rather
 * than by reading the string. */
const IST_OFFSET_MS = 5.5 * 60 * MINUTE_MS;
/** Mirrors bars.SESSION_OPEN in the scanner: 09:15 IST. */
const SESSION_OPEN_MS = (9 * 60 + 15) * MINUTE_MS;

/** Whole days since the epoch in IST — equal for two timestamps in one session. */
function istDay(ms: number): number {
  return Math.floor((ms + IST_OFFSET_MS) / DAY_MS);
}

/** Milliseconds past IST midnight. */
function istTimeOfDay(ms: number): number {
  return ((ms + IST_OFFSET_MS) % DAY_MS + DAY_MS) % DAY_MS;
}

/** The IST session a bar belongs to, as a comparable number; null if unparseable. */
export function sessionDayKey(time: string): number | null {
  const at = Date.parse(time);
  return Number.isFinite(at) ? istDay(at) : null;
}

/** Only the bars belonging to `session`'s own NSE session.
 *
 * Trades stored before 2026-09-22 carry two rows the scanner never meant to
 * hand the engine: the previous session's last trade (the feed's first
 * snapshot arrives with its own last-trade time) and the pre-open auction
 * print around 09:08. Left in, they stretch the price axis to yesterday's
 * price (RHIM 2026-09-22: a floor of 367.00 under a session that never traded
 * below 378.25) and start VWAP there. The scanner now cuts them at the source;
 * this keeps every already-stored trade honest too.
 */
export function sessionBars(bars: MomentumBar[], session: string | null | undefined): MomentumBar[] {
  const reference = session ? Date.parse(session) : Number.NaN;
  const last = bars.length ? Date.parse(bars[bars.length - 1]!.time) : Number.NaN;
  const day = istDay(Number.isFinite(reference) ? reference : last);
  if (!Number.isFinite(day)) return bars;
  return bars.filter((bar) => {
    const at = Date.parse(bar.time);
    return Number.isFinite(at) && istDay(at) === day && istTimeOfDay(at) >= SESSION_OPEN_MS;
  });
}

export type Point = MomentumBar & {
  ema9: number | null;
  ema20: number | null;
  vwap: number | null;
  macd: number | null;
  signal: number | null;
  histogram: number | null;
};

/** EMA with pandas' `adjust=False, min_periods=span` semantics. */
export function ema(values: number[], span: number) {
  const alpha = 2 / (span + 1);
  let value: number | null = null;
  return values.map((close, index) => {
    value = value === null ? close : close * alpha + value * (1 - alpha);
    return index < span - 1 ? null : value;
  });
}

export function points(bars: MomentumBar[]): Point[] {
  const closes = bars.map((bar) => bar.close);
  const ema9 = ema(closes, 9);
  const ema20 = ema(closes, 20);
  const fast = ema(closes, 12);
  const slow = ema(closes, 26);
  const macd = fast.map((value, i) => {
    const slowValue = slow[i] ?? null;
    return value === null || slowValue === null ? null : value - slowValue;
  });
  // The signal line is an EMA of the MACD line, so it may only see the bars
  // where that line exists. Feeding the leading nulls in as zeros (as this did
  // until 2026-09-22) both starts the line ~10 bars early and drags it toward
  // zero: on RHIM 2026-09-22 it drew a signal from 09:40, and at 09:50 read
  // -1.81 against a true -1.96 on a histogram whose full scale is about ±0.5.
  // Matches pandas' ewm(min_periods=9), which counts only real observations.
  const firstMacd = macd.findIndex((value) => value !== null);
  const signalTail = firstMacd < 0 ? [] : ema(macd.slice(firstMacd).map((value) => value ?? 0), 9);
  const signal = macd.map((_value, i) =>
    firstMacd < 0 || i < firstMacd ? null : signalTail[i - firstMacd] ?? null,
  );
  // VWAP is a session measure and resets at each open, exactly as
  // indicators.session_vwap does on the engine side. One running sum across a
  // day boundary would let a single stale bar anchor the whole line.
  let cumPv = 0;
  let cumVol = 0;
  let day: number | null = null;
  const indicators = bars.map((bar, i) => {
    const barDay = sessionDayKey(bar.time);
    if (barDay !== null && day !== null && barDay !== day) {
      cumPv = 0;
      cumVol = 0;
    }
    if (barDay !== null) day = barDay;
    cumPv += ((bar.high + bar.low + bar.close) / 3) * bar.volume;
    cumVol += bar.volume;
    return {
      ema9: ema9[i] ?? null,
      ema20: ema20[i] ?? null,
      vwap: cumVol ? cumPv / cumVol : null,
      macd: macd[i] ?? null,
      signal: signal[i] ?? null,
      histogram: (macd[i] ?? null) === null || (signal[i] ?? null) === null
        ? null
        : macd[i]! - signal[i]!,
    };
  });
  return bars.map((bar, i) => {
    const indicator = indicators[i] ?? null;
    return {
      ...bar,
      ema9: indicator?.ema9 ?? null,
      ema20: indicator?.ema20 ?? null,
      vwap: indicator?.vwap ?? null,
      macd: indicator?.macd ?? null,
      signal: indicator?.signal ?? null,
      histogram: indicator?.histogram ?? null,
    };
  });
}


/** A horizontal support/resistance line on a trade's chart. */
export type TradeLevel = {
  label: string;
  price: number;
  kind: string;
  side: 'resistance' | 'support';
  /** True for a level nothing gates on — drawn faint and dashed. */
  faint: boolean;
};

/** The levels worth drawing on a trade, and what each one actually governs.
 *
 * Two different things get called "resistance" in a stored trade, and they are
 * not interchangeable:
 *
 *   * `structural_resistance` / `structural_support` — what the exit rules
 *     hold for the life of the position (the resistance-reject exit and the
 *     structural stop read exactly these), fixed at entry.
 *   * `resist_px` / `support_px` — the recorded-only location metrics: the
 *     nearest level on the FIVE-minute frame at the trigger. Nothing gates on
 *     them. The chart drew only these, labelled "Resistance", which is how a
 *     trade could appear to have been bought into a ceiling it never touched.
 *
 * Older rows predate the absolute prices and carry only the percentages. Those
 * are measured from the setup's TRIGGER, so they are rebuilt from
 * `level_anchor_px`/`trigger_px` and never from the fill: on RHIM 2026-09-22
 * the fill sat ₹1.30 above the trigger and dragged both lines up with it.
 */
export function tradeLevels(trade: MomentumTrade): TradeLevel[] {
  const anchor = trade.level_anchor_px ?? trade.trigger_px ?? trade.entry_price;
  const fromPct = (px: number | null | undefined, pct: number | null | undefined, sign: 1 | -1) =>
    px ?? (pct != null && anchor > 0 ? anchor * (1 + (sign * pct) / 100) : null);
  const nearestResistance = fromPct(trade.resist_px, trade.resist_head_pct, 1);
  const nearestSupport = fromPct(trade.support_px, trade.support_drop_pct, -1);
  const out: TradeLevel[] = [];
  const add = (label: string, price: number | null, kind: string | undefined,
               side: 'resistance' | 'support', faint: boolean) => {
    if (price === null || !Number.isFinite(price)) return;
    // A second line is only worth drawing when it is a different price.
    if (out.some((level) => level.side === side && Math.abs(level.price - price) / price <= 0.0005)) return;
    out.push({ label, price, kind: kind ?? '', side, faint });
  };
  add('Resistance', trade.structural_resistance ?? nearestResistance,
      trade.structural_resistance != null ? trade.structural_resistance_kind : trade.resist_kind,
      'resistance', false);
  add('Support', trade.structural_support ?? nearestSupport,
      trade.structural_support != null ? trade.structural_support_kind : trade.support_kind,
      'support', false);
  add('5m nearest resistance', nearestResistance, trade.resist_kind, 'resistance', true);
  add('5m nearest support', nearestSupport, trade.support_kind, 'support', true);
  return out;
}
