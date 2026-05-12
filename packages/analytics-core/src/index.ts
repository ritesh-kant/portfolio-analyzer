import type {
  BenchmarkComparison,
  CashFlowPoint,
  Holding,
  PortfolioHealthScore,
} from '@portfolio-analyzer/shared-types';

export function xirr(cashFlows: CashFlowPoint[]): number {
  if (cashFlows.length < 2) {
    return 0;
  }

  const flows = [...cashFlows].sort(
    (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime(),
  );
  const startFlow = flows[0];
  if (!startFlow) {
    return 0;
  }
  const startDate = new Date(startFlow.date);

  let low = -0.9999;
  let high = 10;

  for (let i = 0; i < 200; i += 1) {
    const mid = (low + high) / 2;
    const npv = netPresentValue(flows, startDate, mid);

    if (Math.abs(npv) < 1e-7) {
      return mid;
    }

    if (npv > 0) {
      low = mid;
    } else {
      high = mid;
    }
  }

  return (low + high) / 2;
}

export function calculateBenchmarkComparison(
  portfolioCurrentValue: number,
  investedAmount: number,
  benchmarkStartNav: number,
  benchmarkEndNav: number,
): BenchmarkComparison {
  const portfolioReturnPct = investedAmount === 0 ? 0 : (portfolioCurrentValue - investedAmount) / investedAmount;
  const benchmarkReturnPct = benchmarkStartNav === 0 ? 0 : (benchmarkEndNav - benchmarkStartNav) / benchmarkStartNav;
  const benchmarkEquivalentValue = investedAmount * (1 + benchmarkReturnPct);

  return {
    portfolioReturnPct: portfolioReturnPct * 100,
    benchmarkReturnPct: benchmarkReturnPct * 100,
    alphaPct: (portfolioReturnPct - benchmarkReturnPct) * 100,
    portfolioCurrentValue,
    benchmarkEquivalentValue,
  };
}

export function calculateHealthScore(
  holdings: Holding[],
  alphaPct: number,
): PortfolioHealthScore {
  const totalValue = holdings.reduce((sum, h) => sum + h.currentValue, 0);
  const maxSingleWeight = holdings.reduce((max, h) => {
    const weight = totalValue === 0 ? 0 : h.currentValue / totalValue;
    return Math.max(max, weight);
  }, 0);

  const diversification = Math.max(0, 35 - maxSingleWeight * 50);
  const benchmarkPerformance = Math.max(0, Math.min(35, 17.5 + alphaPct));
  const riskConcentration = Math.max(0, 30 - maxSingleWeight * 40);

  const score = Math.round(diversification + benchmarkPerformance + riskConcentration);

  return {
    score,
    breakdown: {
      diversification: round2(diversification),
      benchmarkPerformance: round2(benchmarkPerformance),
      riskConcentration: round2(riskConcentration),
    },
  };
}

function netPresentValue(cashFlows: CashFlowPoint[], startDate: Date, rate: number): number {
  return cashFlows.reduce((sum, cf) => {
    const years = (new Date(cf.date).getTime() - startDate.getTime()) / (1000 * 60 * 60 * 24 * 365);
    return sum + cf.amount / (1 + rate) ** years;
  }, 0);
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
