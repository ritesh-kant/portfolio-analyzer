const BASE = process.env.NEXT_PUBLIC_TRADING_API_BASE ?? 'http://localhost:6002';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

// ─── types ────────────────────────────────────────────────────────────────────

export interface Portfolio {
  portfolio_id: 'main';
  cash: number;
  invested: number;
  total_value: number;
  initial_capital: number;
  open_positions: number;
  total_trades: number;
  winning_trades: number;
  total_pnl: number;
  total_pnl_pct: number;
  updatedAt: string;
}

export interface Signal {
  _id: string;
  run_id: string;
  date: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  confidence: number;
  base_score: number;
  llm_bonus: number;
  triggered_signals: string[];
  weak_signals?: string[];
  reasoning: string;
  entry_price: number;
  target_pct?: number;
  stop_pct?: number;
  r_r_ratio?: number;
  holding_days?: number;
  signal_scores?: Record<string, number>;
  rsi?: number;
  macd_hist?: number;
  above_ema20?: boolean;
  above_ema50?: boolean;
  volume_ratio?: number;
  meets_threshold: boolean;
  order_placed: boolean;
  createdAt: string;
}

export interface Order {
  _id: string;
  run_id: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  mode: 'paper' | 'live';
  entry_price: number;
  shares: number;
  position_value: number;
  confidence: number;
  kelly_fraction: number;
  stop_loss: number;
  target: number;
  date: string;
  status: 'OPEN' | 'CLOSED' | 'STOPPED';
  reasoning: string;
  exit_price?: number;
  exit_date?: string;
  actual_return_pct?: number;
  createdAt: string;
}

export interface PipelineRun {
  run_id: string;
  date: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  ai_provider: string;
  started_at: string;
  completed_at?: string;
  agent_statuses: Record<string, 'pending' | 'running' | 'done' | 'error'>;
  agent_timings: Record<string, number>;
  stats?: { news_count: number; signals_count: number; orders_count: number; errors_count: number };
  error_summary?: string;
}

export interface NewsArticle {
  _id: string;
  run_id: string;
  headline: string;
  url: string;
  source: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  tier?: 1 | 2 | 3;
  affected_sectors: string[];
  affected_stocks: string[];
  summary?: string;
  createdAt: string;
}

export interface PortfolioSnapshot {
  date: string;
  daily_pnl: number;
  cumulative_pnl: number;
  portfolio_value: number;
  trades: number;
  wins: number;
}

export interface DashboardData {
  latest_run?: PipelineRun;
  signals: Signal[];
  open_orders: Order[];
  portfolio: Portfolio | null;
  recent_news: NewsArticle[];
}

// ─── API calls ────────────────────────────────────────────────────────────────

export const fetchDashboard = () => get<DashboardData>('/trading/dashboard');

export const fetchPortfolio = () => get<Portfolio | null>('/trading/portfolio');

export const fetchPortfolioHistory = () =>
  get<{ snapshots: PortfolioSnapshot[]; initial_capital: number }>('/trading/portfolio/history');

export const fetchSignals = (params?: { run_id?: string; limit?: number }) => {
  const qs = new URLSearchParams();
  if (params?.run_id) qs.set('run_id', params.run_id);
  if (params?.limit) qs.set('limit', String(params.limit));
  return get<{ signals: Signal[]; count: number }>(`/trading/signals?${qs}`);
};

export const fetchOrders = (status?: 'OPEN' | 'CLOSED' | 'STOPPED') => {
  const qs = status ? `?status=${status}` : '';
  return get<{ orders: Order[]; count: number }>(`/trading/orders${qs}`);
};

export const fetchRuns = (limit = 5) =>
  get<{ runs: PipelineRun[]; count: number }>(`/trading/runs?limit=${limit}`);

export const fetchNews = (params?: { run_id?: string; limit?: number }) => {
  const qs = new URLSearchParams();
  if (params?.run_id) qs.set('run_id', params.run_id);
  if (params?.limit) qs.set('limit', String(params.limit));
  return get<{ articles: NewsArticle[]; count: number }>(`/trading/news?${qs}`);
};

export const triggerRun = (opts?: { date?: string; ai_provider?: string }) =>
  post<{ run_id: string; date: string; ai_provider: string; status: string }>(
    '/trading/runs/trigger',
    opts ?? {},
  );
