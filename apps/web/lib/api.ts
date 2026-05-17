import type {
  BenchmarkComparison,
  Holding,
  PortfolioHealthScore,
  TaxDashboardSummary,
} from '@portfolio-analyzer/shared-types';

export const PORTFOLIO_API_BASE =
  process.env.NEXT_PUBLIC_PORTFOLIO_API_BASE ?? 'http://localhost:3001';
const ANALYTICS_API_BASE = process.env.NEXT_PUBLIC_ANALYTICS_API_BASE ?? 'http://localhost:4001';

export interface PortfolioSummaryResponse {
  investedAmount: number;
  currentValue: number;
  pnl: number;
  pnlPct: number;
}

interface HoldingsResponse {
  source: string;
  holdings: Holding[];
  message?: string;
}

export interface SnapshotPoint {
  date: string;
  portfolio: number;
  invested: number;
}

interface SnapshotsResponse {
  source: string;
  storage: 'mongo' | 'memory';
  count: number;
  points: SnapshotPoint[];
}

export async function fetchHoldings(): Promise<HoldingsResponse> {
  return fetchJson<HoldingsResponse>(`${PORTFOLIO_API_BASE}/portfolio/holdings`);
}

export async function fetchSummary(): Promise<PortfolioSummaryResponse> {
  return fetchJson<PortfolioSummaryResponse>(`${PORTFOLIO_API_BASE}/portfolio/summary`);
}

export async function fetchSnapshots(): Promise<SnapshotsResponse> {
  return fetchJson<SnapshotsResponse>(`${PORTFOLIO_API_BASE}/portfolio/snapshots`);
}

export async function fetchBenchmark(): Promise<BenchmarkComparison> {
  return fetchJson<BenchmarkComparison>(`${ANALYTICS_API_BASE}/analytics/benchmark`);
}

export async function fetchScore(
  alphaPct: number,
  holdings: Holding[],
): Promise<PortfolioHealthScore> {
  const params = new URLSearchParams({
    alphaPct: alphaPct.toFixed(4),
    holdings: encodeURIComponent(JSON.stringify(holdings)),
  });
  return fetchJson<PortfolioHealthScore>(`${ANALYTICS_API_BASE}/analytics/score?${params}`);
}

export async function fetchBenchmarkWithContext(
  currentValue: number,
  investedAmount: number,
  inceptionDate: string,
): Promise<
  BenchmarkComparison & { benchmarkScheme?: string; benchmarkAsOf?: string; navLatest?: string; navAtInception?: string }
> {
  const params = new URLSearchParams({
    currentValue: currentValue.toFixed(2),
    investedAmount: investedAmount.toFixed(2),
    inceptionDate,
  });
  return fetchJson(`${ANALYTICS_API_BASE}/analytics/benchmark?${params}`);
}

export async function fetchTaxSummary(): Promise<TaxDashboardSummary> {
  return fetchJson<TaxDashboardSummary>(`${ANALYTICS_API_BASE}/analytics/tax`);
}

let cachedToken: string | null = null;

async function getToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  const res = await fetch('/api/token');
  if (!res.ok) throw new Error('Not authenticated');
  const data = (await res.json()) as { token: string };
  cachedToken = data.token;
  return cachedToken;
}

async function fetchJson<T>(url: string): Promise<T> {
  const token = await getToken();
  const response = await fetch(url, {
    method: 'GET',
    headers: {
      'content-type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    cache: 'no-store',
  });

  if (response.status === 401) {
    cachedToken = null;
    throw new Error('Session expired — please refresh');
  }

  if (!response.ok) {
    throw new Error(`Request failed (${response.status}) for ${url}`);
  }

  return (await response.json()) as T;
}
