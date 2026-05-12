import type { Holding, TaxDashboardSummary } from '@portfolio-analyzer/shared-types';

export const mockHoldings: Holding[] = [
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

export const mockGrowth = [
  { month: 'Jan', portfolio: 115000, benchmark: 112000 },
  { month: 'Feb', portfolio: 119500, benchmark: 115000 },
  { month: 'Mar', portfolio: 123200, benchmark: 118900 },
  { month: 'Apr', portfolio: 128900, benchmark: 124300 },
  { month: 'May', portfolio: 130480, benchmark: 126850 },
];

export const mockTaxSummary: TaxDashboardSummary = {
  realized: {
    ltcg: 96500,
    stcg: 21240,
  },
  unrealized: {
    profits: 38210,
    losses: 7250,
  },
  ltcgExemptionUsed: 116328,
  ltcgExemptionLimit: 125000,
  ltcgExemptionRemaining: 8672,
  harvestable: [
    {
      symbol: 'AXISBANK',
      potentialLoss: 3420,
      quantity: 12,
    },
    {
      symbol: 'LT',
      potentialLoss: 2140,
      quantity: 4,
    },
    {
      symbol: 'SBIN',
      potentialLoss: 1690,
      quantity: 9,
    },
  ],
};
