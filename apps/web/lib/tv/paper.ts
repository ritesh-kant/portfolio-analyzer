import type { Bar } from './types';

/**
 * Paper trading, TradingView style: a simulated account that trades the chart's own candles.
 *
 * One NET position per symbol (buying while short reduces / flips it). Market orders fill at the last close;
 * limit / stop orders and a position's stop-loss / take-profit fill when a candle's range reaches them.
 * Only candles that start at or after an order's `since` may fill it with their full range, so an order never
 * fills on history that happened before it was placed; the newest candle is checked at its close only.
 * If one candle reaches both the stop and the target, the stop wins (the pessimistic reading).
 * Pure functions — the same code drives live charts and replay.
 */

export type Side = 'buy' | 'sell';
export type OrderType = 'market' | 'limit' | 'stop';
export type Tif = 'day' | 'week' | 'gtc';
export const TIF_SECS: Record<Tif, number> = { day: 86400, week: 604800, gtc: 0 };
export type CloseReason = 'manual' | 'sl' | 'tp' | 'flip';

/** Gross exposure allowed as a multiple of equity. */
export const MAX_LEVERAGE = 5;

export interface Order {
  id: number;
  symbol: string;
  side: Side;
  type: 'limit' | 'stop';
  qty: number;
  price: number;
  sl?: number;
  tp?: number;
  /** Unix s: candles starting at or after this may fill the order with their full range. */
  since: number;
  tif: Tif;
  /** Unix s: the order is cancelled once a candle starts at or after this (absent = good till cancelled). */
  expires?: number;
}

/** One line of the Orders tab history: how an order ended. */
export interface OrderLog {
  id: number;
  symbol: string;
  side: Side;
  type: OrderType;
  qty: number;
  /** Limit / stop price, or the fill price for a market order. */
  price: number;
  status: 'filled' | 'cancelled' | 'rejected' | 'expired';
  time: number;
}

export interface Position {
  symbol: string;
  /** Signed: > 0 long, < 0 short. */
  qty: number;
  avg: number;
  sl?: number;
  tp?: number;
  /** Entry fees still attributed to the open quantity. */
  fees: number;
  openedAt: number;
  since: number;
}

export interface Trade {
  id: number;
  symbol: string;
  dir: 'long' | 'short';
  qty: number;
  entry: number;
  exit: number;
  /** Net of both sides' commission. */
  pnl: number;
  fees: number;
  openedAt: number;
  closedAt: number;
  reason: CloseReason;
}

export interface PaperState {
  initial: number;
  cash: number;
  /** Percent of notional charged per fill. */
  commissionPct: number;
  positions: Record<string, Position>;
  orders: Order[];
  history: Trade[];
  log: OrderLog[];
  /** Last price seen per symbol, for marking positions that are not on screen. */
  prices: Record<string, number>;
  nextId: number;
}

export const DEFAULT_CAPITAL = 1_000;
/** The default before it was lowered to 1,000: an untouched saved account at this size is migrated. */
export const OLD_DEFAULT_CAPITAL = 1_000_000;

export function newAccount(initial = DEFAULT_CAPITAL, commissionPct = 0): PaperState {
  return {
    initial,
    cash: initial,
    commissionPct,
    positions: {},
    orders: [],
    history: [],
    log: [],
    prices: {},
    nextId: 1,
  };
}

function logOrder(s: PaperState, o: Omit<OrderLog, 'id'> & { id?: number }): void {
  s.log = [{ ...o, id: o.id ?? s.nextId++ }, ...(s.log ?? [])].slice(0, 300);
}

const clone = (s: PaperState): PaperState => JSON.parse(JSON.stringify(s)) as PaperState;

export const unrealized = (p: Position, price: number): number => p.qty * (price - p.avg);

export function equityOf(s: PaperState): number {
  let e = s.cash;
  for (const p of Object.values(s.positions)) e += unrealized(p, s.prices[p.symbol] ?? p.avg);
  return e;
}

export const realizedOf = (s: PaperState): number => s.history.reduce((a, t) => a + t.pnl, 0);

function exposureOf(s: PaperState): number {
  let g = 0;
  for (const p of Object.values(s.positions)) g += Math.abs(p.qty) * (s.prices[p.symbol] ?? p.avg);
  return g;
}

/** Validates SL / TP against the side they protect. Returns an error message or null. */
export function checkBracket(side: Side, ref: number, sl?: number, tp?: number): string | null {
  if (sl != null && (side === 'buy' ? sl >= ref : sl <= ref))
    return `Stop loss must be ${side === 'buy' ? 'below' : 'above'} ${ref}`;
  if (tp != null && (side === 'buy' ? tp <= ref : tp >= ref))
    return `Take profit must be ${side === 'buy' ? 'above' : 'below'} ${ref}`;
  return null;
}

