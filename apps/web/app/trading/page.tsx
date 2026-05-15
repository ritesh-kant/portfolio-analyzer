'use client';

import { Fragment, useState, useEffect, useCallback, useRef } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { formatCurrency } from '../../lib/format';
import {
  fetchPortfolio,
  fetchPortfolioHistory,
  fetchSignals,
  fetchOrders,
  fetchRuns,
  fetchNews,
  triggerRun,
  type Portfolio,
  type PortfolioSnapshot,
  type Signal,
  type Order,
  type PipelineRun,
  type NewsArticle,
} from '../../lib/trading-api';

// ─── Constants ────────────────────────────────────────────────────────────────

const AGENTS = [
  'news_agent', 'sector_agent', 'stock_selector', 'technical_agent',
  'market_agent', 'guard_agent', 'signal_agent', 'order_agent', 'audit_agent',
] as const;

const AGENT_LABELS: Record<string, string> = {
  news_agent: 'News', sector_agent: 'Sector', stock_selector: 'Stocks',
  technical_agent: 'Tech', market_agent: 'Market', guard_agent: 'Guard',
  signal_agent: 'Signals', order_agent: 'Orders', audit_agent: 'Audit',
};

const POLL_ACTIVE_MS = 30_000;
const POLL_IDLE_MS = 5 * 60 * 1000;

type TabId = 'overview' | 'signals' | 'orders' | 'analytics';
type SigDir = 'all' | 'BUY' | 'SELL';
type OrderTab = 'OPEN' | 'CLOSED' | 'ALL';

// ─── Small components ─────────────────────────────────────────────────────────

function AgentDot({ status }: { status: string }) {
  const base = 'h-2.5 w-2.5 rounded-full flex-shrink-0';
  if (status === 'done') return <span className={`${base} bg-emerald-500`} />;
  if (status === 'running') return <span className={`${base} animate-pulse bg-accent`} />;
  if (status === 'error') return <span className={`${base} bg-rose-500`} />;
  return <span className={`${base} bg-black/15`} />;
}

function Pill({
  status,
}: {
  status: PipelineRun['status'];
}) {
  const map: Record<PipelineRun['status'], string> = {
    completed: 'bg-emerald-100 text-emerald-700',
    running: 'bg-accent/10 text-accent animate-pulse',
    pending: 'bg-amber-100 text-amber-700',
    failed: 'bg-rose-100 text-rose-700',
  };
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${map[status]}`}>
      {status}
    </span>
  );
}

function ConfBar({ value }: { value: number }) {
  const color = value >= 70 ? 'bg-emerald-500' : value >= 50 ? 'bg-amber-400' : 'bg-rose-400';
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-black/10">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs font-semibold tabular-nums">{value}</span>
    </div>
  );
}

function Empty({ msg }: { msg: string }) {
  return (
    <div className="metric-chip flex items-center justify-center py-10 text-sm text-ink/40">
      {msg}
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
  count,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  count?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-semibold transition ${
        active ? 'bg-accent text-white shadow-sm' : 'hover:bg-black/5'
      }`}
    >
      {children}
      {count !== undefined && count > 0 && (
        <span
          className={`rounded-full px-1.5 py-px text-[10px] font-bold ${
            active ? 'bg-white/20 text-white' : 'bg-black/10 text-ink/60'
          }`}
        >
          {count}
        </span>
      )}
    </button>
  );
}

function SubTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg px-3 py-1 text-xs font-semibold transition ${
        active ? 'bg-black/10 text-ink' : 'text-ink/50 hover:text-ink/80'
      }`}
    >
      {children}
    </button>
  );
}

// ─── Analytics tab ────────────────────────────────────────────────────────────

function AnalyticsTab({
  portfolio,
  history,
}: {
  portfolio: Portfolio | null;
  history: { snapshots: PortfolioSnapshot[]; initial_capital: number } | null;
}) {
  if (!portfolio && !history) {
    return <Empty msg="No trade data yet — run the pipeline to generate trades." />;
  }

  const wins = portfolio?.winning_trades ?? 0;
  const losses = (portfolio?.total_trades ?? 0) - wins;
  const winRate = portfolio && portfolio.total_trades > 0
    ? ((wins / portfolio.total_trades) * 100).toFixed(1)
    : null;

  const donutData = [
    { name: 'Wins', value: wins },
    { name: 'Losses', value: Math.max(0, losses) },
  ];

  const snapshots = history?.snapshots ?? [];
  const initialCap = history?.initial_capital ?? portfolio?.initial_capital ?? 100_000;
  const pnlPositive = (portfolio?.total_pnl ?? 0) >= 0;

  // Derive max drawdown from snapshots
  let peak = initialCap;
  let maxDrawdown = 0;
  for (const s of snapshots) {
    if (s.portfolio_value > peak) peak = s.portfolio_value;
    const dd = ((peak - s.portfolio_value) / peak) * 100;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  return (
    <div className="space-y-4">
      {/* Key stats row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          {
            label: 'Total Trades',
            value: String(portfolio?.total_trades ?? 0),
          },
          {
            label: 'Win Rate',
            value: winRate ? `${winRate}%` : '—',
            color: winRate && parseFloat(winRate) >= 50 ? 'text-emerald-700' : 'text-rose-600',
          },
          {
            label: 'Total P&L',
            value: portfolio ? formatCurrency(portfolio.total_pnl) : '—',
            color: pnlPositive ? 'text-emerald-700' : 'text-rose-600',
            prefix: pnlPositive && (portfolio?.total_pnl ?? 0) > 0 ? '+' : '',
          },
          {
            label: 'Max Drawdown',
            value: maxDrawdown > 0 ? `-${maxDrawdown.toFixed(1)}%` : '—',
            color: maxDrawdown > 5 ? 'text-rose-600' : 'text-ink',
          },
        ].map(({ label, value, color, prefix }) => (
          <div key={label} className="metric-chip">
            <p className="text-xs text-ink/50">{label}</p>
            <p className={`mt-0.5 font-display text-xl font-bold ${color ?? ''}`}>
              {prefix}{value}
            </p>
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div className="metric-chip space-y-3">
        <h3 className="text-sm font-semibold">Portfolio Equity Curve</h3>
        {snapshots.length <= 1 ? (
          <p className="py-6 text-center text-sm text-ink/40">
            Close some trades to see the equity curve.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={snapshots} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#0f766e" stopOpacity={0.18} />
                  <stop offset="95%" stopColor="#0f766e" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#17212a12" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: '#17212a80' }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#17212a80' }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `₹${(v / 1000).toFixed(0)}k`}
                domain={['auto', 'auto']}
              />
              <ReferenceLine
                y={initialCap}
                stroke="#17212a30"
                strokeDasharray="4 4"
                label={{ value: 'Initial', fontSize: 9, fill: '#17212a50' }}
              />
              <Tooltip
                formatter={(v: number) => [formatCurrency(v), 'Portfolio Value']}
                contentStyle={{
                  fontSize: 12,
                  borderRadius: 8,
                  border: '1px solid #17212a15',
                  background: '#fffaf2',
                }}
              />
              <Area
                type="monotone"
                dataKey="portfolio_value"
                stroke="#0f766e"
                strokeWidth={2}
                fill="url(#equityGrad)"
                dot={false}
                activeDot={{ r: 4 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Win/Loss donut */}
      {portfolio && portfolio.total_trades > 0 && (
        <div className="metric-chip flex items-center gap-8">
          <div>
            <h3 className="mb-2 text-sm font-semibold">Win / Loss</h3>
            <PieChart width={120} height={120}>
              <Pie
                data={donutData}
                cx={55}
                cy={55}
                innerRadius={34}
                outerRadius={52}
                paddingAngle={2}
                dataKey="value"
              >
                <Cell fill="#10b981" />
                <Cell fill="#f43f5e" />
              </Pie>
              <Tooltip
                formatter={(v: number, name: string) => [v, name]}
                contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #17212a15' }}
              />
            </PieChart>
          </div>
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
              <span className="text-ink/70">Wins</span>
              <span className="ml-auto font-bold">{wins}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-rose-500" />
              <span className="text-ink/70">Losses</span>
              <span className="ml-auto font-bold">{losses}</span>
            </div>
            {winRate && (
              <p className="pt-1 text-xs text-ink/50">
                Win rate:{' '}
                <span className="font-semibold text-ink">{winRate}%</span>
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Signals tab ──────────────────────────────────────────────────────────────

function SignalsTab({ signals }: { signals: Signal[] }) {
  const [dir, setDir] = useState<SigDir>('all');
  const [expanded, setExpanded] = useState<string | null>(null);

  const filtered = dir === 'all' ? signals : signals.filter((s) => s.direction === dir);

  return (
    <div className="space-y-3">
      {/* Direction filter */}
      <div className="flex items-center gap-1">
        {(['all', 'BUY', 'SELL'] as SigDir[]).map((d) => (
          <SubTab key={d} active={dir === d} onClick={() => setDir(d)}>
            {d === 'all' ? 'All' : d}
            {d !== 'all' && (
              <span className="ml-1 text-[10px]">
                ({signals.filter((s) => s.direction === d).length})
              </span>
            )}
          </SubTab>
        ))}
        <span className="ml-auto text-xs text-ink/40">
          {filtered.filter((s) => s.meets_threshold).length} actionable
        </span>
      </div>

      {filtered.length === 0 ? (
        <Empty msg="No signals match the filter." />
      ) : (
        <div className="metric-chip overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-black/5 text-xs text-ink/50">
                <th className="px-4 py-3 text-left font-semibold">Symbol</th>
                <th className="px-4 py-3 text-left font-semibold">Dir</th>
                <th className="px-4 py-3 text-left font-semibold">Confidence</th>
                <th className="px-4 py-3 text-left font-semibold">Signals</th>
                <th className="px-4 py-3 text-right font-semibold">Entry ₹</th>
                <th className="px-4 py-3 text-right font-semibold">Target</th>
                <th className="px-4 py-3 text-left font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => {
                const key = s._id ?? `${s.run_id}-${s.symbol}`;
                const isExpanded = expanded === key;
                return (
                  <Fragment key={key}>
                    <tr
                      className="cursor-pointer border-b border-black/5 last:border-0 hover:bg-black/[0.02]"
                      onClick={() => setExpanded(isExpanded ? null : key)}
                    >
                      <td className="px-4 py-3">
                        <span className="font-display font-bold">
                          {s.symbol.replace('.NS', '')}
                        </span>
                        <span className="ml-1.5 text-xs text-ink/40">{s.date}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                            s.direction === 'BUY'
                              ? 'bg-emerald-100 text-emerald-700'
                              : 'bg-rose-100 text-rose-700'
                          }`}
                        >
                          {s.direction}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <ConfBar value={s.confidence} />
                        {s.llm_bonus > 0 && (
                          <span className="text-[10px] text-accent">+{s.llm_bonus} LLM</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs text-ink/60">
                          {s.triggered_signals.length}/9
                        </span>
                        {s.triggered_signals.slice(0, 2).map((t) => (
                          <span
                            key={t}
                            className="ml-1 rounded bg-black/5 px-1.5 py-0.5 text-[10px] text-ink/60"
                          >
                            {t.split(' ')[0]}
                          </span>
                        ))}
                      </td>
                      <td className="px-4 py-3 text-right font-medium tabular-nums">
                        {s.entry_price > 0
                          ? `₹${s.entry_price.toLocaleString('en-IN')}`
                          : '—'}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-emerald-700">
                        {s.target_pct !== undefined
                          ? `+${s.target_pct.toFixed(1)}%`
                          : '—'}
                      </td>
                      <td className="px-4 py-3">
                        {s.order_placed ? (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-700">
                            Ordered
                          </span>
                        ) : s.meets_threshold ? (
                          <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-semibold text-accent">
                            Actionable
                          </span>
                        ) : (
                          <span className="rounded-full bg-black/5 px-2 py-0.5 text-xs font-semibold text-ink/40">
                            Below threshold
                          </span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${key}-expand`} className="border-b border-black/5 bg-black/[0.015]">
                        <td colSpan={7} className="px-4 pb-3 pt-1">
                          <p className="text-xs font-semibold text-ink/50">LLM Reasoning</p>
                          <p className="mt-1 text-xs leading-relaxed text-ink/70">
                            {s.reasoning || 'No reasoning recorded.'}
                          </p>
                          {(s.rsi !== undefined ||
                            s.macd_hist !== undefined ||
                            s.volume_ratio !== undefined) && (
                            <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-ink/50">
                              {s.rsi !== undefined && (
                                <span>RSI: <b className="text-ink">{s.rsi.toFixed(1)}</b></span>
                              )}
                              {s.macd_hist !== undefined && (
                                <span>
                                  MACD hist:{' '}
                                  <b className={s.macd_hist >= 0 ? 'text-emerald-700' : 'text-rose-600'}>
                                    {s.macd_hist.toFixed(3)}
                                  </b>
                                </span>
                              )}
                              {s.volume_ratio !== undefined && (
                                <span>
                                  Vol ratio: <b className="text-ink">{s.volume_ratio.toFixed(2)}×</b>
                                </span>
                              )}
                              {s.above_ema20 !== undefined && (
                                <span>EMA20: <b>{s.above_ema20 ? '↑ above' : '↓ below'}</b></span>
                              )}
                              {s.above_ema50 !== undefined && (
                                <span>EMA50: <b>{s.above_ema50 ? '↑ above' : '↓ below'}</b></span>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Orders tab ───────────────────────────────────────────────────────────────

function OrdersTab({ orders }: { orders: Order[] }) {
  const [sub, setSub] = useState<OrderTab>('OPEN');

  const filtered =
    sub === 'ALL' ? orders : orders.filter((o) => o.status === sub);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1">
        {(['OPEN', 'CLOSED', 'ALL'] as OrderTab[]).map((s) => (
          <SubTab key={s} active={sub === s} onClick={() => setSub(s)}>
            {s === 'ALL' ? 'All' : s.charAt(0) + s.slice(1).toLowerCase()}
            <span className="ml-1 text-[10px]">
              ({s === 'ALL' ? orders.length : orders.filter((o) => o.status === s).length})
            </span>
          </SubTab>
        ))}
      </div>

      {filtered.length === 0 ? (
        <Empty msg={`No ${sub === 'ALL' ? '' : sub.toLowerCase() + ' '}orders.`} />
      ) : (
        <div className="metric-chip overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-black/5 text-xs text-ink/50">
                <th className="px-4 py-3 text-left font-semibold">Symbol</th>
                <th className="px-4 py-3 text-right font-semibold">Shares</th>
                <th className="px-4 py-3 text-right font-semibold">Entry</th>
                {sub !== 'OPEN' && (
                  <th className="px-4 py-3 text-right font-semibold">Exit</th>
                )}
                {sub === 'OPEN' && (
                  <>
                    <th className="px-4 py-3 text-right font-semibold">Stop</th>
                    <th className="px-4 py-3 text-right font-semibold">Target</th>
                  </>
                )}
                <th className="px-4 py-3 text-right font-semibold">Value</th>
                {sub !== 'OPEN' && (
                  <th className="px-4 py-3 text-right font-semibold">P&amp;L</th>
                )}
                <th className="px-4 py-3 text-right font-semibold">Kelly</th>
                {sub === 'ALL' && (
                  <th className="px-4 py-3 text-left font-semibold">Status</th>
                )}
              </tr>
            </thead>
            <tbody>
              {filtered.map((o) => {
                const key = o._id ?? `${o.run_id}-${o.symbol}`;
                const stopPct =
                  o.stop_loss > 0
                    ? (((o.entry_price - o.stop_loss) / o.entry_price) * 100).toFixed(1)
                    : null;
                const tgtPct =
                  o.target > 0
                    ? (((o.target - o.entry_price) / o.entry_price) * 100).toFixed(1)
                    : null;
                const pnlVal =
                  o.actual_return_pct !== undefined
                    ? o.position_value * (o.actual_return_pct / 100)
                    : null;
                const pnlPos = pnlVal !== null && pnlVal >= 0;

                return (
                  <tr
                    key={key}
                    className="border-b border-black/5 last:border-0 hover:bg-black/[0.02]"
                  >
                    <td className="px-4 py-3">
                      <span className="font-display font-bold">
                        {o.symbol.replace('.NS', '')}
                      </span>
                      <span className="ml-1.5 text-xs text-ink/40">{o.date}</span>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">{o.shares}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      ₹{o.entry_price.toLocaleString('en-IN')}
                    </td>
                    {sub !== 'OPEN' && (
                      <td className="px-4 py-3 text-right tabular-nums">
                        {o.exit_price
                          ? `₹${o.exit_price.toLocaleString('en-IN')}`
                          : '—'}
                      </td>
                    )}
                    {sub === 'OPEN' && (
                      <>
                        <td className="px-4 py-3 text-right tabular-nums text-rose-600">
                          ₹{o.stop_loss.toLocaleString('en-IN')}
                          {stopPct && (
                            <span className="ml-0.5 text-[10px] text-ink/40">−{stopPct}%</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-emerald-700">
                          ₹{o.target.toLocaleString('en-IN')}
                          {tgtPct && (
                            <span className="ml-0.5 text-[10px] text-ink/40">+{tgtPct}%</span>
                          )}
                        </td>
                      </>
                    )}
                    <td className="px-4 py-3 text-right font-medium tabular-nums">
                      {formatCurrency(o.position_value)}
                    </td>
                    {sub !== 'OPEN' && (
                      <td className="px-4 py-3 text-right tabular-nums font-medium">
                        {pnlVal !== null ? (
                          <span className={pnlPos ? 'text-emerald-700' : 'text-rose-600'}>
                            {pnlPos ? '+' : ''}
                            {formatCurrency(pnlVal)}
                            <span className="ml-0.5 text-[10px]">
                              ({pnlPos ? '+' : ''}
                              {o.actual_return_pct?.toFixed(1)}%)
                            </span>
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    )}
                    <td className="px-4 py-3 text-right tabular-nums text-ink/60">
                      {(o.kelly_fraction * 100).toFixed(1)}%
                    </td>
                    {sub === 'ALL' && (
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                            o.status === 'OPEN'
                              ? 'bg-accent/10 text-accent'
                              : o.status === 'CLOSED'
                              ? 'bg-emerald-100 text-emerald-700'
                              : 'bg-rose-100 text-rose-700'
                          }`}
                        >
                          {o.status}
                        </span>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Overview tab ─────────────────────────────────────────────────────────────

function OverviewTab({
  portfolio,
  runs,
  news,
}: {
  portfolio: Portfolio | null;
  runs: PipelineRun[];
  news: NewsArticle[];
}) {
  const latestRun = runs[0];
  const pnlPositive = (portfolio?.total_pnl ?? 0) >= 0;

  return (
    <div className="space-y-5">
      {/* Portfolio summary */}
      {portfolio ? (
        <div className="metric-chip grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {[
            { label: 'Total Value', value: formatCurrency(portfolio.total_value) },
            { label: 'Cash', value: formatCurrency(portfolio.cash) },
            { label: 'Invested', value: formatCurrency(portfolio.invested) },
            {
              label: 'Total P&L',
              value: `${pnlPositive && portfolio.total_pnl > 0 ? '+' : ''}${formatCurrency(portfolio.total_pnl)}`,
              sub: `${pnlPositive && portfolio.total_pnl_pct > 0 ? '+' : ''}${portfolio.total_pnl_pct.toFixed(2)}%`,
              color: pnlPositive ? 'text-emerald-700' : 'text-rose-600',
            },
            {
              label: 'Trades / Open',
              value: String(portfolio.total_trades),
              sub: `${portfolio.open_positions} open`,
            },
          ].map(({ label, value, sub, color }) => (
            <div key={label}>
              <p className="text-xs text-ink/50">{label}</p>
              <p className={`mt-0.5 font-display text-lg font-bold leading-tight ${color ?? ''}`}>
                {value}
              </p>
              {sub && <p className="text-xs text-ink/50">{sub}</p>}
            </div>
          ))}
        </div>
      ) : (
        <Empty msg="Portfolio not initialised — trigger a pipeline run to start." />
      )}

      {/* Latest run */}
      <div className="metric-chip space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Latest Pipeline Run</h2>
          {latestRun && (
            <span className="text-xs text-ink/40">
              {new Date(latestRun.started_at).toLocaleString('en-IN', {
                day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
              })}
            </span>
          )}
        </div>

        {latestRun ? (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <Pill status={latestRun.status} />
              <span className="text-xs text-ink/50">{latestRun.ai_provider}</span>
              <span className="text-xs text-ink/50">{latestRun.date}</span>
              {latestRun.stats && (
                <span className="text-xs text-ink/50">
                  {latestRun.stats.signals_count} signals · {latestRun.stats.orders_count} orders
                </span>
              )}
            </div>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-9">
              {AGENTS.map((agent) => {
                const status = latestRun.agent_statuses[agent] ?? 'pending';
                const ms = latestRun.agent_timings[agent];
                return (
                  <div
                    key={agent}
                    className="flex flex-col items-center gap-1.5 rounded-xl border border-black/5 bg-bg px-2 py-3 text-center"
                    title={`${agent}: ${status}${ms ? ` (${(ms / 1000).toFixed(1)}s)` : ''}`}
                  >
                    <AgentDot status={status} />
                    <span className="text-[10px] font-semibold text-ink/70">
                      {AGENT_LABELS[agent]}
                    </span>
                    <span className="text-[9px] text-ink/40 tabular-nums">
                      {status === 'done' && ms
                        ? `${(ms / 1000).toFixed(1)}s`
                        : status}
                    </span>
                  </div>
                );
              })}
            </div>
            {latestRun.error_summary && (
              <p className="rounded-lg bg-rose-50 p-2 text-xs text-rose-700">
                {latestRun.error_summary}
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-ink/40">No pipeline runs yet.</p>
        )}
      </div>

      {/* Run history */}
      {runs.length > 1 && (
        <div className="metric-chip space-y-2">
          <h2 className="mb-1 text-sm font-semibold">Run History</h2>
          {runs.slice(1).map((run) => (
            <div key={run.run_id} className="flex items-center justify-between text-sm">
              <span className="font-medium">{run.date}</span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-ink/40">{run.ai_provider}</span>
                {run.stats && (
                  <span className="text-xs text-ink/40">
                    {run.stats.signals_count} sig · {run.stats.orders_count} ord
                  </span>
                )}
                <Pill status={run.status} />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Recent news */}
      {news.length > 0 && (
        <div className="metric-chip space-y-3">
          <h2 className="text-sm font-semibold">Recent News</h2>
          {news.map((article) => (
            <div
              key={article._id ?? article.url}
              className="border-b border-black/5 pb-3 last:border-0 last:pb-0"
            >
              <div className="flex items-start justify-between gap-2">
                <a
                  href={article.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-medium hover:text-accent"
                >
                  {article.headline}
                </a>
                <div className="flex shrink-0 items-center gap-1">
                  {article.tier && (
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[9px] font-bold ${
                        article.tier === 1
                          ? 'bg-accent/10 text-accent'
                          : article.tier === 2
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-black/5 text-ink/50'
                      }`}
                    >
                      T{article.tier}
                    </span>
                  )}
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      article.sentiment === 'positive'
                        ? 'bg-emerald-100 text-emerald-700'
                        : article.sentiment === 'negative'
                        ? 'bg-rose-100 text-rose-700'
                        : 'bg-black/5 text-ink/50'
                    }`}
                  >
                    {article.sentiment}
                  </span>
                </div>
              </div>
              <p className="mt-0.5 text-xs text-ink/50">
                {article.source}
                {article.affected_sectors.length > 0 && (
                  <> · {article.affected_sectors.slice(0, 2).join(', ')}</>
                )}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function TradingPage() {
  const [tab, setTab] = useState<TabId>('overview');

  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [news, setNews] = useState<NewsArticle[]>([]);
  const [history, setHistory] = useState<{
    snapshots: PortfolioSnapshot[];
    initial_capital: number;
  } | null>(null);

  const [triggering, setTriggering] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const analyticsLoadedRef = useRef(false);

  const loadAll = useCallback(async () => {
    const [pRes, sRes, oRes, rRes, nRes] = await Promise.allSettled([
      fetchPortfolio(),
      fetchSignals({ limit: 50 }),
      fetchOrders(),
      fetchRuns(10),
      fetchNews({ limit: 10 }),
    ]);
    if (pRes.status === 'fulfilled') setPortfolio(pRes.value);
    if (sRes.status === 'fulfilled') setSignals(sRes.value.signals);
    if (oRes.status === 'fulfilled') setOrders(oRes.value.orders);
    if (rRes.status === 'fulfilled') setRuns(rRes.value.runs);
    if (nRes.status === 'fulfilled') setNews(nRes.value.articles);
  }, []);

  const loadHistory = useCallback(async () => {
    if (analyticsLoadedRef.current) return;
    analyticsLoadedRef.current = true;
    try {
      const res = await fetchPortfolioHistory();
      setHistory(res);
    } catch {}
  }, []);

  // Polling: 30s when active, 5min when idle — always running
  useEffect(() => {
    const latestRun = runs[0];
    const isActive =
      latestRun?.status === 'running' || latestRun?.status === 'pending';
    const ms = isActive ? POLL_ACTIVE_MS : POLL_IDLE_MS;

    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => void loadAll(), ms);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [runs, loadAll]);

  // Load analytics lazily on tab switch
  useEffect(() => {
    if (tab === 'analytics') void loadHistory();
  }, [tab, loadHistory]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  async function handleTrigger() {
    setTriggering(true);
    setTriggerError(null);
    try {
      await triggerRun();
      await loadAll();
    } catch (err) {
      setTriggerError(
        err instanceof Error ? err.message : 'Failed to trigger pipeline',
      );
    } finally {
      setTriggering(false);
    }
  }

  const latestRun = runs[0];
  const isActive =
    latestRun?.status === 'running' || latestRun?.status === 'pending';
  const openOrders = orders.filter((o) => o.status === 'OPEN');

  return (
    <div className="space-y-4">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight">
            Trading Dashboard
          </h1>
          <p className="text-sm text-ink/60">
            Paper trading · NSE/BSE · Half-Kelly position sizing
          </p>
        </div>
        <button
          onClick={() => void handleTrigger()}
          disabled={triggering || isActive}
          className="rounded-lg bg-accent px-5 py-2 text-sm font-semibold text-white transition hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {triggering ? 'Triggering…' : isActive ? 'Running…' : 'Run Pipeline'}
        </button>
      </div>

      {triggerError && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {triggerError}
        </div>
      )}

      {/* ── Tab bar ──────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-1 rounded-full bg-panel p-1 shadow-card w-fit">
        <TabBtn active={tab === 'overview'} onClick={() => setTab('overview')}>
          Overview
        </TabBtn>
        <TabBtn
          active={tab === 'signals'}
          onClick={() => setTab('signals')}
          count={signals.length}
        >
          Signals
        </TabBtn>
        <TabBtn
          active={tab === 'orders'}
          onClick={() => setTab('orders')}
          count={openOrders.length}
        >
          Orders
        </TabBtn>
        <TabBtn active={tab === 'analytics'} onClick={() => setTab('analytics')}>
          Analytics
        </TabBtn>
      </div>

      {/* ── Tab content ─────────────────────────────────────────────────── */}
      {tab === 'overview' && (
        <OverviewTab portfolio={portfolio} runs={runs} news={news} />
      )}
      {tab === 'signals' && <SignalsTab signals={signals} />}
      {tab === 'orders' && <OrdersTab orders={orders} />}
      {tab === 'analytics' && (
        <AnalyticsTab portfolio={portfolio} history={history} />
      )}
    </div>
  );
}
