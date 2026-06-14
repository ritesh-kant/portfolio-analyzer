'use client';

import { useCallback, useEffect, useState } from 'react';
import { useMarketRefresh } from '../../lib/use-market-refresh';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts';
import {
  fetchOptPositions,
  fetchOptStats,
  fetchChainSnapshots,
  type OptPosition,
  type OptStats,
  type ChainSnapshot,
} from '../../lib/options-api';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function fmtPrice(n: number) {
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function pnlColor(v: number) {
  return v > 0 ? 'text-emerald-700' : v < 0 ? 'text-rose-600' : 'text-ink/60';
}

function sign(v: number) {
  return v > 0 ? '+' : '';
}

function pct(current: number, entry: number) {
  return entry === 0 ? 0 : ((entry - current) / entry) * 100;
}

// ─── Small atoms ─────────────────────────────────────────────────────────────

function Empty({ msg }: { msg: string }) {
  return (
    <div className="metric-chip flex items-center justify-center py-10 text-sm text-ink/40">
      {msg}
    </div>
  );
}

function MetricCard({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="metric-chip flex flex-col gap-0.5">
      <span className="text-xs text-ink/50">{label}</span>
      <span className={`text-xl font-bold tabular-nums ${color ?? ''}`}>{value}</span>
      {sub && <span className="text-xs text-ink/40">{sub}</span>}
    </div>
  );
}

// ─── Premium decay bar ───────────────────────────────────────────────────────

function PremiumBar({ pos }: { pos: OptPosition }) {
  const entry = pos.entry_total_prem;
  const current = pos.current_total_prem ?? entry;
  const target = entry * (1 - pos.target_pct);
  // progress: 0 = entry, 100 = target
  const progress = Math.max(0, Math.min(100, ((entry - current) / (entry - target)) * 100));
  const decayPct = pct(current, entry);
  const isProfit = current < entry;

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-ink/50">
        <span>Entry ₹{entry.toFixed(2)}</span>
        <span className={isProfit ? 'text-emerald-600' : 'text-rose-500'}>
          {sign(decayPct)}{decayPct.toFixed(1)}% decay
        </span>
        <span>Target ₹{target.toFixed(2)}</span>
      </div>
      <div className="relative h-2 rounded-full bg-black/10 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${isProfit ? 'bg-emerald-500' : 'bg-rose-400'}`}
          style={{ width: `${Math.max(2, progress)}%` }}
        />
      </div>
    </div>
  );
}

// ─── IV Source badge ──────────────────────────────────────────────────────────

function IvBadge({ source }: { source: 'nse' | 'synthetic' }) {
  return source === 'nse' ? (
    <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
      NSE live
    </span>
  ) : (
    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
      Synthetic
    </span>
  );
}

// ─── Open position row ────────────────────────────────────────────────────────

function OpenPositionRow({ pos }: { pos: OptPosition }) {
  const currentPnl = pos.current_pnl ?? 0;
  const entryPrem = pos.entry_total_prem;
  const currentPrem = pos.current_total_prem ?? entryPrem;
  const target = entryPrem * (1 - pos.target_pct);
  const stopLevel = entryPrem * (1 + pos.stop_pct);

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="font-bold">{pos.symbol}</span>
          <span className="ml-2 text-sm text-ink/50">
            Strike {pos.strike} · {pos.lots}×{pos.lot_size} lots · {pos.expiry}
          </span>
        </div>
        <span className={`text-sm font-semibold tabular-nums ${pnlColor(currentPnl)}`}>
          {sign(currentPnl)}{fmtPrice(currentPnl)}
        </span>
      </div>

      <PremiumBar pos={pos} />

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-ink/60 sm:grid-cols-4">
        <div>
          <span className="text-ink/40">Entry prem</span>
          <br />
          <span className="font-medium text-ink">₹{entryPrem.toFixed(2)}</span>
        </div>
        <div>
          <span className="text-ink/40">Current prem</span>
          <br />
          <span className={`font-medium ${currentPrem <= entryPrem ? 'text-emerald-600' : 'text-rose-500'}`}>
            ₹{currentPrem.toFixed(2)}
          </span>
        </div>
        <div>
          <span className="text-ink/40">Target</span>
          <br />
          <span className="font-medium text-ink">₹{target.toFixed(2)}</span>
        </div>
        <div>
          <span className="text-ink/40">Stop</span>
          <br />
          <span className="font-medium text-rose-500">₹{stopLevel.toFixed(2)}</span>
        </div>
      </div>

      <div className="flex items-center justify-between text-xs text-ink/40">
        <span>Entered {fmtDate(pos.entry_at)} · spot ₹{pos.entry_spot.toFixed(0)}</span>
        <span>IV {(pos.entry_iv * 100).toFixed(0)}% (synthetic)</span>
      </div>
    </div>
  );
}