/** Applies a fill to `s` in place: opens / adds / reduces / flips the net position and books the trade. */
function fill(
  s: PaperState,
  symbol: string,
  side: Side,
  qty: number,
  price: number,
  time: number,
  reason: CloseReason,
  bracket?: { sl?: number; tp?: number },
): boolean {
  const signed = side === 'buy' ? qty : -qty;
  const fee = (q: number) => (Math.abs(q) * price * s.commissionPct) / 100;
  const pos = s.positions[symbol];
  s.prices[symbol] = price;
  if (!pos) {
    const f = fee(qty);
    s.cash -= f;
    s.positions[symbol] = {
      symbol,
      qty: signed,
      avg: price,
      sl: bracket?.sl,
      tp: bracket?.tp,
      fees: f,
      openedAt: time,
      since: time,
    };
    return true;
  }
  if (Math.sign(pos.qty) === Math.sign(signed)) {
    const f = fee(qty);
    s.cash -= f;
    const total = Math.abs(pos.qty) + qty;
    pos.avg = (pos.avg * Math.abs(pos.qty) + price * qty) / total;
    pos.qty += signed;
    pos.fees += f;
    if (bracket?.sl != null) pos.sl = bracket.sl;
    if (bracket?.tp != null) pos.tp = bracket.tp;
    return true;
  }
  // reducing or flipping
  const closeQty = Math.min(Math.abs(pos.qty), qty);
  const dir = pos.qty > 0 ? 1 : -1;
  const gross = closeQty * (price - pos.avg) * dir;
  const exitFee = fee(closeQty);
  const entryShare = (pos.fees * closeQty) / Math.abs(pos.qty);
  s.cash += gross - exitFee;
  s.history.unshift({
    id: s.nextId++,
    symbol,
    dir: dir > 0 ? 'long' : 'short',
    qty: closeQty,
    entry: pos.avg,
    exit: price,
    pnl: gross - exitFee - entryShare,
    fees: exitFee + entryShare,
    openedAt: pos.openedAt,
    closedAt: time,
    reason: qty > closeQty ? 'flip' : reason,
  });
  const left = qty - closeQty;
  if (left > 0) {
    // the old position is gone; the remainder opens the opposite way, without the old brackets
    delete s.positions[symbol];
    const f = fee(left);
    s.cash -= f;
    s.positions[symbol] = {
      symbol,
      qty: dir > 0 ? -left : left,
      avg: price,
      sl: bracket?.sl,
      tp: bracket?.tp,
      fees: f,
      openedAt: time,
      since: time,
    };
  } else if (closeQty === Math.abs(pos.qty)) {
    delete s.positions[symbol];
  } else {
    pos.qty -= dir * closeQty;
    pos.fees -= entryShare;
  }
  return true;
}

export interface Result {
  state: PaperState;
  error?: string;
}

const fail = (state: PaperState, error: string): Result => ({ state, error });

function marginOk(s: PaperState): boolean {
  return exposureOf(s) <= Math.max(0, equityOf(s)) * MAX_LEVERAGE + 1e-9;
}

export function placeMarket(
  state: PaperState,
  symbol: string,
  side: Side,
  qty: number,
  price: number,
  time: number,
  bracket?: { sl?: number; tp?: number },
): Result {
  if (!(qty > 0)) return fail(state, 'Quantity must be positive');
  if (!(price > 0)) return fail(state, 'No price yet');
  const bad = checkBracket(side, price, bracket?.sl, bracket?.tp);
  if (bad) return fail(state, bad);
  const s = clone(state);
  fill(s, symbol, side, qty, price, time, 'manual', bracket);
  if (!marginOk(s)) return fail(state, `Exceeds ${MAX_LEVERAGE}× buying power`);
  logOrder(s, { symbol, side, type: 'market', qty, price, status: 'filled', time });
  return { state: s };
}

export function placePending(
  state: PaperState,
  symbol: string,
  side: Side,
  type: 'limit' | 'stop',
  qty: number,
  price: number,
  last: number,
  since: number,
  bracket?: { sl?: number; tp?: number },
  tif: Tif = 'gtc',
): Result {
  if (!(qty > 0)) return fail(state, 'Quantity must be positive');
  if (!(price > 0)) return fail(state, 'Enter a price');
  // a limit buys below / sells above the market, a stop the other way round
  const below = (side === 'buy') === (type === 'limit');
  if (below ? price >= last : price <= last)
    return fail(
      state,
      `${side === 'buy' ? 'Buy' : 'Sell'} ${type} must be ${below ? 'below' : 'above'} ${last}`,
    );
  const bad = checkBracket(side, price, bracket?.sl, bracket?.tp);
  if (bad) return fail(state, bad);
  const s = clone(state);
  s.orders.push({
    id: s.nextId++,
    symbol,
    side,
    type,
    qty,
    price,
    sl: bracket?.sl,
    tp: bracket?.tp,
    since,
    tif,
    expires: tif === 'gtc' ? undefined : since + TIF_SECS[tif],
  });
  return { state: s };
}

