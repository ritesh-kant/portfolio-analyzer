import type { Transaction } from '@portfolio-analyzer/shared-types';
import { calculateTaxSummary } from '@portfolio-analyzer/tax-core';

import { json } from '../lib/http.js';

const sampleTransactions: Transaction[] = [
  {
    symbol: 'INFY',
    quantity: 10,
    price: 1200,
    side: 'buy',
    executedAt: '2024-01-11T10:00:00.000Z',
    broker: 'zerodha',
  },
  {
    symbol: 'INFY',
    quantity: 6,
    price: 1460,
    side: 'sell',
    executedAt: '2025-06-19T10:00:00.000Z',
    broker: 'zerodha',
  },
];

export async function handler() {
  const result = calculateTaxSummary(sampleTransactions);

  return json(200, result);
}
