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

// ─── Types ────────────────────────────────────────────────────────────────────

export interface OptPosition {
  _id: string;
  signal_id: string;
  symbol: string;
  status: 'open' | 'closed';
  strategy: 'straddle_sell';
  signal_type: 'bullish' | 'bearish';
  strike: number;
  expiry: string;
  lot_size: number;
  lots: number;
  entry_at: string;
  entry_spot: number;
  entry_ce_prem: number;
  entry_pe_prem: number;
  entry_total_prem: number;
  entry_iv: number;
  entry_exposure_inr: number;
  current_spot?: number;
  current_ce_prem?: number;
  current_pe_prem?: number;
  current_total_prem?: number;
  current_pnl?: number;
  last_updated_at?: string;
  exit_at?: string;
  exit_spot?: number;
  exit_total_prem?: number;
  exit_reason?: 'target_hit' | 'stop_hit' | 'time_stop' | 'eod_close';
  gross_pnl?: number;
  net_pnl?: number;
  costs?: { total: number; stt: number; slippage: number };
  target_pct: number;
  stop_pct: number;
  max_hold_minutes_used: number;
}

export interface OptStats {
  strategy: string;
  open_count: number;
  unrealized_pnl: number;
  open_premium_received: number;
  open_current_value: number;
  closed_count: number;
  win_count: number;
  loss_count: number;
  win_rate_pct: number | null;
  total_premium_received: number;
  total_gross_pnl: number;
  total_net_pnl: number;
  total_costs: number;
  avg_win_inr: number | null;
  avg_loss_inr: number | null;
  expectancy_inr: number | null;
  avg_hold_min: number | null;
  by_exit_reason: Record<string, { count: number; total_net_pnl: number }>;
  equity_curve: { date: string; cumul: number }[];
  max_drawdown_inr: number;
  chain_snapshots_7d: number;
  iv_source_breakdown: Record<string, number>;
}

export interface ChainSnapshot {
  _id: string;
  symbol: string;
  signal_id: string;
  signal_created_at?: string;
  signal_type?: 'bullish' | 'bearish';
  snapshot_at: string;
  spot_price: number;
  atm_strike: number;
  expiry: string;
  bs_ce_prem: number;
  bs_pe_prem: number;
  bs_straddle_mid: number;
  bs_iv_used: number;
  iv_source: 'nse' | 'synthetic';
  // NSE real data (present when iv_source === 'nse')
  ce_bid?: number;
  ce_ask?: number;
  ce_iv?: number;
  ce_oi?: number;
  pe_bid?: number;
  pe_ask?: number;
  pe_iv?: number;
  pe_oi?: number;
  nse_straddle_mid?: number;
}

// ─── API calls ────────────────────────────────────────────────────────────────

export function fetchOptPositions(status: 'open' | 'closed' | 'all' = 'all') {
  return get<{ positions: OptPosition[]; count: number }>(
    `/opt/positions?status=${status}`,
  );
}

export function fetchOptStats(days?: number) {
  const q = days ? `?days=${days}` : '';
  return get<OptStats>(`/opt/stats${q}`);
}

export function fetchChainSnapshots(opts?: { symbol?: string; signal_id?: string; hours?: number }) {
  const params = new URLSearchParams();
  if (opts?.symbol) params.set('symbol', opts.symbol);
  if (opts?.signal_id) params.set('signal_id', opts.signal_id);
  if (opts?.hours) params.set('hours', String(opts.hours));
  const q = params.toString() ? `?${params}` : '';
  return get<{ snapshots: ChainSnapshot[]; count: number; hours: number }>(
    `/opt/chain-snapshots${q}`,
  );
}
