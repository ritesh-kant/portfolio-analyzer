import type { MomentumBar } from './momentum-api';
import { fiveMinuteBars } from './momentum-bars';
import type { TradeLevel } from './momentum-session';

type Level = { price: number; kind: string };
const PIVOT_K = 3;
const CLUSTER_TOL_PCT = 0.30;

/** The same confirmed 5-minute swing-pivot and clustering rules as levels.py. */
function pivots(bars: MomentumBar[], side: 'high' | 'low'): Level[] {
  const points: { price: number; volume: number }[] = [];
  for (let i = PIVOT_K; i < bars.length - PIVOT_K; i += 1) {
    const window = bars.slice(i - PIVOT_K, i + PIVOT_K + 1).map((bar) => bar[side]);
    const extreme = side === 'high' ? Math.max(...window) : Math.min(...window);
    if (bars[i]![side] === extreme && window.indexOf(extreme) === PIVOT_K) {
      points.push({ price: extreme, volume: bars[i]!.volume });
    }
  }
  points.sort((a, b) => a.price - b.price);
  const groups: typeof points[] = [];
  for (const point of points) {
    const group = groups[groups.length - 1];
    if (group && Math.abs(point.price - group[0]!.price) / group[0]!.price * 100 <= CLUSTER_TOL_PCT) {
      group.push(point);
    } else {
      groups.push([point]);
    }
  }
  return groups.map((group) => {
    const volume = group.reduce((sum, point) => sum + point.volume, 0);
    return {
      price: volume > 0
        ? group.reduce((sum, point) => sum + point.price * point.volume, 0) / volume
        : group.reduce((sum, point) => sum + point.price, 0) / group.length,
      kind: side === 'high' ? '5m pivot high' : '5m pivot low',
    };
  });
}

/** Chart references at the latest scanner flag, never using candles printed later. */
export function watchlistLevels(bars: MomentumBar[], flagTime: string, market: 'NSE' | 'US'): TradeLevel[] {
  const cutoff = Date.parse(flagTime);
  if (!Number.isFinite(cutoff)) return [];
  const known = bars.filter((bar) => Number.isFinite(Date.parse(bar.time)) && Date.parse(bar.time) <= cutoff)
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  const latest = known[known.length - 1];
  if (!latest || !Number.isFinite(latest.close) || latest.close <= 0) return [];
  const price = latest.close;
  const tf5 = fiveMinuteBars(known);
  const candidates: Level[] = [
    ...pivots(tf5, 'high'),
    ...pivots(tf5, 'low'),
    { price: Math.max(...known.map((bar) => bar.high)), kind: 'session high' },
    { price: Math.min(...known.map((bar) => bar.low)), kind: 'session low' },
  ];
  if (market === 'NSE') {
    // Mirrors indicators.round_levels_above. These are references, not trade gates.
    const minor = price < 100 ? 5 : price < 1000 ? 10 : 50;
    const major = price < 100 ? 10 : price < 1000 ? 50 : 100;
    candidates.push(
      { price: (Math.floor(price / minor) + 1) * minor, kind: 'round rupee' },
      { price: (Math.floor(price / major) + 1) * major, kind: 'round rupee' },
    );
  }
  const resistance = candidates.filter((level) => level.price > price)
    .sort((a, b) => a.price - b.price)[0];
  const support = candidates.filter((level) => level.price < price)
    .sort((a, b) => b.price - a.price)[0];
  return [
    ...(resistance ? [{ label: 'Watch resistance', price: resistance.price, kind: resistance.kind,
      side: 'resistance' as const, faint: true }] : []),
    ...(support ? [{ label: 'Watch support', price: support.price, kind: support.kind,
      side: 'support' as const, faint: true }] : []),
  ];
}
