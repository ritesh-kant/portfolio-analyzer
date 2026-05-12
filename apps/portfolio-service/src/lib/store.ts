/**
 * In-memory portfolio store for local/MVP use.
 * Holds the last imported set of holdings in process memory.
 * Sufficient for single-user local usage without MongoDB.
 */
import type { Holding } from '@portfolio-analyzer/shared-types';
import type { Transaction } from '@portfolio-analyzer/shared-types';

const store: { holdings: Holding[]; transactions: Transaction[] } = {
  holdings: [],
  transactions: [],
};

export function getHoldings(): Holding[] {
  return store.holdings;
}

export function setHoldings(holdings: Holding[]): void {
  store.holdings = holdings;
}

export function getTransactions(): Transaction[] {
  return store.transactions;
}

export function setTransactions(transactions: Transaction[]): void {
  store.transactions = transactions;
}
