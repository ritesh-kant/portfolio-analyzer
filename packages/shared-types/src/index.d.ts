export type AssetType = 'stock' | 'mf' | 'etf';
export type BrokerName = 'zerodha' | 'groww';
export interface Holding {
    symbol: string;
    quantity: number;
    averagePrice: number;
    currentPrice: number;
    investedAmount: number;
    currentValue: number;
    sector?: string;
    assetType: AssetType;
    broker: BrokerName;
    asOf: string;
}
export interface Transaction {
    symbol: string;
    quantity: number;
    price: number;
    side: 'buy' | 'sell';
    executedAt: string;
    broker: BrokerName;
}
export interface CashFlowPoint {
    date: string;
    amount: number;
}
export interface BenchmarkPoint {
    date: string;
    nav: number;
}
export interface BenchmarkComparison {
    portfolioReturnPct: number;
    benchmarkReturnPct: number;
    alphaPct: number;
    portfolioCurrentValue: number;
    benchmarkEquivalentValue: number;
}
export interface TaxRealizedSummary {
    ltcg: number;
    stcg: number;
}
export interface TaxUnrealizedSummary {
    profits: number;
    losses: number;
}
export interface HarvestOpportunity {
    symbol: string;
    potentialLoss: number;
    quantity: number;
}
export interface TaxDashboardSummary {
    realized: TaxRealizedSummary;
    unrealized: TaxUnrealizedSummary;
    ltcgExemptionUsed: number;
    ltcgExemptionLimit: number;
    ltcgExemptionRemaining: number;
    harvestable: HarvestOpportunity[];
}
export interface ScoreBreakdown {
    diversification: number;
    benchmarkPerformance: number;
    riskConcentration: number;
}
export interface PortfolioHealthScore {
    score: number;
    breakdown: ScoreBreakdown;
}
//# sourceMappingURL=index.d.ts.map