export function cancelOrder(state: PaperState, id: number, time = 0): PaperState {
  const o = state.orders.find((x) => x.id === id);
  if (!o) return state;
  const s = clone(state);
  s.orders = s.orders.filter((x) => x.id !== id);
  logOrder(s, {
    id: o.id,
    symbol: o.symbol,
    side: o.side,
    type: o.type,
    qty: o.qty,
    price: o.price,
    status: 'cancelled',
    time,
  });
  return s;
}

export function closePosition(
  state: PaperState,
  symbol: string,
  price: number,
  time: number,
): PaperState {
  const pos = state.positions[symbol];
  if (!pos || !(price > 0)) return state;
  const s = clone(state);
  fill(s, symbol, pos.qty > 0 ? 'sell' : 'buy', Math.abs(pos.qty), price, time, 'manual');
  return s;
}

/** Closes the position and opens the same size the other way, at `price`. */
export function reversePosition(
  state: PaperState,
  symbol: string,
  price: number,
  time: number,
): Result {
  const pos = state.positions[symbol];
  if (!pos) return fail(state, 'No open position');
  return placeMarket(
    state,
    symbol,
    pos.qty > 0 ? 'sell' : 'buy',
    Math.abs(pos.qty) * 2,
    price,
    time,
  );
}

/** Drags a working order to a new price (same side rules as placing it). */
export function moveOrder(state: PaperState, id: number, price: number, last: number): Result {
  const o = state.orders.find((x) => x.id === id);
  if (!o) return fail(state, 'Order is gone');
  const below = (o.side === 'buy') === (o.type === 'limit');
  if (below ? price >= last : price <= last)
    return fail(
      state,
      `${o.side === 'buy' ? 'Buy' : 'Sell'} ${o.type} must be ${below ? 'below' : 'above'} ${last}`,
    );
  return {
    state: { ...state, orders: state.orders.map((x) => (x.id === id ? { ...x, price } : x)) },
  };
}

/** Edits a working order: its type, quantity, or its own stop / target (null clears). */
export function updateOrder(
  state: PaperState,
  id: number,
  patch: {
    type?: 'limit' | 'stop';
    qty?: number;
    price?: number;
    tif?: Tif;
    sl?: number | null;
    tp?: number | null;
  },
  last: number,
): Result {
  const o = state.orders.find((x) => x.id === id);
  if (!o) return fail(state, 'Order is gone');
  const next = { ...o };
  if (patch.type) next.type = patch.type;
  if (patch.price != null) {
    if (!(patch.price > 0)) return fail(state, 'Enter a price');
    next.price = patch.price;
  }
  if (patch.tif) {
    next.tif = patch.tif;
    next.expires = patch.tif === 'gtc' ? undefined : next.since + TIF_SECS[patch.tif];
  }
  if (patch.qty != null) {
    if (!(patch.qty > 0)) return fail(state, 'Quantity must be positive');
    next.qty = patch.qty;
  }
  if (patch.sl !== undefined) next.sl = patch.sl ?? undefined;
  if (patch.tp !== undefined) next.tp = patch.tp ?? undefined;
  const below = (next.side === 'buy') === (next.type === 'limit');
  if (below ? next.price >= last : next.price <= last)
    return fail(
      state,
      `${next.side === 'buy' ? 'Buy' : 'Sell'} ${next.type} must be ${below ? 'below' : 'above'} ${last}`,
    );
  const bad = checkBracket(next.side, next.price, next.sl, next.tp);
  if (bad) return fail(state, bad);
  return { state: { ...state, orders: state.orders.map((x) => (x.id === id ? next : x)) } };
}

/** Sets or clears (undefined) a position's stop / target. */
export function setBracket(
  state: PaperState,
  symbol: string,
  sl: number | undefined,
  tp: number | undefined,
): Result {
  const pos = state.positions[symbol];
  if (!pos) return fail(state, 'No open position');
  const bad = checkBracket(pos.qty > 0 ? 'buy' : 'sell', pos.avg, sl, tp);
  if (bad) return fail(state, bad);
  return { state: { ...state, positions: { ...state.positions, [symbol]: { ...pos, sl, tp } } } };
}

export function updateSettings(
  state: PaperState,
  initial: number,
  commissionPct: number,
): PaperState {
  const delta = initial - state.initial;
  return { ...state, initial, cash: state.cash + delta, commissionPct };
}

