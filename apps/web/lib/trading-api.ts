const BASE = process.env.NEXT_PUBLIC_PORTFOLIO_API_BASE ?? 'http://localhost:3001';

let cachedToken: string | null = null;

async function getToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  const res = await fetch('/api/token');
  if (!res.ok) throw new Error('Not authenticated');
  const data = (await res.json()) as { token: string };
  cachedToken = data.token;
  return cachedToken;
}

async function get<T>(path: string): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${BASE}${path}`, {
    cache: 'no-store',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    cachedToken = null;
    throw new Error('Session expired — please refresh');
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'content-type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    cachedToken = null;
    throw new Error('Session expired — please refresh');
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

// ─── types ────────────────────────────────────────────────────────────────────

export interface NtPosition {
  _id: string;
  symbol: string;
  signal: 'bullish' | 'bearish';
  sector: string;
  confidence: 'high' | 'medium' | 'low';
  entry_price: number;
  qty: number;
  entry_value: number;
  current_price?: number;
  highest_price: number;
  trailing_sl: number;
  target_price: number;
  status: 'open' | 'closed';
  exit_reason?: 'sl_hit' | 'target_hit' | 'day5';
  exit_price?: number;
  gross_pnl?: number;
  net_pnl?: number;
  entry_at: string;
  exit_at?: string;
  signal_id?: string;
  paper?: boolean;
}

export interface NtSignal {
  _id: string;
  sector: string;
  signal: 'bullish' | 'bearish' | 'neutral';
  magnitude: 'major' | 'moderate' | 'minor';
  stocks: string[];
  confidence: 'high' | 'medium' | 'low';
  reasoning: string;
  acted_on: boolean;
  created_at: string;
}

export interface NtNews {
  _id: string;
  headline: string;
  url?: string;
  source: string;
  classified: boolean;
  ingested_at: string;
}

export interface NtCostBreakdown {
  brokerage: number;
  stt: number;
  exchange: number;
  stamp: number;
  gst: number;
  slippage: number;
}

export interface NtStats {
  open_count: number;
  total_invested_inr: number;
  unrealized_pnl: number;
  has_live_prices: boolean;
  closed_count: number;
  win_count: number;
  loss_count: number;
  win_rate_pct: number | null;
  avg_hold_days: number | null;
  by_exit_reason: Record<string, { count: number; total_net_pnl: number }>;
  // P&L breakdown
  total_gross_pnl: number;
  total_realized_net_pnl: number;
  total_costs_inr: number;
  cost_breakdown: NtCostBreakdown | null;
  // Expectancy & efficiency
  avg_win_inr: number | null;
  avg_loss_inr: number | null;
  expectancy_inr: number | null;
  profit_factor: number | null;
  // Equity curve
  equity_curve: { date: string; cumul: number }[];
  max_drawdown_inr: number;
}

// ─── API calls ────────────────────────────────────────────────────────────────

export const fetchPositions = (status?: 'open' | 'closed' | 'all') => {
  const qs = status ? `?status=${status}` : '';
  return get<{ positions: NtPosition[]; count: number }>(`/nt/positions${qs}`);
};

export const fetchSignals = (limit = 50) =>
  get<{ signals: NtSignal[]; count: number }>(`/nt/signals?limit=${limit}`);

export const fetchNews = (limit = 30) =>
  get<{ articles: NtNews[]; count: number }>(`/nt/news?limit=${limit}`);

export const fetchStats = () => get<NtStats>('/nt/stats');

export type StageStatus = 'pending' | 'running' | 'waiting' | 'done' | 'skipped';

export interface PipelineStatus {
  run: { _id: string; triggered_at: string; source: string } | null;
  stages: {
    ingester: { status: StageStatus; count: number };
    classifier: { status: StageStatus; count: number };
    sqs_delay: { status: StageStatus; remain_ms: number };
    trade: { status: StageStatus; count: number };
  } | null;
  is_active: boolean;
  elapsed_ms: number;
}

export const fetchPipelineStatus = () => get<PipelineStatus>('/nt/pipeline/status');

export const triggerPipeline = () =>
  post<{ status: string; function?: string; message?: string }>('/nt/pipeline');

export interface PipelineRun {
  _id: string;
  triggered_at: string;
  completed_at: string | null;
  source: string;
  status: 'running' | 'completed' | 'failed';
  new_articles: number | null;
  signals_created: number | null;
  positions_opened: number | null;
  error: string | null;
}

export const fetchPipelineHistory = (page = 1) =>
  get<{ runs: PipelineRun[]; total: number; page: number; pages: number }>(
    `/nt/pipeline/history?page=${page}`,
  );

export const fetchPipelinePositions = (runId: string) =>
  get<{ positions: NtPosition[]; count: number }>(`/nt/pipeline/${runId}/positions`);
