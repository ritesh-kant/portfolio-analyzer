// ─── Shared primitives ────────────────────────────────────────────────────────

export type AgentStatus = 'pending' | 'running' | 'done' | 'error';
export type PipelineStatus = 'pending' | 'running' | 'completed' | 'failed';
export type OrderStatus = 'OPEN' | 'CLOSED' | 'STOPPED';
export type OrderMode = 'paper' | 'live';
export type LogLevel = 'info' | 'warn' | 'error';

// ─── news_articles (trading_news_articles) ────────────────────────────────────

export interface NewsArticleDoc {
  run_id: string;
  hash: string;
  headline: string;
  url: string;
  source: string;
  published: string;
  summary?: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  affected_stocks: string[];
  affected_sectors: string[];
  is_duplicate: boolean;
  is_market_relevant: boolean;
  createdAt: Date;
  updatedAt: Date;
}

// ─── stock_analyses (trading_stock_analyses) ─────────────────────────────────

export interface StockAnalysisDoc {
  run_id: string;
  symbol: string;
  confidence: number;
  direction: 'BUY' | 'SELL';
  triggered_signals: string[];
  reasoning: string;
  technical_data: Record<string, unknown>;
  market_data: Record<string, unknown>;
  createdAt: Date;
  updatedAt: Date;
}

// ─── trading_signals ─────────────────────────────────────────────────────────

export interface TradingSignalDoc {
  run_id: string;
  date: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  confidence: number;
  base_score: number;
  llm_bonus: number;
  triggered_signals: string[];
  reasoning: string;
  entry_price: number;
  rsi?: number;
  macd_hist?: number;
  above_ema20?: boolean;
  above_ema50?: boolean;
  volume_ratio?: number;
  meets_threshold: boolean;
  order_placed: boolean;
  prompt_version?: string; // semver string — set by signal_agent, enables A/B comparison
  weak_signals?: string[]; // near-miss indicators that almost triggered
  actual_return_pct?: number; // filled by monitor_agent when position closes
  was_correct?: boolean; // true if exit_price > entry_price
  outcome_date?: string; // ISO date when position was closed
  createdAt: Date;
  updatedAt: Date;
}

// ─── paper_orders (trading_paper_orders) ─────────────────────────────────────

export interface PaperOrderDoc {
  run_id: string;
  symbol: string;
  direction: 'BUY' | 'SELL';
  mode: OrderMode;
  entry_price: number;
  shares: number;
  position_value: number;
  confidence: number;
  kelly_fraction: number;
  stop_loss: number;
  target: number;
  date: string;
  status: OrderStatus;
  reasoning: string;
  exit_price?: number;
  exit_date?: string;
  actual_return_pct?: number;
  was_correct?: boolean;
  createdAt: Date;
  updatedAt: Date;
}

// ─── virtual_portfolio (trading_virtual_portfolio) ────────────────────────────

export interface VirtualPortfolioDoc {
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
  updatedAt: Date;
}

// ─── pipeline_runs (trading_pipeline_runs) ────────────────────────────────────

export interface PipelineRunStats {
  news_count: number;
  signals_count: number;
  orders_count: number;
  errors_count: number;
  blocked_count: number;
}

export interface PipelineRunDoc {
  run_id: string;
  date: string;
  status: PipelineStatus;
  ai_provider: string;
  started_at: Date;
  completed_at?: Date;
  duration_ms?: number;
  agent_statuses: Record<string, AgentStatus>;
  agent_timings: Record<string, number>;
  stats?: PipelineRunStats;
  error_summary?: string;
  createdAt: Date;
  updatedAt: Date;
}

// ─── agent_logs (trading_agent_logs) ─────────────────────────────────────────

export interface AgentLogDoc {
  run_id: string;
  agent: string;
  level: LogLevel;
  message: string;
  duration_ms?: number;
  context?: Record<string, unknown>;
  createdAt: Date;
  // TTL index on createdAt — documents expire after 30 days
}

// ─── agent_decisions (trading_agent_decisions) ────────────────────────────────

export interface AgentDecisionDoc {
  run_id: string;
  agent: string;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  input_summary: string;
  output_summary: string;
  reasoning?: string;
  createdAt: Date;
}

// ─── API response shapes (used by trading-service routes) ────────────────────

export interface DashboardResponse {
  latest_run?: PipelineRunDoc;
  signals: TradingSignalDoc[];
  open_orders: PaperOrderDoc[];
  portfolio: VirtualPortfolioDoc | null;
  recent_news: NewsArticleDoc[];
}
