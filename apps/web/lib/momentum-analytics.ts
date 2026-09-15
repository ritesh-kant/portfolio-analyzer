/**
 * Trade-journal analytics for momentum paper trades.
 *
 * Everything here is a pure function over a slim trade list, so the dashboard
 * can recompute every panel on each filter click without another round trip,
 * and so the arithmetic can be unit-tested without a browser or a database.
 *
 * Two rules the whole module keeps:
 *   1. Every bucket carries its own `trades` count. A ₹/trade number over
 *      three trades is not a measurement, and the UI needs `n` to say so.
 *   2. Gross and net are always reported side by side. On this strategy the
 *      gross edge is ≈ 0 and costs are the entire loss, so a net-only view
 *      would hide which of the two a bucket is actually telling you about.
 */

const IST = 'Asia/Kolkata';

export interface AnalyticsTrade {
  _id: string;
  symbol: string;
  setup?: string;
  strategy?: string;
  entry_time: string;
  exit_time?: string;
  entry_price: number;
  exit_price?: number;
  stop?: number;
  qty?: number;
  notional_inr?: number;
  risk_inr?: number;
  gross_inr?: number;
  costs_inr?: number;
  net_inr?: number;
  /** Portion of `costs_inr` that is a backtest cost stress, not a broker charge. */
  stress_inr?: number;
  exit_reason?: string;
  day_chg_pct?: number;
  rvol?: number;
  atr_pct?: number | null;
  macd_hist?: number | null;
  pullback_ord?: number | null;
  resist_head_pct?: number | null;
  support_drop_pct?: number | null;
  catalyst?: number;
  event_type?: string;
  candle_tags?: string[];
  quality_reason?: string;
  prev_day_gainer?: number | boolean;
}

export interface TradeSource {
  id: string;
  kind: 'live' | 'backtest';
  label: string;
  trades: number;
  from: string | null;
  to: string | null;
  sourceFile?: string | null;
  importedAt?: string | null;
  stressSlip: number;
}

/**
 * Which cost schedule the P&L is measured against.
 *
 * `recorded` shows each source as it was stored. The other two put live and
 * backtest on the same footing: backtests charge a +40 bps/side stress on top
 * of real MIS costs, live paper charges none, so comparing the two as recorded
 * compares cost models rather than trading.
 */
export type CostModel = 'recorded' | 'real' | 'stress';

/** bt17's cost stress, mirrored from src/momentum_trader/engine.py STRESS_SLIP. */
export const STRESS_SLIP = 0.004;

export const COST_MODELS: { id: CostModel; label: string; hint: string }[] = [
  { id: 'recorded', label: 'As recorded', hint: 'Each source exactly as it was stored.' },
  {
    id: 'real',
    label: 'Real broker costs',
    hint: 'Backtest cost stress removed — brokerage, STT, stamp, GST and the modelled slippage only (~0.21% round trip).',
  },
  {
    id: 'stress',
    label: 'Stress-tested',
    hint: 'A +40 bps/side execution stress added on top of real costs (~0.80% round trip), the assumption the backtests were gated on.',
  },
];

/** Cost in rupees for one trade under the chosen model. */
export function costsUnder(trade: AnalyticsTrade, model: CostModel): number {
  const recorded = trade.costs_inr ?? 0;
  if (model === 'recorded') return recorded;
  const gross = (trade.entry_price + (trade.exit_price ?? trade.entry_price)) * (trade.qty ?? 0);
  const stress = trade.stress_inr ?? 0;
  if (model === 'real') return recorded - stress;
  // `stress`: add the stress only to sources that were not already charged it.
  return stress > 0 ? recorded : recorded + gross * STRESS_SLIP;
}

export function netUnder(trade: AnalyticsTrade, model: CostModel): number {
  return (trade.gross_inr ?? 0) - costsUnder(trade, model);
}

// ── derived per-trade measures ───────────────────────────────────────────────

interface IstParts {
  date: string;
  weekday: string;
  hour: number;
  minute: number;
}

