/**
 * "Big Trades" bubbles (MotiveWave's name for the study): one circle per large trade, green when
 * the buyer was the aggressor and red when the seller was, sized by quantity.
 *
 * ⚠ SAMPLED, not a full tape. The Upstox V3 feed sends a snapshot about once a second whose `ltq` is
 * only the LAST trade, so trades in between are lost (measured 50–70 % of lots seen on MCX crude and
 * natural gas, 2026-11-05). `coverage` below reports how much of the day volume the ticks account for.
 * The tape is written by `apps/signal-engine/scripts/capture_ticks.py`.
 */

/** One line of the capture file (see capture_ticks.py). */
export interface RawTick {
  /** Last-trade time, ms since epoch. */
  t: number;
  p: number;
  /** Quantity of the last trade. */
  q: number;
  /** Day volume at the snapshot. */
  v: number;
  bp: number | null;
  bq: number | null;
  ap: number | null;
  aq: number | null;
}

export type Side = 'buy' | 'sell';

/** A trade with its aggressor. `method`: the side came from the best bid/ask, or from the tick rule. */
export interface Trade {
  t: number;
  p: number;
  q: number;
  side: Side;
  method: 'quote' | 'tick';
}

/** Trades merged by the aggregate window. */
export interface Bubble {
  /** First trade of the group, ms. */
  t: number;
  p: number;
  q: number;
  side: Side;
  /** Trades merged into this bubble. */
  n: number;
}

export interface BigTradesCfg {
  /** Hide bubbles smaller than this (in the feed's quantity unit: shares, or units for MCX). */
  minQty: number;
  /** Merge trades at the same price and side that occur within this many ms of the group's first. 0 = off. */
  aggMs: number;
}

export const BIG_TRADES_DEFAULTS: BigTradesCfg = { minQty: 500, aggMs: 1000 };

/**
 * Aggressor of each trade. Buy when it printed at or above the best ask, sell at or below the best
 * bid; otherwise the tick rule (up from the previous trade = buy, down = sell, unchanged = same as
 * before). The bid/ask is from the snapshot that carried the trade, i.e. just after it, so a trade
 * that cleared the level often falls through to the tick rule. The first trade with no quote and no
 * history has no side and is dropped.
 */
export function classify(ticks: RawTick[]): Trade[] {
  const sorted = [...ticks].sort((a, b) => a.t - b.t);
  const out: Trade[] = [];
  let prevP: number | null = null;
  let prevSide: Side | null = null;
  for (const k of sorted) {
    if (!(k.q > 0) || !(k.p > 0)) continue;
    let side: Side | null = null;
    let method: Trade['method'] = 'quote';
    if (k.ap != null && k.p >= k.ap) side = 'buy';
    else if (k.bp != null && k.p <= k.bp) side = 'sell';
    else {
      method = 'tick';
      if (prevP != null) side = k.p > prevP ? 'buy' : k.p < prevP ? 'sell' : prevSide;
    }
    prevP = k.p;
    if (side == null) continue;
    prevSide = side;
    out.push({ t: k.t, p: k.p, q: k.q, side, method });
  }
  return out;
}

/** Merge same-price, same-side trades inside the window, then keep bubbles of at least `minQty`. */
export function aggregate(trades: Trade[], cfg: BigTradesCfg): Bubble[] {
  const bubbles: Bubble[] = [];
  const open = new Map<string, Bubble>();
  for (const x of trades) {
    const key = `${x.side}|${x.p}`;
    const b = open.get(key);
    if (cfg.aggMs > 0 && b && x.t - b.t <= cfg.aggMs) {
      b.q += x.q;
      b.n += 1;
    } else {
      const nb: Bubble = { t: x.t, p: x.p, q: x.q, side: x.side, n: 1 };
      open.set(key, nb);
      bubbles.push(nb);
    }
  }
  return bubbles.filter((b) => b.q >= cfg.minQty);
}

/** Share of the volume that traded between the first and last tick that the ticks account for (0–1). */
export function coverage(ticks: RawTick[]): number | null {
  const s = [...ticks].sort((a, b) => a.t - b.t);
  if (s.length < 2) return null;
  const total = s[s.length - 1]!.v - s[0]!.v;
  if (!(total > 0)) return null;
  const seen = s.slice(1).reduce((acc, k) => acc + k.q, 0);
  return Math.min(1, seen / total);
}

/** Parse capture-file text; malformed lines are skipped. */
export function parseTicks(text: string): RawTick[] {
  const out: RawTick[] = [];
  for (const line of text.split('\n')) {
    if (!line) continue;
    try {
      const r = JSON.parse(line) as RawTick;
      if (Number.isFinite(r.t) && Number.isFinite(r.p)) out.push(r);
    } catch {
      /* partial last line while the capture is writing */
    }
  }
  return out;
}
