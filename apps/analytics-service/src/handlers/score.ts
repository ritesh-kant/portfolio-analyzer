import type { Holding } from '@portfolio-analyzer/shared-types';
import { calculateHealthScore } from '@portfolio-analyzer/analytics-core';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';

export const handler = requireAuth(async (event) => {
  const qs = event.queryStringParameters ?? {};
  const alphaPct = qs['alphaPct'] ? parseFloat(qs['alphaPct']) : 2.2;

  // Accept holdings as a JSON-encoded query param for stateless score computation.
  // Falls back to a minimal default set so the endpoint always returns a score.
  let holdings: Holding[];
  try {
    holdings = qs['holdings']
      ? (JSON.parse(decodeURIComponent(qs['holdings'])) as Holding[])
      : defaultHoldings();
  } catch {
    holdings = defaultHoldings();
  }

  const result = calculateHealthScore(holdings, alphaPct);
  return json(200, result);
});

function defaultHoldings(): Holding[] {
  return [
    {
      symbol: 'HDFCBANK',
      quantity: 18,
      averagePrice: 1410,
      currentPrice: 1665,
      investedAmount: 25380,
      currentValue: 29970,
      assetType: 'stock',
      broker: 'zerodha',
      sector: 'Financials',
      asOf: new Date().toISOString(),
    },
    {
      symbol: 'INFY',
      quantity: 22,
      averagePrice: 1312,
      currentPrice: 1470,
      investedAmount: 28864,
      currentValue: 32340,
      assetType: 'stock',
      broker: 'zerodha',
      sector: 'Technology',
      asOf: new Date().toISOString(),
    },
    {
      symbol: 'UTI_NIFTY50',
      quantity: 410,
      averagePrice: 158,
      currentPrice: 176,
      investedAmount: 64780,
      currentValue: 72160,
      assetType: 'mf',
      broker: 'groww',
      sector: 'Index',
      asOf: new Date().toISOString(),
    },
    {
      symbol: 'RELIANCE',
      quantity: 9,
      averagePrice: 2465,
      currentPrice: 2890,
      investedAmount: 22185,
      currentValue: 26010,
      assetType: 'stock',
      broker: 'groww',
      sector: 'Energy',
      asOf: new Date().toISOString(),
    },
  ];
}
