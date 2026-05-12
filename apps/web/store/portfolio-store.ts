'use client';

import { create } from 'zustand';

import type { BenchmarkComparison, PortfolioHealthScore } from '@portfolio-analyzer/shared-types';

import {
  fetchBenchmarkWithContext,
  fetchHoldings,
  fetchScore,
  fetchSnapshots,
  fetchSummary,
  type SnapshotPoint,
  type PortfolioSummaryResponse,
} from '@/lib/api';
import { mockGrowth, mockHoldings } from '@/lib/mock-data';

// Inception date used for benchmark comparison (earliest purchase approximation)
const INCEPTION_DATE = '2024-01-01';

interface PortfolioStore {
  holdings: typeof mockHoldings;
  growthSeries: typeof mockGrowth;
  summary: PortfolioSummaryResponse;
  benchmark: BenchmarkComparison & { benchmarkScheme?: string; benchmarkAsOf?: string };
  score: PortfolioHealthScore;
  loading: boolean;
  error: string | null;
  source: 'live' | 'fallback';
  loadDashboardData: () => Promise<void>;
}

const fallbackSummary: PortfolioSummaryResponse = {
  investedAmount: mockHoldings.reduce((sum, item) => sum + item.investedAmount, 0),
  currentValue: mockHoldings.reduce((sum, item) => sum + item.currentValue, 0),
  pnl: mockHoldings.reduce((sum, item) => sum + item.currentValue - item.investedAmount, 0),
  pnlPct: 13.64,
};

const fallbackBenchmark: BenchmarkComparison = {
  portfolioReturnPct: 13.64,
  benchmarkReturnPct: 16.9,
  alphaPct: -3.26,
  portfolioCurrentValue: fallbackSummary.currentValue,
  benchmarkEquivalentValue: 164000,
};

const fallbackScore: PortfolioHealthScore = {
  score: 72,
  breakdown: {
    diversification: 24,
    benchmarkPerformance: 22,
    riskConcentration: 26,
  },
};

export const usePortfolioStore = create<PortfolioStore>((set, get) => ({
  holdings: mockHoldings,
  growthSeries: mockGrowth,
  summary: fallbackSummary,
  benchmark: fallbackBenchmark,
  score: fallbackScore,
  loading: false,
  error: null,
  source: 'fallback',
  loadDashboardData: async () => {
    set({ loading: true, error: null });

    try {
      const [holdingsResponse, summary] = await Promise.all([fetchHoldings(), fetchSummary()]);
      const snapshotsResponse = await fetchSnapshots().catch(() => ({
        source: 'empty',
        storage: 'memory' as const,
        count: 0,
        points: [] as SnapshotPoint[],
      }));

      const liveHoldings =
        holdingsResponse.holdings.length > 0 ? holdingsResponse.holdings : mockHoldings;

      // Benchmark and score both depend on summary + holdings, fetch after
      const [benchmark, score] = await Promise.all([
        fetchBenchmarkWithContext(summary.currentValue, summary.investedAmount, INCEPTION_DATE),
        fetchScore(
          // Use computed alpha until benchmark returns; initial optimistic estimate
          fallbackBenchmark.alphaPct,
          liveHoldings,
        ),
      ]);

      // Refetch score with real alpha
      const finalScore = await fetchScore(benchmark.alphaPct, liveHoldings).catch(() => score);

      const growthSeries = buildGrowthSeries(
        snapshotsResponse.points,
        benchmark.benchmarkReturnPct,
        summary.currentValue,
      );

      set({
        holdings: liveHoldings,
        growthSeries,
        summary,
        benchmark,
        score: finalScore,
        source: 'live',
        loading: false,
      });
    } catch (error) {
      set({
        source: 'fallback',
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load live dashboard data',
      });
    }
  },
}));

function buildGrowthSeries(
  points: SnapshotPoint[],
  benchmarkReturnPct: number,
  currentValue: number,
) {
  if (points.length === 0) {
    return mockGrowth;
  }

  const first = points[0];
  if (!first || first.portfolio === 0) {
    return mockGrowth;
  }

  const totalGrowthRatio = currentValue / first.portfolio;
  const benchmarkGrowthRatio = 1 + benchmarkReturnPct / 100;

  return points.map((p, idx) => {
    const month = new Date(p.date).toLocaleString('en-IN', { month: 'short' });
    const progress = points.length <= 1 ? 1 : idx / (points.length - 1);

    return {
      month,
      portfolio: Math.round(p.portfolio),
      benchmark: Math.round(first.portfolio * (1 + (benchmarkGrowthRatio - 1) * progress)),
      _ratio: totalGrowthRatio,
    };
  });
}