// ─── Closed position row ──────────────────────────────────────────────────────

function ClosedPositionRow({ pos }: { pos: OptPosition }) {
  const net = pos.net_pnl ?? 0;
  const decayPct = pos.exit_total_prem != null
    ? pct(pos.exit_total_prem, pos.entry_total_prem)
    : 0;
  const exitLabels: Record<string, string> = {
    target_hit: '✓ Target',
    stop_hit: '✗ Stop',
    time_stop: '⏱ Time',
    eod_close: '⊘ EOD',
  };

  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2 border-b border-black/5 py-2 last:border-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm">
        <span className="font-semibold">{pos.symbol}</span>
        <span className="text-ink/50">
          {pos.strike} · {fmtDate(pos.entry_at)} → {pos.exit_at ? fmtDate(pos.exit_at) : '?'}
        </span>
        <span className="rounded bg-black/5 px-1.5 py-0.5 text-xs">
          {exitLabels[pos.exit_reason ?? ''] ?? pos.exit_reason ?? '?'}
        </span>
        <span className="text-xs text-ink/40">
          decay {sign(decayPct)}{decayPct.toFixed(1)}%
        </span>
      </div>
      <span className={`text-sm font-bold tabular-nums ${pnlColor(net)}`}>
        {sign(net)}{fmtPrice(net)}
      </span>
    </div>
  );
}

// ─── IV Chart (straddle mid over time per signal) ────────────────────────────