/**
 * Advances the account over `bars` (the chart's candles for `symbol`, oldest first): fills pending orders and
 * triggers stops / targets. Returns the SAME object when nothing changed so callers can skip a re-render.
 */
export function step(
  state: PaperState,
  symbol: string,
  bars: Bar[],
  candleSecs: number,
): PaperState {
  const last = bars[bars.length - 1];
  if (!last) return state;
  const orders = state.orders.filter((o) => o.symbol === symbol);
  const pos = state.positions[symbol];
  if (!orders.length && !pos) {
    if (state.prices[symbol] === last.close) return state;
    return { ...state, prices: { ...state.prices, [symbol]: last.close } };
  }
  const s = clone(state);
  const minSince = Math.min(...orders.map((o) => o.since), pos ? pos.since : Infinity);
  let from = bars.length - 1;
  while (from > 0 && bars[from - 1]!.time >= minSince) from--;
  void candleSecs;
  let changed = false;
  for (let i = from; i < bars.length; i++) {
    const b = bars[i]!;
    const isLast = i === bars.length - 1;
    const range = (since: number) =>
      b.time >= since ? b : isLast ? { ...b, open: b.close, high: b.close, low: b.close } : null;
    // day / week orders that ran out of time
    for (const o of s.orders.filter(
      (x) => x.symbol === symbol && x.expires != null && b.time >= x.expires,
    )) {
      s.orders = s.orders.filter((x) => x.id !== o.id);
      logOrder(s, {
        id: o.id,
        symbol,
        side: o.side,
        type: o.type,
        qty: o.qty,
        price: o.price,
        status: 'expired',
        time: b.time,
      });
      changed = true;
    }
    // pending orders, oldest first
    for (const o of [...s.orders].filter((x) => x.symbol === symbol).sort((a, c) => a.id - c.id)) {
      const r = range(o.since);
      if (!r) continue;
      let px: number | null = null;
      if (o.side === 'buy') {
        if (o.type === 'limit' && r.low <= o.price) px = Math.min(o.price, r.open);
        if (o.type === 'stop' && r.high >= o.price) px = Math.max(o.price, r.open);
      } else {
        if (o.type === 'limit' && r.high >= o.price) px = Math.max(o.price, r.open);
        if (o.type === 'stop' && r.low <= o.price) px = Math.min(o.price, r.open);
      }
      if (px == null) continue;
      s.orders = s.orders.filter((x) => x.id !== o.id);
      const before = clone(s);
      fill(s, symbol, o.side, o.qty, px, b.time, 'manual', { sl: o.sl, tp: o.tp });
      if (!marginOk(s)) {
        Object.assign(s, before);
        logOrder(s, {
          id: o.id,
          symbol,
          side: o.side,
          type: o.type,
          qty: o.qty,
          price: o.price,
          status: 'rejected',
          time: b.time,
        });
      } else {
        logOrder(s, {
          id: o.id,
          symbol,
          side: o.side,
          type: o.type,
          qty: o.qty,
          price: px,
          status: 'filled',
          time: b.time,
        });
        const p = s.positions[symbol];
        if (p) p.since = Math.max(p.since, b.time + candleSecs);
      }
      changed = true;
    }
    // the position's stop / target
    const p = s.positions[symbol];
    if (p && (p.sl != null || p.tp != null)) {
      const r = range(p.since);
      if (r) {
        const long = p.qty > 0;
        let hit: { px: number; why: CloseReason } | null = null;
        if (p.sl != null && (long ? r.low <= p.sl : r.high >= p.sl))
          hit = { px: long ? Math.min(p.sl, r.open) : Math.max(p.sl, r.open), why: 'sl' };
        else if (p.tp != null && (long ? r.high >= p.tp : r.low <= p.tp))
          hit = { px: long ? Math.max(p.tp, r.open) : Math.min(p.tp, r.open), why: 'tp' };
        if (hit) {
          fill(s, symbol, long ? 'sell' : 'buy', Math.abs(p.qty), hit.px, b.time, hit.why);
          changed = true;
        }
      }
    }
  }
  s.prices[symbol] = last.close;
  return changed || state.prices[symbol] !== last.close ? s : state;
}

/** Trade statistics for the History tab footer. */
export function tradeStats(history: Trade[]) {
  const wins = history.filter((t) => t.pnl > 0);
  const gross = history.reduce((a, t) => a + t.pnl, 0);
  return {
    count: history.length,
    winRate: history.length ? (wins.length / history.length) * 100 : 0,
    net: gross,
    avgWin: wins.length ? wins.reduce((a, t) => a + t.pnl, 0) / wins.length : 0,
    avgLoss:
      history.length - wins.length
        ? history.filter((t) => t.pnl <= 0).reduce((a, t) => a + t.pnl, 0) /
          (history.length - wins.length)
        : 0,
  };
}
