/**
 * In-memory portfolio store for local/MVP use.
 * Holds the last imported set of holdings in process memory.
 * Sufficient for single-user local usage without MongoDB.
 */
import type { Holding } from '@portfolio-analyzer/shared-types';

const store: { holdings: Holding[] } = { holdings: [] };

export function getHoldings(): Holding[] {
  return store.holdings;
}

export function setHoldings(holdings: Holding[]): void {
  store.holdings = holdings;
}
