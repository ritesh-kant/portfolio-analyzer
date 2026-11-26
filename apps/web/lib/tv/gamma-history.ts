import type { GexLevels, GexResponse } from './gex.ts';

/**
 * Recorded gamma snapshots (phase 5 of research/specs/ar-model-chart-strategy-plan.md): the course's gamma regime and
 * levels, as they were known at each candle. No free source keeps history of the levels, so they are recorded going
 * forward by `pnpm bt:gex` (research/backtests/gex_snapshot.mjs), one JSON line per snapshot, in
 * research/backtests/gamma_snapshots/<SYMBOL>.jsonl. Pure helpers: the file reading is in the recorder and the route.
 *
 * NQ's levels come from QQQ's option chain (Yahoo has no options on futures), stored in QQQ prices with the
 * NQ ÷ QQQ ratio at the time (`scale`), the way the chart's Gamma Levels indicator draws them. NIFTY futures work
 * the same way: NIFTY index options from Upstox, stored in index prices with futures ÷ index (`proxy: 'NIFTY'`).
 *
 * His rule (course notes §1e): price above the gamma flip ("HVL") = positive gamma = compression, mean reversion,
 * take profit quickly; below = negative gamma = expansion, hold winners toward the targets.
 */

export interface GammaSet {
  /** Gamma flip, in the proxy's prices; null when the total never changes sign within ±20 % (±8 % before 2026-11-21). */
  flip: number | null;
  callWalls: number[];
  putWalls: number[];
  gammaWall: number | null;
  /** $ per 1 % move, summed over every contract in the set. */
  netGex: number;
}

export interface GammaSnap {
  /** When the option chain was read, unix seconds. A candle may use it only if it closes at or after this. */
  asOf: number;
  symbol: string;
  /** Whose options (QQQ for NQ); absent = the symbol's own. */
  proxy?: string;
  /** The proxy's price then (put–call parity), and chart price ÷ proxy price (1 when there is no proxy). */
  spot: number;
  scale: number;
  convention: string;
  days: number;
  expiries: string[];
  /** Every expiry within `days`, and the nearest expiry only. */
  all: GammaSet;
  nearest: GammaSet | null;
}

/** The levels a snapshot draws, in the chart's prices. */
export interface GammaLevel {
  name: string;
  price: number;
}

export type GammaRegime = 'positive' | 'negative';

/** Snapshots older than this (4 days: a Friday read still covers Monday) are not used. */
export const GAMMA_MAX_AGE = 4 * 86_400;

const set = (l: GexLevels | undefined): GammaSet | null =>
  l
    ? {
        flip: l.flip,
        callWalls: l.callWalls.slice(0, 3),
        putWalls: l.putWalls.slice(0, 3),
        gammaWall: l.gammaWall ?? null,
        netGex: l.netGex,
      }
    : null;

/**
 * A `/api/chart/gex` (`usLevels` / `nseLevels`) response → one snapshot line; null when it has no levels.
 * `chartSymbol` names the chart the line is for when the response names the underlying instead (NSE: NIFTY for
 * NIFTY1!.NFO); a futures chart then stores the index as the proxy and futures ÷ index as the scale.
 */
export function snapFromResponse(res: GexResponse, days: number, chartSymbol?: string): GammaSnap | null {
  const all = set(res.all);
  if (!res.supported || !all || !res.asOf || !res.spot || !res.symbol) return null;
  const proxy = res.proxy ?? (chartSymbol && chartSymbol !== res.symbol && res.scale ? res.symbol : undefined);
  // an NSE futures chart needs futures ÷ index; without it the levels would sit a whole basis away
  if (!proxy && /(\.NFO|1!)$/i.test(chartSymbol ?? '')) return null;
  return {
    asOf: res.asOf,
    symbol: chartSymbol ?? res.symbol,
    ...(proxy ? { proxy } : {}),
    spot: res.spot,
    scale: proxy ? (res.scale ?? NaN) : 1,
    convention: res.convention ?? 'dealer_long_calls',
    days,
    expiries: res.expiries ?? [],
    all,
    nearest: set(res.nearest),
  };
}

/**
 * The snapshot file for a chart symbol: `NQ=F` → `NQ_F.jsonl`, `NIFTY1!.NFO` → `NIFTY1_.NFO.jsonl`. Null for
 * anything that is not a plain ticker (no paths).
 */
export function snapshotFileName(symbol: string): string | null {
  if (!/^[A-Z0-9^.=!-]{1,24}$/i.test(symbol) || symbol.includes('..')) return null;
  return `${symbol.toUpperCase().replace(/[=!]/g, '_')}.jsonl`;
}

/** JSON lines → snapshots sorted by time (bad lines and proxies without a scale are dropped). */
export function parseSnapshots(text: string): GammaSnap[] {
  const out: GammaSnap[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      const s = JSON.parse(line) as GammaSnap;
      if (Number.isFinite(s.asOf) && Number.isFinite(s.scale) && s.scale > 0 && s.all) out.push(s);
    } catch {
      /* a half-written line */
    }
  }
  return out.sort((a, b) => a.asOf - b.asOf);
}

/** The newest snapshot read at or before `t` (unix seconds), if it is at most `maxAge` old. */
export function snapAt(snaps: GammaSnap[], t: number, maxAge = GAMMA_MAX_AGE): GammaSnap | null {
  let lo = 0;
  let hi = snaps.length - 1;
  let best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (snaps[mid]!.asOf <= t) {
      best = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (best < 0) return null;
  const s = snaps[best]!;
  return t - s.asOf <= maxAge ? s : null;
}

/** Flip, walls and the gamma wall in chart prices (the 'all' set: every expiry in the window). */
export function gammaLevels(s: GammaSnap): GammaLevel[] {
  const k = s.scale;
  const out: GammaLevel[] = [];
  if (s.all.flip != null) out.push({ name: 'gamma flip', price: s.all.flip * k });
  s.all.callWalls.forEach((p, i) => out.push({ name: `C${i + 1}`, price: p * k }));
  s.all.putWalls.forEach((p, i) => out.push({ name: `P${i + 1}`, price: p * k }));
  if (s.all.gammaWall != null) out.push({ name: 'gamma wall', price: s.all.gammaWall * k });
  return out;
}

/**
 * The regime at a price: above the flip = positive, below = negative. With no flip in range, the sign of the net
 * GEX decides (`basis: 'net'`).
 */
export function gammaRegime(s: GammaSnap, price: number): { regime: GammaRegime; basis: 'flip' | 'net'; flip: number | null } {
  if (s.all.flip != null) {
    const flip = s.all.flip * s.scale;
    return { regime: price >= flip ? 'positive' : 'negative', basis: 'flip', flip };
  }
  return { regime: s.all.netGex >= 0 ? 'positive' : 'negative', basis: 'net', flip: null };
}

/**
 * The first gamma level a trade meets on its way to the target: strictly between entry and target, nearest the
 * entry. Null when none lies in between.
 */
export function firstLevelBefore(s: GammaSnap, dir: 'long' | 'short', entry: number, target: number): GammaLevel | null {
  const long = dir === 'long';
  let best: GammaLevel | null = null;
  for (const l of gammaLevels(s)) {
    const between = long ? l.price > entry && l.price < target : l.price < entry && l.price > target;
    if (!between) continue;
    if (!best || (long ? l.price < best.price : l.price > best.price)) best = l;
  }
  return best;
}