function IvDecayChart({ snapshots }: { snapshots: ChainSnapshot[] }) {
  if (snapshots.length < 2) return null;

  // Group by signal_id, show first signal's decay as representative
  const bySignal: Record<string, ChainSnapshot[]> = {};
  for (const s of snapshots) {
    if (!bySignal[s.signal_id]) bySignal[s.signal_id] = [];
    (bySignal[s.signal_id] as ChainSnapshot[]).push(s);
  }
  const sigIds = Object.keys(bySignal).slice(0, 3); // show up to 3 signals

  const series = sigIds.map((sid) => {
    const snaps = (bySignal[sid] ?? []).sort(
      (a, b) => new Date(a.snapshot_at).getTime() - new Date(b.snapshot_at).getTime(),
    );
    if (snaps.length === 0) return [];
    const first = snaps[0]!;
    const t0 = new Date(first.snapshot_at).getTime();
    const entryPrem = first.nse_straddle_mid ?? first.bs_straddle_mid;
    return snaps.map((s) => ({
      min: Math.round((new Date(s.snapshot_at).getTime() - t0) / 60_000),
      pct: entryPrem > 0
        ? Math.round(((s.nse_straddle_mid ?? s.bs_straddle_mid) / entryPrem - 1) * 1000) / 10
        : 0,
      sym: s.symbol,
      src: s.iv_source,
    }));
  });

  const colors = ['#10b981', '#3b82f6', '#f59e0b'];

  return (
    <div className="metric-chip space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Straddle premium decay (by signal)</h3>
        <span className="text-xs text-ink/40">0% = entry premium</span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <XAxis
            dataKey="min"
            type="number"
            domain={['auto', 'auto']}
            tickFormatter={(v) => `${v}m`}
            tick={{ fontSize: 10 }}
            label={{ value: 'min after signal', position: 'insideBottomRight', fontSize: 10 }}
          />
          <YAxis
            tickFormatter={(v) => `${v}%`}
            tick={{ fontSize: 10 }}
            domain={['auto', 'auto']}
          />
          <ReferenceLine y={0} stroke="#999" strokeDasharray="3 3" />
          <Tooltip
            formatter={(v: number, name: string) => [`${v}%`, name]}
            labelFormatter={(l) => `${l} min`}
          />
          {series.map((data, i) => (
            <Line
              key={sigIds[i] ?? i}
              data={data}
              dataKey="pct"
              name={data[0]?.sym ?? (sigIds[i] ?? '').slice(-4)}
              stroke={colors[i] ?? '#999'}
              dot={false}
              strokeWidth={2}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <p className="text-xs text-ink/40">
        Negative = premium decayed (seller profits). Based on
        {snapshots[0]?.iv_source === 'nse' ? ' NSE live' : ' synthetic BS'} prices.
      </p>
    </div>
  );
}

// ─── Equity curve ─────────────────────────────────────────────────────────────

function EquityCurve({ curve }: { curve: { date: string; cumul: number }[] }) {
  if (curve.length < 2) return null;
  const data = curve.map((p) => ({
    ...p,
    date: new Date(p.date).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }),
  }));
  return (
    <div className="metric-chip space-y-2">
      <h3 className="text-sm font-semibold">Cumulative P&L (closed trades)</h3>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 10 }} />
          <YAxis tickFormatter={(v) => `₹${v}`} tick={{ fontSize: 10 }} />
          <ReferenceLine y={0} stroke="#999" strokeDasharray="3 3" />
          <Tooltip formatter={(v: number) => [fmtPrice(v), 'Cumul net P&L']} />
          <Line dataKey="cumul" stroke="#3b82f6" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function OptionsPage() {
  const [positions, setPositions] = useState<OptPosition[]>([]);
  const [stats, setStats] = useState<OptStats | null>(null);
  const [snapshots, setSnapshots] = useState<ChainSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'open' | 'closed'>('open');

  const load = useCallback(async () => {
    try {
      const [posRes, statsRes, snapRes] = await Promise.all([
        fetchOptPositions('all'),
        fetchOptStats(),
        fetchChainSnapshots({ hours: 48 }),
      ]);
      setPositions(posRes.positions);
      setStats(statsRes);
      setSnapshots(snapRes.snapshots);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Load failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const { marketOpen } = useMarketRefresh(load, 30_000);

  const open = positions.filter((p) => p.status === 'open');
  const closed = positions.filter((p) => p.status === 'closed');

  if (loading)
    return (
      <div className="flex h-64 items-center justify-center text-ink/40">Loading…</div>
    );

  if (error)
    return (
      <div className="flex h-64 items-center justify-center text-rose-500">{error}</div>
    );

  const noData = positions.length === 0 && snapshots.length === 0;

  return (
    <div className="space-y-6 p-4 sm:p-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Options Paper Trading</h1>
          <p className="text-sm text-ink/50">
            Short straddle · synthetic BS pricing · data collection for IV-crush backtesting
          </p>
        </div>
        <div className="flex items-center gap-2">
          {noData ? (
            <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-700">
              ⏸ Awaiting first signal
            </span>
          ) : (
            <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
              ● Active
            </span>
          )}
          <span className="text-xs text-ink/40">{marketOpen ? 'auto-refresh 30s' : 'market closed'}</span>
        </div>
      </div>

      {/* No data yet — waiting on first market session */}
      {noData && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <p className="font-semibold">No trades yet — system is live and will pick up signals automatically.</p>
          <p className="mt-1 text-amber-700">
            <code className="rounded bg-amber-100 px-1">optTradeDecision</code> opens straddles
            from eligible signals during market hours.{' '}
            <code className="rounded bg-amber-100 px-1">optPositionMonitor</code> reprices and
            logs chain snapshots every 3 min. Both run on EventBridge — no manual steps needed.
          </p>
        </div>
      )}

      {/* Stats summary */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard
            label="Open positions"
            value={String(stats.open_count)}
            sub="short straddles"
          />
          <MetricCard
            label="Unrealized P&L"
            value={`${sign(stats.unrealized_pnl)}${fmtPrice(stats.unrealized_pnl)}`}
            color={pnlColor(stats.unrealized_pnl)}
            sub="premium decay so far"
          />
          <MetricCard
            label="Realized net P&L"
            value={`${sign(stats.total_net_pnl)}${fmtPrice(stats.total_net_pnl)}`}
            color={pnlColor(stats.total_net_pnl)}
            sub={`${stats.closed_count} closed · ${stats.win_rate_pct ?? '—'}% win`}
          />
          <MetricCard
            label="Expectancy"
            value={
              stats.expectancy_inr != null
                ? `${sign(stats.expectancy_inr)}${fmtPrice(stats.expectancy_inr)}`
                : '—'
            }
            color={stats.expectancy_inr != null ? pnlColor(stats.expectancy_inr) : ''}
            sub="per trade"
          />
        </div>
      )}

      {/* IV data quality row */}
      {stats && stats.chain_snapshots_7d > 0 && (
        <div className="metric-chip flex flex-wrap items-center gap-4 text-sm">
          <span className="font-semibold">Chain log (7d):</span>
          <span>{stats.chain_snapshots_7d} snapshots</span>
          {Object.entries(stats.iv_source_breakdown).map(([src, count]) => (
            <span key={src} className="flex items-center gap-1">
              <IvBadge source={src as 'nse' | 'synthetic'} />
              <span className="text-ink/60">{count}</span>
            </span>
          ))}
          <span className="ml-auto text-xs text-ink/40">
            NSE live IV = real data for backtest; Synthetic = BS model-implied
          </span>
        </div>
      )}

      {/* Decay chart */}
      {snapshots.length > 1 && <IvDecayChart snapshots={snapshots} />}

      {/* Equity curve */}
      {stats && stats.equity_curve.length > 1 && (
        <EquityCurve curve={stats.equity_curve} />
      )}

      {/* Positions tab */}
      <div>
        <div className="mb-3 flex gap-2">
          {(['open', 'closed'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-full px-4 py-1.5 text-sm font-semibold transition ${
                tab === t ? 'bg-accent text-white shadow-sm' : 'hover:bg-black/5'
              }`}
            >
              {t === 'open' ? `Open (${open.length})` : `Closed (${closed.length})`}
            </button>
          ))}
        </div>

        {tab === 'open' && (
          <div className="space-y-3">
            {open.length === 0 ? (
              <Empty msg="No open straddles" />
            ) : (
              open.map((p) => <OpenPositionRow key={p._id} pos={p} />)
            )}
          </div>
        )}

        {tab === 'closed' && (
          <div className="metric-chip">
            {closed.length === 0 ? (
              <Empty msg="No closed trades yet" />
            ) : (
              <div>
                {/* Exit reason summary */}
                {stats && Object.keys(stats.by_exit_reason).length > 0 && (
                  <div className="mb-4 flex flex-wrap gap-3">
                    {Object.entries(stats.by_exit_reason).map(([r, d]) => (
                      <div key={r} className="text-center">
                        <div className="text-xs text-ink/40">{r.replace('_', ' ')}</div>
                        <div className="text-sm font-semibold">{d.count}</div>
                        <div className={`text-xs ${pnlColor(d.total_net_pnl)}`}>
                          {sign(d.total_net_pnl)}{fmtPrice(d.total_net_pnl)}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {closed.map((p) => (
                  <ClosedPositionRow key={p._id} pos={p} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Backtest readiness note */}
      {stats && (
        <div className="rounded-xl border border-black/10 bg-panel p-4 text-sm text-ink/60">
          <p className="font-semibold text-ink">Data collection status</p>
          <p className="mt-1">
            Accumulating <strong>opt_chain_snapshots</strong> for every signal during market
            hours. Once you have ~20 paper trading days, run a backtest analysis on this data
            the same way BT7–BT11 analysed nt_signals. The key metric to watch:{' '}
            <strong>does IV drop from signal time to 90-min time-stop?</strong> That&apos;s the
            IV-crush hypothesis. Real NSE live IV confirms or kills it; synthetic prices are the fallback.
          </p>
          {stats.chain_snapshots_7d < 100 && (
            <p className="mt-1 text-amber-600">
              {stats.chain_snapshots_7d} snapshots so far — keep accumulating.
              Target: ~500+ across 10+ signals before drawing conclusions.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