const IST_FORMAT = new Intl.DateTimeFormat('en-GB', {
  timeZone: IST,
  weekday: 'short',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

/**
 * Cache keyed by the raw timestamp.
 *
 * Every filter click re-buckets the whole trade list across every dimension,
 * three of which are calendar-based. Formatting is by far the most expensive
 * step, and a trade's timestamp never changes, so it is only ever done once.
 */
const IST_CACHE = new Map<string, IstParts>();

/** Calendar parts of a timestamp in IST, where the trading session is defined. */
export function istParts(iso: string): IstParts {
  const cached = IST_CACHE.get(iso);
  if (cached) return cached;
  const parts = IST_FORMAT.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? '';
  const value: IstParts = {
    date: `${get('year')}-${get('month')}-${get('day')}`,
    weekday: get('weekday'),
    hour: Number(get('hour')),
    minute: Number(get('minute')),
  };
  IST_CACHE.set(iso, value);
  return value;
}

/** Minutes held, or undefined while a trade is still open. */
export function holdMinutes(trade: AnalyticsTrade): number | undefined {
  if (!trade.exit_time) return undefined;
  return (Date.parse(trade.exit_time) - Date.parse(trade.entry_time)) / 60_000;
}

/** Stop distance as a percentage of entry — how much room the trade was given. */
export function stopDistancePct(trade: AnalyticsTrade): number | undefined {
  if (trade.stop === undefined || !trade.entry_price) return undefined;
  const distance = trade.entry_price - trade.stop;
  return distance > 0 ? (distance / trade.entry_price) * 100 : undefined;
}

/**
 * Result in units of the risk taken ("R").
 *
 * 1R is the rupee distance from entry to the initial stop, so +2R means the
 * trade made twice what it was risking. Measured on gross so it describes the
 * price move; costs are reported separately.
 */
export function rMultiple(trade: AnalyticsTrade): number | undefined {
  if (trade.stop === undefined || trade.exit_price === undefined) return undefined;
  const risk = trade.entry_price - trade.stop;
  if (risk <= 0) return undefined;
  return (trade.exit_price - trade.entry_price) / risk;
}

// ── aggregate statistics ─────────────────────────────────────────────────────

export interface Stats {
  trades: number;
  wins: number;
  losses: number;
  scratches: number;
  winRate: number;
  grossWinRate: number;
  gross: number;
  costs: number;
  net: number;
  grossPerTrade: number;
  netPerTrade: number;
  /** Gross move as a share of money put to work — comparable across position sizes. */
  grossPctPerTrade: number;
  netPctPerTrade: number;
  avgWin: number;
  avgLoss: number;
  /** Rupees won per rupee lost. Above 1 means the winners paid for the losers. */
  profitFactor: number | null;
  bestNet: number;
  worstNet: number;
  avgR: number | null;
  avgHoldMinutes: number | null;
  turnover: number;
}

const EMPTY: Stats = {
  trades: 0, wins: 0, losses: 0, scratches: 0, winRate: 0, grossWinRate: 0,
  gross: 0, costs: 0, net: 0, grossPerTrade: 0, netPerTrade: 0,
  grossPctPerTrade: 0, netPctPerTrade: 0, avgWin: 0, avgLoss: 0, profitFactor: null,
  bestNet: 0, worstNet: 0, avgR: null, avgHoldMinutes: null, turnover: 0,
};

export function summarise(trades: AnalyticsTrade[], model: CostModel): Stats {
  if (trades.length === 0) return EMPTY;

  let gross = 0, costs = 0, net = 0, turnover = 0;
  let wins = 0, losses = 0, scratches = 0, grossWins = 0;
  let wonRupees = 0, lostRupees = 0;
  let best = -Infinity, worst = Infinity;
  let rSum = 0, rCount = 0, holdSum = 0, holdCount = 0;

  for (const trade of trades) {
    const tradeGross = trade.gross_inr ?? 0;
    const tradeCosts = costsUnder(trade, model);
    const tradeNet = tradeGross - tradeCosts;
    gross += tradeGross;
    costs += tradeCosts;
    net += tradeNet;
    turnover += trade.notional_inr ?? trade.entry_price * (trade.qty ?? 0);

    if (tradeNet > 0) { wins += 1; wonRupees += tradeNet; }
    else if (tradeNet < 0) { losses += 1; lostRupees += -tradeNet; }
    else scratches += 1;
    if (tradeGross > 0) grossWins += 1;

    if (tradeNet > best) best = tradeNet;
    if (tradeNet < worst) worst = tradeNet;

    const r = rMultiple(trade);
    if (r !== undefined) { rSum += r; rCount += 1; }
    const hold = holdMinutes(trade);
    if (hold !== undefined) { holdSum += hold; holdCount += 1; }
  }

  const n = trades.length;
  return {
    trades: n,
    wins,
    losses,
    scratches,
    winRate: wins / n,
    grossWinRate: grossWins / n,
    gross,
    costs,
    net,
    grossPerTrade: gross / n,
    netPerTrade: net / n,
    grossPctPerTrade: turnover > 0 ? (gross / turnover) * 100 : 0,
    netPctPerTrade: turnover > 0 ? (net / turnover) * 100 : 0,
    avgWin: wins > 0 ? wonRupees / wins : 0,
    avgLoss: losses > 0 ? lostRupees / losses : 0,
    profitFactor: lostRupees > 0 ? wonRupees / lostRupees : null,
    bestNet: best === -Infinity ? 0 : best,
    worstNet: worst === Infinity ? 0 : worst,
    avgR: rCount > 0 ? rSum / rCount : null,
    avgHoldMinutes: holdCount > 0 ? holdSum / holdCount : null,
    turnover,
  };
}

/** Largest peak-to-trough fall of the running net P&L, in rupees. */
export function maxDrawdown(trades: AnalyticsTrade[], model: CostModel): number {
  let running = 0;
  let peak = 0;
  let worst = 0;
  for (const trade of trades) {
    running += netUnder(trade, model);
    if (running > peak) peak = running;
    const fall = peak - running;
    if (fall > worst) worst = fall;
  }
  return worst;
}

/** Longest run of consecutive winners and of consecutive losers, in order. */
export function streaks(trades: AnalyticsTrade[], model: CostModel) {
  let win = 0, loss = 0, bestWin = 0, worstLoss = 0;
  for (const trade of trades) {
    const net = netUnder(trade, model);
    if (net > 0) { win += 1; loss = 0; if (win > bestWin) bestWin = win; }
    else if (net < 0) { loss += 1; win = 0; if (loss > worstLoss) worstLoss = loss; }
  }
  return { longestWin: bestWin, longestLoss: worstLoss };
}

// ── dimensions ───────────────────────────────────────────────────────────────

/**
 * One way of slicing the trade list.
 *
 * `bucket` returns the key a trade falls in, or undefined when the trade never
 * recorded the field — those trades are excluded from that panel rather than
 * being grouped under a fake "0", and the panel reports how many it dropped.
 */
export interface Dimension {
  id: string;
  label: string;
  question: string;
  /** Fixed bucket order; omitted for dimensions whose buckets come from the data. */
  order?: string[];
  /** Sort data-derived buckets by trade count instead of alphabetically. */
  sortByVolume?: boolean;
  /** A trade can sit in several buckets at once (e.g. candle tags). */
  multi?: boolean;
  bucket: (trade: AnalyticsTrade) => string | string[] | undefined;
}

/** Labels for every band formed by ascending cut points, low to high. */
function bandOrder(cuts: number[], format: (n: number) => string): string[] {
  const labels = [`< ${format(cuts[0]!)}`];
  for (let i = 1; i < cuts.length; i += 1) labels.push(`${format(cuts[i - 1]!)}–${format(cuts[i]!)}`);
  labels.push(`${format(cuts[cuts.length - 1]!)}+`);
  return labels;
}

/** Label for the band a value falls in — the same labels `bandOrder` lists. */
function band(value: number, cuts: number[], format: (n: number) => string): string {
  const labels = bandOrder(cuts, format);
  for (let i = 0; i < cuts.length; i += 1) {
    if (value < cuts[i]!) return labels[i]!;
  }
  return labels[labels.length - 1]!;
}

const rupees = (n: number) => (n >= 100_000 ? `₹${n / 100_000}L` : n >= 1000 ? `₹${n / 1000}k` : `₹${n}`);
const percent = (n: number) => `${n}%`;
const times = (n: number) => `${n}×`;
const minutes = (n: number) => (n >= 60 ? `${n / 60}h` : `${n}m`);

const PRICE_CUTS = [100, 250, 500, 1000, 2000];
const DAY_CHG_CUTS = [2, 4, 6, 8, 10];
const RVOL_CUTS = [2, 3, 5, 10];
const STOP_CUTS = [0.3, 0.5, 0.75, 1, 1.5];
const HOLD_CUTS = [5, 15, 30, 60, 120];
const SIZE_CUTS = [25_000, 50_000, 75_000, 100_000];
const HEADROOM_CUTS = [0.25, 0.5, 1, 2];

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];

/** Half-hour slots covering the NSE session, labelled by their start. */
const SESSION_SLOTS = (() => {
  const slots: string[] = [];
  for (let m = 9 * 60 + 15; m < 15 * 60 + 30; m += 30) {
    slots.push(`${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`);
  }
  return slots;
})();

function sessionSlot(trade: AnalyticsTrade): string | undefined {
  const { hour, minute } = istParts(trade.entry_time);
  const sinceOpen = hour * 60 + minute - (9 * 60 + 15);
  if (sinceOpen < 0) return SESSION_SLOTS[0];
  const index = Math.min(Math.floor(sinceOpen / 30), SESSION_SLOTS.length - 1);
  return SESSION_SLOTS[index];
}

const optional = (value: number | null | undefined, make: (n: number) => string) =>
  value === null || value === undefined || Number.isNaN(value) ? undefined : make(value);

export const DIMENSIONS: Dimension[] = [
  {
    id: 'time',
    label: 'Time of day',
    question: 'At what time of day do these trades work?',
    order: SESSION_SLOTS,
    bucket: sessionSlot,
  },
  {
    id: 'weekday',
    label: 'Day of week',
    question: 'Which weekday has the higher success rate?',
    order: WEEKDAYS,
    bucket: (trade) => istParts(trade.entry_time).weekday,
  },
  {
    id: 'price',
    label: 'Share price',
    question: 'Which price range of stock performs best?',
    order: bandOrder(PRICE_CUTS, rupees),
    bucket: (trade) => optional(trade.entry_price, (v) => band(v, PRICE_CUTS, rupees)),
  },
  {
    id: 'dayChg',
    label: 'Day move at entry',
    question: 'How far up should the stock already be when you buy it?',
    order: bandOrder(DAY_CHG_CUTS, percent),
    bucket: (trade) => optional(trade.day_chg_pct, (v) => band(v, DAY_CHG_CUTS, percent)),
  },
  {
    id: 'rvol',
    label: 'Relative volume',
    question: 'Does unusually heavy volume actually pay?',
    order: bandOrder(RVOL_CUTS, times),
    bucket: (trade) => optional(trade.rvol, (v) => band(v, RVOL_CUTS, times)),
  },
  {
    id: 'stop',
    label: 'Stop distance',
    question: 'Do tight stops or wide stops suit this setup?',
    order: bandOrder(STOP_CUTS, percent),
    bucket: (trade) => optional(stopDistancePct(trade), (v) => band(v, STOP_CUTS, percent)),
  },
  {
    id: 'hold',
    label: 'Time in trade',
    question: 'Are you being paid to hold, or paid to get out quickly?',
    order: bandOrder(HOLD_CUTS, minutes),
    bucket: (trade) => optional(holdMinutes(trade), (v) => band(v, HOLD_CUTS, minutes)),
  },
  {
    id: 'exit',
    label: 'Exit reason',
    question: 'Which way of leaving a trade costs the most?',
    sortByVolume: true,
    bucket: (trade) => trade.exit_reason?.replaceAll('_', ' '),
  },
  {
    id: 'setup',
    label: 'Setup',
    question: 'Which named pattern earns its place?',
    sortByVolume: true,
    bucket: (trade) => trade.setup?.replaceAll('_', ' '),
  },
  {
    id: 'strategy',
    label: 'Strategy arm',
    question: 'Which deployed arm is carrying the result?',
    sortByVolume: true,
    bucket: (trade) => trade.strategy?.replaceAll('_', ' ') ?? 'unlabelled run',
  },
  {
    id: 'symbol',
    label: 'Stock',
    question: 'Which individual names do you trade well, and which keep taking money?',
    sortByVolume: true,
    bucket: (trade) => trade.symbol,
  },
  {
    id: 'size',
    label: 'Position size',
    question: 'Do you size up on the right trades?',
    order: bandOrder(SIZE_CUTS, rupees),
    bucket: (trade) =>
      optional(trade.notional_inr ?? trade.entry_price * (trade.qty ?? 0), (v) => band(v, SIZE_CUTS, rupees)),
  },
  {
    id: 'pullback',
    label: 'Pullback number',
    question: 'Is the first pullback of the day really the best one?',
    order: ['1st', '2nd', '3rd', '4th', '5th+'],
    bucket: (trade) =>
      optional(trade.pullback_ord, (v) => (v >= 5 ? '5th+' : ['1st', '2nd', '3rd', '4th'][v - 1] ?? '1st')),
  },
  {
    id: 'macd',
    label: 'MACD at entry',
    question: 'Does momentum already rolling over change the outcome?',
    order: ['falling (< 0)', 'rising (≥ 0)'],
    bucket: (trade) => optional(trade.macd_hist, (v) => (v < 0 ? 'falling (< 0)' : 'rising (≥ 0)')),
  },
  {
    id: 'headroom',
    label: 'Room to resistance',
    question: 'How much clear air above the entry do you need?',
    order: bandOrder(HEADROOM_CUTS, percent),
    bucket: (trade) => optional(trade.resist_head_pct, (v) => band(v, HEADROOM_CUTS, percent)),
  },
  {
    id: 'candles',
    label: 'Candle pattern',
    question: 'Do the named candlestick formations mark better entries?',
    sortByVolume: true,
    multi: true,
    bucket: (trade) =>
      trade.candle_tags && trade.candle_tags.length > 0
        ? trade.candle_tags.map((tag) => tag.replaceAll('_', ' '))
        : 'no pattern',
  },
  {
    id: 'month',
    label: 'Month',
    question: 'Is the result steady, or one or two months carrying everything?',
    bucket: (trade) => istParts(trade.entry_time).date.slice(0, 7),
  },
];

export interface Bucket {
  key: string;
  stats: Stats;
}

export interface Breakdown {
  dimension: Dimension;
  buckets: Bucket[];
  /** Trades the dimension could not place because the field was never recorded. */
  unrecorded: number;
}

export function breakdown(trades: AnalyticsTrade[], dimension: Dimension, model: CostModel): Breakdown {
  const groups = new Map<string, AnalyticsTrade[]>();
  let unrecorded = 0;
  for (const trade of trades) {
    const key = dimension.bucket(trade);
    if (key === undefined) { unrecorded += 1; continue; }
    for (const one of Array.isArray(key) ? key : [key]) {
      const existing = groups.get(one);
      if (existing) existing.push(trade);
      else groups.set(one, [trade]);
    }
  }

  const buckets = [...groups.entries()].map(([key, group]) => ({ key, stats: summarise(group, model) }));
  if (dimension.order) {
    const rank = new Map(dimension.order.map((key, i) => [key, i]));
    buckets.sort((a, b) => (rank.get(a.key) ?? 999) - (rank.get(b.key) ?? 999));
  } else if (dimension.sortByVolume) {
    buckets.sort((a, b) => b.stats.trades - a.stats.trades || a.key.localeCompare(b.key));
  } else {
    buckets.sort((a, b) => a.key.localeCompare(b.key));
  }
  return { dimension, buckets, unrecorded };
}

// ── filtering ────────────────────────────────────────────────────────────────

/** A bucket the user clicked; the whole dashboard narrows to trades inside it. */
export interface Filter {
  dimensionId: string;
  key: string;
}

export function applyFilters(trades: AnalyticsTrade[], filters: Filter[]): AnalyticsTrade[] {
  if (filters.length === 0) return trades;
  const active = filters
    .map((filter) => ({ filter, dimension: DIMENSIONS.find((d) => d.id === filter.dimensionId) }))
    .filter((pair): pair is { filter: Filter; dimension: Dimension } => pair.dimension !== undefined);
  return trades.filter((trade) =>
    active.every(({ filter, dimension }) => {
      const key = dimension.bucket(trade);
      return Array.isArray(key) ? key.includes(filter.key) : key === filter.key;
    }),
  );
}

// ── date range ───────────────────────────────────────────────────────────────

export type RangePreset = 'all' | '1d' | '3d' | '7d' | '1m' | '3m' | 'custom';

export const RANGE_PRESETS: { id: RangePreset; label: string; hint: string }[] = [
  { id: 'all', label: 'All', hint: 'Every trade in this set.' },
  { id: '1d', label: '1 day', hint: 'The most recent trading day only.' },
  { id: '3d', label: '3 days', hint: 'The last 3 calendar days up to the most recent trading day.' },
  { id: '7d', label: '7 days', hint: 'The last 7 calendar days up to the most recent trading day.' },
  { id: '1m', label: '1 month', hint: 'From one month before the most recent trading day.' },
  { id: '3m', label: '3 months', hint: 'From three months before the most recent trading day.' },
  { id: 'custom', label: 'Custom', hint: 'Pick your own start and end dates.' },
];

/** Inclusive IST date bounds, as `YYYY-MM-DD`. */
export interface DateBounds {
  from: string;
  to: string;
}

const daysInMonth = (year: number, month: number) => new Date(Date.UTC(year, month + 1, 0)).getUTCDate();

/**
 * Move an IST calendar date backwards, clamping the day into the target month.
 *
 * Plain `setUTCMonth` overflows — 31 March minus one month lands on 2 or 3
 * March — which would silently widen a "1 month" window at every month end.
 */
export function shiftDate(date: string, { days = 0, months = 0 }: { days?: number; months?: number }): string {
  const [year = 0, month = 1, day = 1] = date.split('-').map(Number);
  let targetYear = year;
  let targetMonth = month - 1;
  if (months) {
    const absolute = targetYear * 12 + targetMonth - months;
    targetYear = Math.floor(absolute / 12);
    targetMonth = absolute - targetYear * 12;
  }
  const clampedDay = Math.min(day, daysInMonth(targetYear, targetMonth));
  const shifted = new Date(Date.UTC(targetYear, targetMonth, clampedDay - days));
  return shifted.toISOString().slice(0, 10);
}

/** The IST dates of the first and last trade in a list, or null when empty. */
export function tradeDateSpan(trades: AnalyticsTrade[]): DateBounds | null {
  let from: string | undefined;
  let to: string | undefined;
  for (const trade of trades) {
    const date = istParts(trade.entry_time).date;
    if (from === undefined || date < from) from = date;
    if (to === undefined || date > to) to = date;
  }
  return from === undefined || to === undefined ? null : { from, to };
}

/**
 * Turn a preset into concrete dates.
 *
 * Anchored to the most recent trade in the set, never to today's clock: on a
 * 2022 backtest "last 7 days" measured from now would be empty, and on live
 * paper trades a Monday morning would silently drop Friday's session.
 */
export function resolveRange(
  trades: AnalyticsTrade[],
  preset: RangePreset,
  custom?: Partial<DateBounds>,
): DateBounds | null {
  const span = tradeDateSpan(trades);
  if (span === null) return null;
  if (preset === 'all') return span;
  if (preset === 'custom') {
    const from = custom?.from || span.from;
    const to = custom?.to || span.to;
    return from <= to ? { from, to } : { from: to, to: from };
  }
  const to = span.to;
  const from =
    preset === '1d' ? to
      : preset === '3d' ? shiftDate(to, { days: 2 })
        : preset === '7d' ? shiftDate(to, { days: 6 })
          : preset === '1m' ? shiftDate(to, { months: 1 })
            : shiftDate(to, { months: 3 });
  return { from: from < span.from ? span.from : from, to };
}

/** Trades whose entry falls inside the inclusive IST date bounds. */
export function inDateRange(trades: AnalyticsTrade[], bounds: DateBounds | null): AnalyticsTrade[] {
  if (bounds === null) return trades;
  return trades.filter((trade) => {
    const date = istParts(trade.entry_time).date;
    return date >= bounds.from && date <= bounds.to;
  });
}

/** How many distinct trading days a list covers — the honest denominator. */
export function tradingDayCount(trades: AnalyticsTrade[]): number {
  const days = new Set<string>();
  for (const trade of trades) days.add(istParts(trade.entry_time).date);
  return days.size;
}

// ── series ───────────────────────────────────────────────────────────────────

export interface EquityPoint {
  index: number;
  date: string;
  net: number;
  gross: number;
}

/** Running gross and net P&L in trade order — the shape of the account curve. */
export function equityCurve(trades: AnalyticsTrade[], model: CostModel): EquityPoint[] {
  let net = 0;
  let gross = 0;
  return trades.map((trade, index) => {
    net += netUnder(trade, model);
    gross += trade.gross_inr ?? 0;
    return { index: index + 1, date: istParts(trade.entry_time).date, net, gross };
  });
}

export interface DayPoint {
  date: string;
  trades: number;
  net: number;
  gross: number;
}

export function byDay(trades: AnalyticsTrade[], model: CostModel): DayPoint[] {
  const days = new Map<string, DayPoint>();
  for (const trade of trades) {
    const date = istParts(trade.entry_time).date;
    const point = days.get(date) ?? { date, trades: 0, net: 0, gross: 0 };
    point.trades += 1;
    point.net += netUnder(trade, model);
    point.gross += trade.gross_inr ?? 0;
    days.set(date, point);
  }
  return [...days.values()].sort((a, b) => a.date.localeCompare(b.date));
}

/** Histogram of results in R, the unit the strategy actually risks. */
export const R_BINS = [-3, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3] as const;

export function rDistribution(trades: AnalyticsTrade[]): { label: string; count: number; win: boolean }[] {
  const counts: number[] = new Array(R_BINS.length + 1).fill(0);
  let measured = 0;
  for (const trade of trades) {
    const r = rMultiple(trade);
    if (r === undefined) continue;
    measured += 1;
    let index: number = R_BINS.length;
    for (let i = 0; i < R_BINS.length; i += 1) {
      if (r < R_BINS[i]!) { index = i; break; }
    }
    counts[index] = (counts[index] ?? 0) + 1;
  }
  if (measured === 0) return [];
  return counts.map((count: number, i) => ({
    label:
      i === 0 ? `< ${R_BINS[0]}R`
        : i === R_BINS.length ? `${R_BINS[R_BINS.length - 1]}R+`
          : `${R_BINS[i - 1]} to ${R_BINS[i]}R`,
    count,
    win: i > 0 && R_BINS[i - 1]! >= 0,
  }));
}

/**
 * Sample size below which a bucket's ₹/trade is noise rather than a finding.
 *
 * Not a significance test — just the point under which this dashboard refuses
 * to draw the eye to a number, so a two-trade bucket can never read as a rule.
 */
export const MIN_READABLE_TRADES = 30;
