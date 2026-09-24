import type { EntryEvidence, MomentumBar, PatternMatch, WatchlistSession } from './momentum-api';

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

/**
 * A US paper trade. Money fields are `_usd`, never `_inr` — the two arms are
 * stored in different collections and must never be summed.
 */
export interface USMomentumTrade {
  _id: string;
  symbol: string;
  strategy?: string;
  status: 'open' | 'closed';
  setup: string;
  trigger_px?: number;
  level?: number | null;
  time: string;
  entry_time: string;
  entry_price: number;
  exit_time?: string;
  exit_price?: number;
  exit_reason?: string;
  stop: number;
  target?: number | null;
  qty: number;
  notional_usd?: number;
  risk_usd?: number;
  gross_usd?: number;
  costs_usd?: number;
  net_usd?: number;
  /** Modelled round-trip cost over the dollars at risk. See us_risk.py — this
   * is what sets the win rate the trade needs to break even. */
  cost_over_risk?: number;
  day_chg_pct?: number;
  rvol?: number;
  float_shares?: number | null;
  catalyst?: number | null;
  exchange?: string;
  /** Which of the five criteria actually ran on this entry. */
  screen_flags?: Record<string, number>;
  screen_complete?: boolean;
  pattern_matches?: PatternMatch[];
  entry_evidence?: EntryEvidence;
  resist_head_pct?: number | null;
  support_drop_pct?: number | null;
  chart?: { interval: '1m'; bars: MomentumBar[] };
}

/** One name's verdict in a screen snapshot, pass or fail. */
export interface USWatchlistName {
  symbol: string;
  passed: boolean;
  reason: string;
  screen_complete: boolean;
  price: number;
  day_chg_pct: number;
  rvol: number | null;
  float_shares: number | null;
  flags: Record<string, number>;
  observed_at?: string;
}

/** One screening session, survivors and rejections together. */
export interface USWatchlistSession {
  _id: string;
  date: string;
  considered: number;
  passed: number;
  /** Passing names that ran all five criteria. Below `passed` means the arm is
   * running a weaker screen than the guide's. */
  complete: number;
  rejected_by: Record<string, number>;
  missing_criteria: string[];
  names: USWatchlistName[];
}

export const fetchUSMomentumTrades = () =>
  get<{ trades: USMomentumTrade[]; count: number }>('/mt/us/trades?limit=500');

export const fetchUSWatchlist = (days = 5) =>
  get<{ sessions: USWatchlistSession[]; count: number }>(`/mt/us/watchlist?days=${days}`);

/** The watchlist day by day, in the same shape as the NSE one (dollars, ET). */
export const fetchUSWatchlistDays = (days = 15) =>
  get<{ sessions: WatchlistSession[]; count: number }>(`/mt/us/watchlist/days?days=${days}`);

/** A watched name's session bars, as the US session saved them. */
export const fetchUSWatchlistBars = (symbol: string, date: string) =>
  get<{ symbol: string; date: string; interval: '1m'; bars: MomentumBar[] }>(
    `/mt/us/watchlist/bars?symbol=${encodeURIComponent(symbol)}&date=${date}`,
  );

/** The guide's screen, in the order the funnel applies it. */
export const US_CRITERIA = [
  { id: 'rvol', n: 1, label: '5× relative volume', side: 'Demand' },
  { id: 'day_chg', n: 2, label: 'Already up 10% on the day', side: 'Demand' },
  { id: 'catalyst', n: 3, label: 'A news event moving it higher', side: 'Demand' },
  { id: 'price', n: 4, label: 'Price between $1.00 and $20.00', side: 'Demand' },
  { id: 'float', n: 5, label: 'Under 10M shares available to trade', side: 'Supply' },
] as const;

/** Maps a criterion to the flag the screener writes when it actually ran. */
export const CRITERION_FLAG: Record<string, string> = {
  rvol: 'rvol_filter_applied',
  catalyst: 'catalyst_filter_applied',
  float: 'float_filter_applied',
};
