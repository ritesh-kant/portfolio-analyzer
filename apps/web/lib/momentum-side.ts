/**
 * Long or short. Every trade stored before 2026-09-26 has no `side` field and
 * was a long, so a missing value reads as long.
 */
export type TradeSide = 'long' | 'short';

export function sideOf(trade: { side?: string | null }): TradeSide {
  return trade.side === 'short' ? 'short' : 'long';
}

/** +1 for a long, -1 for a short: multiplies a price move into "in my favour". */
export function direction(trade: { side?: string | null }): 1 | -1 {
  return sideOf(trade) === 'short' ? -1 : 1;
}

/** The two order verbs, in the order they happen. A short sells first. */
export function orderVerbs(trade: { side?: string | null }): { open: string; close: string } {
  return sideOf(trade) === 'short' ? { open: 'Short', close: 'Cover' } : { open: 'Buy', close: 'Sell' };
}
