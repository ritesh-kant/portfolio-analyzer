'use client';

import { Fragment, useState, useEffect, useCallback, useRef } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts';
import { formatCurrency } from '../../lib/format';
import {
  fetchPositions,
  fetchSignals,
  fetchNews,
  fetchStats,
  fetchPipelineStatus,
  fetchPipelineHistory,
  fetchPipelinePositions,
  triggerPipeline,
  type NtPosition,
  type NtSignal,
  type NtNews,
  type NtStats,
  type PipelineStatus,
  type PipelineRun,
  type StageStatus,
} from '../../lib/trading-api';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function holdDays(entryIso: string, exitIso?: string): number {
  const end = exitIso ? new Date(exitIso) : new Date();
  return Math.floor((end.getTime() - new Date(entryIso).getTime()) / 86_400_000);
}

function fmtPrice(n: number) {
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function pnlColor(v: number) {
  return v > 0 ? 'text-emerald-700' : v < 0 ? 'text-rose-600' : 'text-ink/60';
}

function pnlSign(v: number) {
  return v > 0 ? '+' : '';
}

// ─── Small atoms ─────────────────────────────────────────────────────────────

function Empty({ msg }: { msg: string }) {
  return (
    <div className="metric-chip flex items-center justify-center py-10 text-sm text-ink/40">
      {msg}
    </div>
  );
}

function InfoTip({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onFocus={() => setShow(true)}
        onBlur={() => setShow(false)}
        className="flex h-4 w-4 items-center justify-center rounded-full bg-black/10 text-[9px] font-bold text-ink/50 hover:bg-black/20"
        aria-label="Info"
      >
        i
      </button>
      {show && (
        <span className="absolute left-5 top-0 z-10 w-56 rounded-lg border border-black/10 bg-white px-3 py-2 text-xs leading-relaxed text-ink/70 shadow-lg">
          {text}
        </span>
      )}
    </span>
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

function ConfBadge({ confidence }: { confidence: NtPosition['confidence'] }) {
  const map = {
    high: 'bg-emerald-100 text-emerald-700',
    medium: 'bg-amber-100 text-amber-700',
    low: 'bg-black/5 text-ink/50',
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${map[confidence]}`}>
      {confidence}
    </span>
  );
}

function SignalBadge({ signal }: { signal: NtSignal['signal'] | NtPosition['signal'] }) {
  const map = {
    bullish: 'bg-emerald-100 text-emerald-700',
    bearish: 'bg-rose-100 text-rose-700',
    neutral: 'bg-black/5 text-ink/50',
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${map[signal]}`}>
      {signal}
    </span>
  );
}

function ExitBadge({ reason }: { reason: NtPosition['exit_reason'] }) {
  if (!reason) return null;
  const map = {
    sl_hit: 'bg-rose-100 text-rose-700',
    target_hit: 'bg-emerald-100 text-emerald-700',
    day5: 'bg-amber-100 text-amber-700',
  };
  const label = { sl_hit: 'SL Hit', target_hit: 'Target', day5: 'Day 5 Expired' };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${map[reason]}`}>
      {label[reason]}
    </span>
  );
}

// ─── Pipeline status panel ────────────────────────────────────────────────────

function fmt(ms: number) {
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

const STAGE_META: { key: keyof NonNullable<PipelineStatus['stages']>; label: string; detail: (s: PipelineStatus['stages']) => string }[] = [
  { key: 'ingester',   label: 'Ingester',       detail: s => s!.ingester.count > 0 ? `${s!.ingester.count} articles` : s!.ingester.status === 'done' ? 'no new articles' : 'fetching news…' },
  { key: 'classifier', label: 'Classifier',     detail: s => s!.classifier.count > 0 ? `${s!.classifier.count} signals` : s!.classifier.status === 'done' ? '0 signals' : 'classifying…' },
  { key: 'sqs_delay',  label: 'SQS Delay',      detail: s => s!.sqs_delay.remain_ms > 0 ? `${fmt(s!.sqs_delay.remain_ms)} left` : s!.classifier.count === 0 ? 'skipped — no signals' : '15-min wait done' },
  { key: 'trade',      label: 'Trade Decision', detail: s => s!.trade.count > 0 ? `${s!.trade.count} positions opened` : s!.trade.status === 'done' ? (s!.classifier.count === 0 ? 'skipped — no signals' : 'no positions opened') : 'evaluating signals…' },
];

function StageDot({ status }: { status: StageStatus }) {
  if (status === 'done')    return <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 shrink-0" />;
  if (status === 'running') return <span className="h-2.5 w-2.5 rounded-full bg-accent animate-pulse shrink-0" />;
  if (status === 'waiting') return <span className="h-2.5 w-2.5 rounded-full bg-amber-400 animate-pulse shrink-0" />;
  if (status === 'skipped') return <span className="h-2.5 w-2.5 rounded-full bg-black/10 shrink-0" />;
  if (status === 'failed')  return <span className="h-2.5 w-2.5 rounded-full bg-rose-500 shrink-0" />;
  return <span className="h-2.5 w-2.5 rounded-full bg-black/15 shrink-0" />;
}

function PipelineStatusPanel({ pipelineStatus }: { pipelineStatus: PipelineStatus | null }) {
  if (!pipelineStatus?.run) return null;

  const { run, stages, is_active, elapsed_ms } = pipelineStatus;

  const noNews = stages?.ingester.status === 'done' && stages.ingester.count === 0;

  function effectiveStatus(key: keyof NonNullable<PipelineStatus['stages']>): StageStatus {
    if (noNews && key !== 'ingester') return 'skipped';
    return stages?.[key]?.status ?? 'pending';
  }

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Pipeline Run</h2>
          {is_active ? (
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-bold text-accent animate-pulse">
              running
            </span>
          ) : run.status === 'failed' ? (
            <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-bold text-rose-700">
              failed
            </span>
          ) : (
            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
              complete
            </span>
          )}
        </div>
        <span className="text-xs text-ink/40">
          triggered {fmt(elapsed_ms)} ago · {run.source}
        </span>
      </div>

      {run.status === 'failed' && run.error && (
        <p className="rounded-lg bg-rose-50 px-3 py-2 text-[11px] text-rose-700 break-words">
          {run.error}
        </p>
      )}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {STAGE_META.map(({ key, label, detail }) => {
          const status = effectiveStatus(key);
          return (
            <div
              key={key}
              className="flex flex-col gap-1.5 rounded-xl border border-black/5 bg-bg px-3 py-2.5"
            >
              <div className="flex items-center gap-1.5">
                <StageDot status={status} />
                <span className="text-xs font-semibold text-ink/70">{label}</span>
              </div>
              <span
                className={`text-[10px] tabular-nums ${
                  status === 'done' ? 'text-emerald-700' :
                  status === 'waiting' ? 'text-amber-600' :
                  status === 'skipped' ? 'text-ink/30' :
                  status === 'failed' ? 'text-rose-600' :
                  'text-ink/40'
                }`}
              >
                {status === 'skipped' ? 'skipped — no news' : status === 'failed' ? 'stage failed' : stages ? detail(stages) : status}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Pipeline history ─────────────────────────────────────────────────────────

function RunStatusBadge({ status }: { status: PipelineRun['status'] }) {
  if (status === 'running')
    return <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-bold text-accent animate-pulse">running</span>;
  if (status === 'failed')
    return <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-bold text-rose-700">failed</span>;
  return <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">complete</span>;
}

function Stat({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className="text-center">
      <p className="tabular-nums font-semibold">{value ?? '—'}</p>
      <p className="text-[10px] text-ink/40">{label}</p>
    </div>
  );
}

function PipelineRunModal({ run, onClose }: { run: PipelineRun; onClose: () => void }) {
  const [positions, setPositions] = useState<NtPosition[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPipelinePositions(run._id)
      .then((d) => setPositions(d.positions))
      .catch(() => setPositions([]))
      .finally(() => setLoading(false));
  }, [run._id]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center sm:items-center bg-black/30 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-white flex items-center justify-between px-5 py-4 border-b border-black/5">
          <div>
            <p className="text-sm font-semibold">Positions — {fmtDate(run.triggered_at)}</p>
            <p className="text-xs text-ink/40 mt-0.5">{run.source} · <RunStatusBadge status={run.status} /></p>
          </div>
          <button onClick={onClose} className="text-ink/40 hover:text-ink text-lg leading-none px-2">✕</button>
        </div>

        <div className="px-5 py-4">
          {loading && <p className="text-xs text-ink/40 py-4 text-center">Loading…</p>}
          {!loading && positions?.length === 0 && (
            <p className="text-xs text-ink/40 py-4 text-center">
              No positions linked to this run.{' '}
              {run.positions_opened && run.positions_opened > 0
                ? 'Positions opened before run-id tracking was added.'
                : ''}
            </p>
          )}
          {!loading && positions && positions.length > 0 && (
            <div className="space-y-2">
              {positions.map((p) => {
                const netPnl = p.net_pnl ?? 0;
                const isOpen = p.status === 'open';
                return (
                  <div key={p._id} className="rounded-xl border border-black/5 px-4 py-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-sm">{p.symbol}</span>
                      {isOpen ? (
                        <span className="text-[10px] rounded-full bg-sky-100 text-sky-700 px-2 py-0.5 font-bold">open</span>
                      ) : (
                        <span className={`tabular-nums text-sm font-semibold ${pnlColor(netPnl)}`}>
                          {pnlSign(netPnl)}{formatCurrency(netPnl)}
                        </span>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-ink/50">
                      <span>Entry {fmtPrice(p.entry_price)} × {p.qty}</span>
                      <span className="capitalize">{p.confidence} · {p.sector}</span>
                      {p.exit_reason && <span className="capitalize">{p.exit_reason.replace(/_/g, ' ')}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PipelineHistory({ refreshKey }: { refreshKey: number }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ runs: PipelineRun[]; total: number; pages: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState<PipelineRun | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchPipelineHistory(page)
      .then(setData)
      .catch(() => null)
      .finally(() => setLoading(false));
  }, [page, refreshKey]);

  if (!data && loading) return null;
  if (!data || data.total === 0) return null;

  const durationStr = (run: PipelineRun) => {
    if (!run.completed_at) return null;
    const ms = new Date(run.completed_at).getTime() - new Date(run.triggered_at).getTime();
    const s = Math.floor(ms / 1000);
    return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  };

  return (
    <>
    <div className="metric-chip space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Pipeline History</h2>
        <span className="text-xs text-ink/40">{data.total} run{data.total !== 1 ? 's' : ''}</span>
      </div>

      <div className="overflow-x-auto p-0 -mx-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-black/5 text-xs text-ink/50">
              <th className="px-4 py-2 text-left font-semibold">Triggered</th>
              <th className="px-4 py-2 text-left font-semibold">Source</th>
              <th className="px-4 py-2 text-left font-semibold">Status</th>
              <th className="px-4 py-2 text-center font-semibold">Articles</th>
              <th className="px-4 py-2 text-center font-semibold">Signals</th>
              <th className="px-4 py-2 text-center font-semibold">Positions</th>
              <th className="px-4 py-2 text-right font-semibold">Duration</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map((run) => (
              <tr
                key={run._id}
                className="border-b border-black/5 last:border-0 hover:bg-black/[0.03] cursor-pointer"
                onClick={() => setSelectedRun(run)}
              >
                <td className="px-4 py-2.5 text-xs text-ink/60">{fmtDate(run.triggered_at)}</td>
                <td className="px-4 py-2.5 text-xs text-ink/50">{run.source}</td>
                <td className="px-4 py-2.5"><RunStatusBadge status={run.status} /></td>
                <td className="px-4 py-2.5"><Stat label="articles" value={run.new_articles} /></td>
                <td className="px-4 py-2.5"><Stat label="signals" value={run.signals_created} /></td>
                <td className="px-4 py-2.5"><Stat label="positions" value={run.positions_opened} /></td>
                <td className="px-4 py-2.5 text-right text-xs text-ink/40">{durationStr(run) ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.pages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-1">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="rounded px-2.5 py-1 text-xs font-semibold disabled:opacity-30 hover:bg-black/5"
          >
            ← Prev
          </button>
          <span className="text-xs text-ink/40">page {page} of {data.pages}</span>
          <button
            onClick={() => setPage((p) => Math.min(data.pages, p + 1))}
            disabled={page === data.pages}
            className="rounded px-2.5 py-1 text-xs font-semibold disabled:opacity-30 hover:bg-black/5"
          >
            Next →
          </button>
        </div>
      )}
    </div>
    {selectedRun && <PipelineRunModal run={selectedRun} onClose={() => setSelectedRun(null)} />}
    </>
  );
}

// ─── Equity curve ─────────────────────────────────────────────────────────────

function EquityCurvePanel({ stats }: { stats: NtStats }) {
  const { equity_curve, max_drawdown_inr } = stats;
  if (!equity_curve || equity_curve.length < 2) return null;

  const data = equity_curve.map((pt, i) => ({
    i,
    date: new Date(pt.date).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }),
    cumul: pt.cumul,
  }));

  const minVal = Math.min(...data.map((d) => d.cumul));
  const maxVal = Math.max(...data.map((d) => d.cumul));
  const latest = equity_curve[equity_curve.length - 1]?.cumul ?? 0;
  const isPositive = latest >= 0;

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Equity Curve</h2>
        <div className="flex items-center gap-4 text-xs">
          <span className="text-ink/50">
            Net P&L{' '}
            <span className={`font-semibold tabular-nums ${pnlColor(latest)}`}>
              {pnlSign(latest)}{formatCurrency(latest)}
            </span>
          </span>
          {max_drawdown_inr > 0 && (
            <span className="text-ink/50">
              Max DD{' '}
              <span className="font-semibold tabular-nums text-rose-600">
                −{formatCurrency(max_drawdown_inr)}
              </span>
            </span>
          )}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: 'var(--color-ink)', opacity: 0.4 }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: 'var(--color-ink)', opacity: 0.4 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) =>
              v >= 1000 || v <= -1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v))
            }
            domain={[Math.min(minVal * 1.1, minVal - 50), Math.max(maxVal * 1.1, maxVal + 50)]}
            width={48}
          />
          <ReferenceLine y={0} stroke="var(--color-ink)" strokeOpacity={0.15} strokeDasharray="3 3" />
          <Tooltip
            contentStyle={{
              background: 'var(--color-panel)',
              border: '1px solid rgba(0,0,0,0.08)',
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(v: number) => [
              `${pnlSign(v)}${formatCurrency(v)}`,
              'Cumul Net P&L',
            ]}
            labelStyle={{ color: 'var(--color-ink)', opacity: 0.5, marginBottom: 2 }}
          />
          <Line
            type="monotone"
            dataKey="cumul"
            stroke={isPositive ? '#059669' : '#e11d48'}
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── P&L attribution ──────────────────────────────────────────────────────────

function PnlAttributionPanel({ stats }: { stats: NtStats }) {
  if (stats.closed_count === 0) return null;

  const {
    total_gross_pnl,
    total_costs_inr,
    total_realized_net_pnl,
    cost_breakdown,
    avg_win_inr,
    avg_loss_inr,
    expectancy_inr,
    profit_factor,
  } = stats;

  const costDragPct =
    total_gross_pnl !== 0
      ? Math.abs((total_costs_inr / Math.abs(total_gross_pnl)) * 100)
      : null;

  const costRows: { label: string; tooltip: string; value: number }[] = cost_breakdown
    ? [
        { label: 'STT', tooltip: '0.1% each side (delivery)', value: cost_breakdown.stt },
        { label: 'Slippage', tooltip: 'Modelled 5 bps/side', value: cost_breakdown.slippage },
        { label: 'Brokerage', tooltip: '₹20/order flat', value: cost_breakdown.brokerage },
        { label: 'GST', tooltip: '18% on brokerage + exchange', value: cost_breakdown.gst },
        { label: 'Exchange', tooltip: 'NSE + SEBI charges', value: cost_breakdown.exchange },
        { label: 'Stamp', tooltip: '0.015% buy-side', value: cost_breakdown.stamp },
      ]
    : [];

  return (
    <div className="metric-chip space-y-4">
      <h2 className="text-sm font-semibold">P&L Attribution</h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {/* Waterfall */}
        <div className="space-y-2">
          <p className="text-xs font-semibold text-ink/50 uppercase tracking-wide">Cost waterfall</p>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between rounded-lg bg-black/[0.03] px-3 py-2">
              <span className="text-xs text-ink/60">Gross P&L</span>
              <span className={`tabular-nums text-sm font-semibold ${pnlColor(total_gross_pnl)}`}>
                {pnlSign(total_gross_pnl)}{formatCurrency(total_gross_pnl)}
              </span>
            </div>
            <div className="flex items-center justify-between rounded-lg bg-rose-50 px-3 py-2">
              <span className="text-xs text-ink/60">
                Total Costs
                {costDragPct !== null && (
                  <span className="ml-1 text-rose-500">({costDragPct.toFixed(1)}% drag)</span>
                )}
              </span>
              <span className="tabular-nums text-sm font-semibold text-rose-600">
                −{formatCurrency(total_costs_inr)}
              </span>
            </div>
            <div className="flex items-center justify-between rounded-lg bg-black/[0.03] px-3 py-2 border border-black/10">
              <span className="text-xs font-semibold text-ink/70">Net P&L</span>
              <span className={`tabular-nums text-sm font-bold ${pnlColor(total_realized_net_pnl)}`}>
                {pnlSign(total_realized_net_pnl)}{formatCurrency(total_realized_net_pnl)}
              </span>
            </div>
          </div>
        </div>

        {/* Cost breakdown bar chart or expectancy */}
        <div className="space-y-2">
          {cost_breakdown ? (
            <>
              <p className="text-xs font-semibold text-ink/50 uppercase tracking-wide">Cost breakdown</p>
              <div className="space-y-1.5">
                {costRows.map(({ label, tooltip, value }) => {
                  const pct = total_costs_inr > 0 ? (value / total_costs_inr) * 100 : 0;
                  return (
                    <div key={label} className="flex items-center gap-2">
                      <span className="w-16 shrink-0 text-xs text-ink/60" title={tooltip}>{label}</span>
                      <div className="flex-1 h-1.5 bg-black/5 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-rose-400 rounded-full"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="w-16 text-right tabular-nums text-xs text-ink/70">
                        {formatCurrency(value)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <p className="text-xs text-ink/40 pt-4">Cost breakdown available for trades after the model upgrade.</p>
          )}
        </div>
      </div>

      {/* Expectancy row */}
      {(expectancy_inr !== null || profit_factor !== null) && (
        <div className="border-t border-black/5 pt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {expectancy_inr !== null && (
            <div>
              <p className="text-xs text-ink/50">Expectancy</p>
              <p className={`mt-0.5 font-display text-lg font-bold ${pnlColor(expectancy_inr)}`}>
                {pnlSign(expectancy_inr)}{formatCurrency(expectancy_inr)}
              </p>
              <p className="text-[10px] text-ink/40">avg ₹ per trade</p>
            </div>
          )}
          {profit_factor !== null && (
            <div>
              <p className="text-xs text-ink/50">Profit Factor</p>
              <p className={`mt-0.5 font-display text-lg font-bold ${profit_factor >= 1 ? 'text-emerald-700' : 'text-rose-600'}`}>
                {profit_factor.toFixed(2)}×
              </p>
              <p className="text-[10px] text-ink/40">gross wins / losses</p>
            </div>
          )}
          {avg_win_inr !== null && (
            <div>
              <p className="text-xs text-ink/50">Avg Win</p>
              <p className="mt-0.5 font-display text-lg font-bold text-emerald-700">
                +{formatCurrency(avg_win_inr)}
              </p>
              <p className="text-[10px] text-ink/40">{stats.win_count} trades</p>
            </div>
          )}
          {avg_loss_inr !== null && (
            <div>
              <p className="text-xs text-ink/50">Avg Loss</p>
              <p className="mt-0.5 font-display text-lg font-bold text-rose-600">
                {formatCurrency(avg_loss_inr)}
              </p>
              <p className="text-[10px] text-ink/40">{stats.loss_count} trades</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Risk metrics ─────────────────────────────────────────────────────────────

function RiskMetricsPanel({ positions }: { positions: NtPosition[] }) {
  const closed = positions.filter((p) => p.status === 'closed' && p.net_pnl != null);
  if (closed.length < 3) return null;

  const sorted = [...closed].sort(
    (a, b) => new Date(a.exit_at!).getTime() - new Date(b.exit_at!).getTime(),
  );
  const pnls = sorted.map((p) => p.net_pnl!);

  const mean = pnls.reduce((s, v) => s + v, 0) / pnls.length;
  const variance = pnls.reduce((s, v) => s + (v - mean) ** 2, 0) / pnls.length;
  const stdDev = Math.sqrt(variance);
  const downside = pnls.filter((v) => v < 0);
  const downsideVariance =
    downside.length > 0 ? downside.reduce((s, v) => s + v ** 2, 0) / downside.length : 0;
  const downsideDev = Math.sqrt(downsideVariance);

  const sharpe = stdDev > 0 ? mean / stdDev : null;
  const sortino = downsideDev > 0 ? mean / downsideDev : null;

  let maxWin = 0,
    maxLoss = 0,
    curWin = 0,
    curLoss = 0;
  for (const p of pnls) {
    if (p > 0) {
      curWin++;
      curLoss = 0;
      maxWin = Math.max(maxWin, curWin);
    } else {
      curLoss++;
      curWin = 0;
      maxLoss = Math.max(maxLoss, curLoss);
    }
  }
  const lastPnl = pnls[pnls.length - 1] ?? 0;
  const streak = lastPnl > 0 ? curWin : -curLoss;

  let peak = 0,
    cumul = 0,
    maxDD = 0;
  for (const p of pnls) {
    cumul += p;
    if (cumul > peak) peak = cumul;
    const dd = peak - cumul;
    if (dd > maxDD) maxDD = dd;
  }
  const calmar = maxDD > 0 ? (mean * closed.length) / maxDD : null;

  const metrics = [
    {
      label: 'Sharpe (trade)',
      value: sharpe != null ? sharpe.toFixed(2) : '—',
      color: sharpe != null && sharpe >= 1 ? 'text-emerald-700' : sharpe != null && sharpe < 0 ? 'text-rose-600' : '',
      tip: 'Trade-level Sharpe: mean P&L ÷ std dev of all trade P&Ls. ≥1.0 is good. Not annualised — computed per trade, not per day.',
    },
    {
      label: 'Sortino (trade)',
      value: sortino != null ? sortino.toFixed(2) : '—',
      color: sortino != null && sortino >= 1 ? 'text-emerald-700' : sortino != null && sortino < 0 ? 'text-rose-600' : '',
      tip: 'Like Sharpe but only penalises losing trades. A higher Sortino vs Sharpe means losses are small relative to wins.',
    },
    {
      label: 'Calmar',
      value: calmar != null ? calmar.toFixed(2) : '—',
      color: calmar != null && calmar >= 1 ? 'text-emerald-700' : calmar != null && calmar < 0 ? 'text-rose-600' : '',
      tip: 'Total net P&L ÷ max drawdown. Measures how much you earned per rupee of peak-to-trough loss. Higher is better.',
    },
    {
      label: 'Max Win Streak',
      value: String(maxWin),
      color: 'text-emerald-700',
      tip: 'Longest consecutive winning trade run across all closed trades.',
    },
    {
      label: 'Max Loss Streak',
      value: String(maxLoss),
      color: 'text-rose-600',
      tip: 'Longest consecutive losing trade run across all closed trades.',
    },
    {
      label: 'Current Streak',
      value: streak > 0 ? `+${streak}W` : streak < 0 ? `${Math.abs(streak)}L` : '—',
      color: streak > 0 ? 'text-emerald-700' : streak < 0 ? 'text-rose-600' : '',
      tip: 'Current run counting from the latest closed trade. +2W means 2 consecutive wins.',
    },
  ];

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Risk Metrics</h2>
        <InfoTip text="Risk-adjusted performance metrics computed from all closed trades. Sharpe and Sortino are trade-level (not annualised daily returns)." />
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {metrics.map(({ label, value, color, tip }) => (
          <div
            key={label}
            className="rounded-xl border border-black/5 bg-bg px-3 py-2.5 space-y-0.5"
          >
            <div className="flex items-center gap-1">
              <p className="text-[10px] text-ink/50">{label}</p>
              <InfoTip text={tip} />
            </div>
            <p className={`font-display text-lg font-bold tabular-nums ${color}`}>{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Confidence tier breakdown ────────────────────────────────────────────────

function ConfidenceTierPanel({ positions }: { positions: NtPosition[] }) {
  const closed = positions.filter((p) => p.status === 'closed' && p.net_pnl != null);
  if (closed.length === 0) return null;

  type Tier = { trades: number; wins: number; totalPnl: number };
  const tiers = new Map<string, Tier>([
    ['high', { trades: 0, wins: 0, totalPnl: 0 }],
    ['medium', { trades: 0, wins: 0, totalPnl: 0 }],
    ['low', { trades: 0, wins: 0, totalPnl: 0 }],
  ]);
  for (const p of closed) {
    const k = p.confidence ?? 'low';
    const t = tiers.get(k) ?? { trades: 0, wins: 0, totalPnl: 0 };
    t.trades++;
    if ((p.net_pnl ?? 0) > 0) t.wins++;
    t.totalPnl += p.net_pnl ?? 0;
    tiers.set(k, t);
  }

  const rows = (['high', 'medium', 'low'] as const)
    .map((tier) => {
      const t = tiers.get(tier)!;
      return {
        tier,
        trades: t.trades,
        wins: t.wins,
        totalPnl: t.totalPnl,
        winRate: t.trades > 0 ? (t.wins / t.trades) * 100 : null,
        avgPnl: t.trades > 0 ? t.totalPnl / t.trades : null,
      };
    })
    .filter((r) => r.trades > 0);

  if (rows.length === 0) return null;

  const colorMap = { high: 'text-emerald-700', medium: 'text-amber-600', low: 'text-ink/50' };
  const bgMap = { high: 'bg-emerald-100', medium: 'bg-amber-100', low: 'bg-black/5' };

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Performance by LLM Confidence</h2>
        <InfoTip text="How well the AI's confidence tiers predict actual outcomes. High-confidence signals should show better win rates and P&L than medium/low — if they don't, the confidence scoring needs recalibration." />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-ink/40">
              <th className="pb-2 font-semibold">Confidence</th>
              <th className="pb-2 text-right font-semibold">Trades</th>
              <th className="pb-2 text-right font-semibold">Win Rate</th>
              <th className="pb-2 text-right font-semibold">Avg P&amp;L</th>
              <th className="pb-2 text-right font-semibold">Total Net P&amp;L</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-black/5">
            {rows.map(({ tier, trades, winRate, avgPnl, totalPnl }) => (
              <tr key={tier}>
                <td className="py-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${bgMap[tier]} ${colorMap[tier]}`}
                  >
                    {tier}
                  </span>
                </td>
                <td className="py-2 text-right tabular-nums font-semibold">{trades}</td>
                <td className="py-2 text-right tabular-nums">
                  <span
                    className={
                      winRate != null && winRate >= 50
                        ? 'font-semibold text-emerald-700'
                        : 'font-semibold text-rose-600'
                    }
                  >
                    {winRate != null ? `${winRate.toFixed(0)}%` : '—'}
                  </span>
                </td>
                <td
                  className={`py-2 text-right tabular-nums font-semibold ${avgPnl != null ? pnlColor(avgPnl) : ''}`}
                >
                  {avgPnl != null ? `${pnlSign(avgPnl)}${formatCurrency(avgPnl)}` : '—'}
                </td>
                <td className={`py-2 text-right tabular-nums font-semibold ${pnlColor(totalPnl)}`}>
                  {pnlSign(totalPnl)}
                  {formatCurrency(totalPnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Sector P&L ───────────────────────────────────────────────────────────────

function SectorPnlPanel({ positions }: { positions: NtPosition[] }) {
  const closed = positions.filter((p) => p.status === 'closed' && p.net_pnl != null && p.sector);
  if (closed.length === 0) return null;

  const byS: Record<string, { total: number; count: number }> = {};
  for (const p of closed) {
    const s = p.sector ?? 'Unknown';
    if (!byS[s]) byS[s] = { total: 0, count: 0 };
    byS[s].total += p.net_pnl ?? 0;
    byS[s].count++;
  }

  const rows = Object.entries(byS)
    .map(([sector, d]) => ({ sector, ...d }))
    .sort((a, b) => b.total - a.total);

  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.total)), 1);

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">P&amp;L by Sector</h2>
        <InfoTip text="Net P&L grouped by the sector the LLM tagged on the originating news signal. Identifies which market sectors your strategy works best in." />
      </div>
      <div className="space-y-2">
        {rows.map(({ sector, total, count }) => {
          const pct = (Math.abs(total) / maxAbs) * 100;
          return (
            <div key={sector} className="flex items-center gap-3">
              <span className="w-28 shrink-0 truncate text-xs text-ink/70" title={sector}>
                {sector}
              </span>
              <div className="flex-1 h-4 rounded-full bg-black/5 overflow-hidden">
                <div
                  className={`h-full rounded-full ${total >= 0 ? 'bg-emerald-400' : 'bg-rose-400'}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span
                className={`w-24 text-right tabular-nums text-xs font-semibold ${pnlColor(total)}`}
              >
                {pnlSign(total)}
                {formatCurrency(total)}
              </span>
              <span className="w-12 text-right text-[10px] text-ink/40">{count} trades</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Signal → Trade funnel ────────────────────────────────────────────────────

function SignalFunnelPanel({
  signals,
  positions,
}: {
  signals: NtSignal[];
  positions: NtPosition[];
}) {
  if (signals.length === 0) return null;

  const total = signals.length;
  const evaluated = signals.filter((s) => s.acted_on).length;
  const tradedSignalIds = new Set(positions.map((p) => p.signal_id).filter(Boolean));
  const traded = tradedSignalIds.size;

  const evalRate = total > 0 ? (evaluated / total) * 100 : 0;
  const tradeRate = evaluated > 0 ? (traded / evaluated) * 100 : 0;

  const steps = [
    {
      label: 'Classified',
      value: total,
      pct: 100,
      tip: 'Total news signals the LLM classified and stored in this session.',
    },
    {
      label: 'Evaluated',
      value: evaluated,
      pct: evalRate,
      tip: 'Signals that passed confidence/freshness gates and were sent to the trade-decision Lambda for evaluation.',
    },
    {
      label: 'Traded',
      value: traded,
      pct: tradeRate,
      tip: 'Distinct signals that resulted in at least one position being opened (passed regime + conviction gates, had a liquid stock with a valid price).',
    },
  ];

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Signal → Trade Funnel</h2>
        <InfoTip text="How many news signals survive each stage of the pipeline. Low Traded % means the regime or conviction gates are filtering aggressively — which may be intentional." />
      </div>
      <div className="grid grid-cols-3 gap-3">
        {steps.map(({ label, value, pct, tip }, i) => (
          <div
            key={label}
            className="rounded-xl border border-black/5 bg-bg px-4 py-3 text-center space-y-1"
          >
            <div className="flex items-center justify-center gap-1">
              {i > 0 && <span className="text-[10px] text-ink/30">▶&nbsp;</span>}
              <p className="text-[10px] text-ink/50">{label}</p>
              <InfoTip text={tip} />
            </div>
            <p className="font-display text-2xl font-bold tabular-nums">{value}</p>
            <p className="text-[10px] tabular-nums text-ink/40">
              {i === 0 ? '100%' : `${pct.toFixed(0)}% of prev`}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── R-multiple distribution ──────────────────────────────────────────────────

function RMultiplePanel({ positions }: { positions: NtPosition[] }) {
  const closed = positions.filter(
    (p) => p.status === 'closed' && p.net_pnl != null && p.sl_pct_used && p.entry_price && p.qty,
  );
  if (closed.length < 3) return null;

  const rmultiples = closed
    .map((p) => {
      const risk = p.entry_price * (p.sl_pct_used ?? 0.015) * p.qty;
      return risk > 0 ? (p.net_pnl ?? 0) / risk : null;
    })
    .filter((r): r is number => r != null);

  if (rmultiples.length === 0) return null;

  const buckets = [
    { label: '<−1R', min: -Infinity, max: -1, color: 'bg-rose-500' },
    { label: '−1–0R', min: -1, max: 0, color: 'bg-rose-300' },
    { label: '0–1R', min: 0, max: 1, color: 'bg-emerald-200' },
    { label: '1–3R', min: 1, max: 3, color: 'bg-emerald-400' },
    { label: '3–5R', min: 3, max: 5, color: 'bg-emerald-600' },
    { label: '>5R', min: 5, max: Infinity, color: 'bg-emerald-800' },
  ];

  const counts = buckets.map((b) => ({
    ...b,
    count: rmultiples.filter((r) => r >= b.min && r < b.max).length,
  }));
  const maxCount = Math.max(...counts.map((b) => b.count), 1);
  const avgR = rmultiples.reduce((s, v) => s + v, 0) / rmultiples.length;

  return (
    <div className="metric-chip space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">R-Multiple Distribution</h2>
          <InfoTip text="Each trade's P&L expressed as a multiple of its initial risk (1R = entry × SL%). A 2R win means you made 2× what you risked. Positive expectancy requires avg R > 0. Bars right of 0 are wins, left are losses." />
        </div>
        <span className="text-xs text-ink/50">
          Avg R:{' '}
          <span
            className={`tabular-nums font-semibold ${avgR >= 0 ? 'text-emerald-700' : 'text-rose-600'}`}
          >
            {avgR >= 0 ? '+' : ''}
            {avgR.toFixed(2)}R
          </span>
        </span>
      </div>
      <div className="flex items-end gap-2" style={{ height: 80 }}>
        {counts.map(({ label, count, color }) => (
          <div key={label} className="flex flex-1 flex-col items-center gap-1">
            <span className="text-[10px] tabular-nums text-ink/50">{count > 0 ? count : ''}</span>
            <div
              className={`w-full rounded-t ${color}`}
              style={{ height: count > 0 ? `${(count / maxCount) * 56}px` : '2px', opacity: count > 0 ? 1 : 0.15 }}
            />
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        {counts.map(({ label }) => (
          <div key={label} className="flex-1 text-center text-[10px] text-ink/40">
            {label}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Stats row ────────────────────────────────────────────────────────────────

function StatsRow({ stats }: { stats: NtStats | null }) {
  if (!stats) return null;

  const totalPnl = stats.total_realized_net_pnl + (stats.has_live_prices ? stats.unrealized_pnl : 0);

  return (
    <div className="metric-chip grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
      <div>
        <p className="text-xs text-ink/50">Open Positions</p>
        <p className="mt-0.5 font-display text-xl font-bold">{stats.open_count}</p>
        {stats.open_count > 0 && (
          <p className="text-xs text-ink/40">{formatCurrency(stats.total_invested_inr)} invested</p>
        )}
      </div>

      <div>
        <p className="text-xs text-ink/50">Unrealised P&L</p>
        <p className={`mt-0.5 font-display text-xl font-bold ${pnlColor(stats.unrealized_pnl)}`}>
          {stats.has_live_prices
            ? `${pnlSign(stats.unrealized_pnl)}${formatCurrency(stats.unrealized_pnl)}`
            : '—'}
        </p>
        {!stats.has_live_prices && stats.open_count > 0 && (
          <p className="text-xs text-ink/40">updates every 3 min</p>
        )}
      </div>

      <div>
        <p className="text-xs text-ink/50">Realised P&L</p>
        <p
          className={`mt-0.5 font-display text-xl font-bold ${pnlColor(stats.total_realized_net_pnl)}`}
        >
          {stats.closed_count > 0
            ? `${pnlSign(stats.total_realized_net_pnl)}${formatCurrency(stats.total_realized_net_pnl)}`
            : '—'}
        </p>
        {stats.has_live_prices && stats.closed_count > 0 && (
          <p className={`text-xs ${pnlColor(totalPnl)}`}>
            Total {pnlSign(totalPnl)}{formatCurrency(totalPnl)}
          </p>
        )}
      </div>

      <div>
        <p className="text-xs text-ink/50">Win Rate</p>
        <p
          className={`mt-0.5 font-display text-xl font-bold ${
            stats.win_rate_pct != null && stats.win_rate_pct >= 50
              ? 'text-emerald-700'
              : 'text-rose-600'
          }`}
        >
          {stats.win_rate_pct != null ? `${stats.win_rate_pct}%` : '—'}
        </p>
        {stats.closed_count > 0 && (
          <p className="text-xs text-ink/40">
            {stats.win_count}W / {stats.loss_count}L
          </p>
        )}
      </div>

      <div>
        <p className="text-xs text-ink/50">Total Trades</p>
        <p className="mt-0.5 font-display text-xl font-bold">{stats.closed_count}</p>
        {stats.avg_hold_days != null && (
          <p className="text-xs text-ink/40">avg {stats.avg_hold_days}d hold</p>
        )}
      </div>

      <div>
        <p className="text-xs text-ink/50">By Exit</p>
        <div className="mt-1 space-y-0.5">
          {Object.entries(stats.by_exit_reason ?? {}).map(([reason, d]) => (
            <div key={reason} className="flex items-center justify-between gap-2 text-xs">
              <ExitBadge reason={reason as NtPosition['exit_reason']} />
              <span
                className={`tabular-nums font-semibold ${pnlColor(d.total_net_pnl)}`}
              >
                {d.count} ({pnlSign(d.total_net_pnl)}{formatCurrency(d.total_net_pnl)})
              </span>
            </div>
          ))}
          {Object.keys(stats.by_exit_reason ?? {}).length === 0 && (
            <p className="text-xs text-ink/30">no closed trades</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Positions (orders) tab ───────────────────────────────────────────────────

type PosFilter = 'open' | 'closed' | 'all';

function PositionsTab({ positions }: { positions: NtPosition[] }) {
  const [sub, setSub] = useState<PosFilter>('open');
  const filtered = sub === 'all' ? positions : positions.filter((p) => p.status === sub);
  const openCount = positions.filter((p) => p.status === 'open').length;
  const closedCount = positions.filter((p) => p.status === 'closed').length;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1">
        {(
          [
            ['open', `Open (${openCount})`],
            ['closed', `Closed (${closedCount})`],
            ['all', `All (${positions.length})`],
          ] as [PosFilter, string][]
        ).map(([s, label]) => (
          <SubTab key={s} active={sub === s} onClick={() => setSub(s)}>
            {label}
          </SubTab>
        ))}
      </div>

      {filtered.length === 0 ? (
        <Empty msg={`No ${sub === 'all' ? '' : sub + ' '}positions yet.`} />
      ) : (
        <div className="metric-chip overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-black/5 text-xs text-ink/50">
                <th className="px-4 py-3 text-left font-semibold">Symbol</th>
                <th className="px-4 py-3 text-left font-semibold">Signal</th>
                <th className="px-4 py-3 text-right font-semibold">Entry</th>
                {sub !== 'closed' && (
                  <>
                    <th className="px-4 py-3 text-right font-semibold">Current</th>
                    <th className="px-4 py-3 text-right font-semibold">Unreal P&L</th>
                    <th className="px-4 py-3 text-right font-semibold">Trailing SL</th>
                    <th className="px-4 py-3 text-right font-semibold">Target</th>
                  </>
                )}
                {sub !== 'open' && (
                  <>
                    <th className="px-4 py-3 text-right font-semibold">Exit</th>
                    <th className="px-4 py-3 text-right font-semibold">Net P&L</th>
                    <th className="px-4 py-3 text-left font-semibold">Reason</th>
                  </>
                )}
                <th className="px-4 py-3 text-right font-semibold">Qty</th>
                <th className="px-4 py-3 text-right font-semibold">Days</th>
                {sub === 'all' && <th className="px-4 py-3 text-left font-semibold">Status</th>}
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => {
                const unrealPnl =
                  p.current_price != null
                    ? (p.current_price - p.entry_price) * p.qty
                    : null;
                const days = holdDays(p.entry_at, p.exit_at);

                return (
                  <tr
                    key={p._id}
                    className="border-b border-black/5 last:border-0 hover:bg-black/[0.02]"
                  >
                    <td className="px-4 py-3">
                      <span className="font-display font-bold">{p.symbol}</span>
                      <span className="ml-1.5 text-xs text-ink/40">{p.sector}</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-0.5">
                        <SignalBadge signal={p.signal} />
                        <ConfBadge confidence={p.confidence} />
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">{fmtPrice(p.entry_price)}</td>

                    {/* Open-only columns */}
                    {sub !== 'closed' && (
                      <>
                        <td className="px-4 py-3 text-right tabular-nums">
                          {p.current_price != null ? fmtPrice(p.current_price) : '—'}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums font-semibold">
                          {unrealPnl != null ? (
                            <span className={pnlColor(unrealPnl)}>
                              {pnlSign(unrealPnl)}{formatCurrency(unrealPnl)}
                            </span>
                          ) : (
                            <span className="text-ink/30">—</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-rose-600">
                          {fmtPrice(p.trailing_sl)}
                          <span className="ml-0.5 text-[10px] text-ink/40">
                            {(((p.trailing_sl - p.entry_price) / p.entry_price) * 100).toFixed(1)}%
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-emerald-700">
                          {fmtPrice(p.target_price)}
                          <span className="ml-0.5 text-[10px] text-ink/40">
                            +{(((p.target_price - p.entry_price) / p.entry_price) * 100).toFixed(1)}%
                          </span>
                        </td>
                      </>
                    )}

                    {/* Closed-only columns */}
                    {sub !== 'open' && (
                      <>
                        <td className="px-4 py-3 text-right tabular-nums">
                          {p.exit_price ? fmtPrice(p.exit_price) : '—'}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums font-semibold">
                          {p.net_pnl != null ? (
                            <span className={pnlColor(p.net_pnl)}>
                              {pnlSign(p.net_pnl)}{formatCurrency(p.net_pnl)}
                            </span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <ExitBadge reason={p.exit_reason} />
                        </td>
                      </>
                    )}

                    <td className="px-4 py-3 text-right tabular-nums text-ink/60">{p.qty}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-ink/60">{days}d</td>

                    {sub === 'all' && (
                      <td className="px-4 py-3">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                            p.status === 'open'
                              ? 'bg-accent/10 text-accent'
                              : 'bg-black/5 text-ink/50'
                          }`}
                        >
                          {p.status}
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

// ─── Signals tab ──────────────────────────────────────────────────────────────

type SigFilter = 'all' | 'bullish' | 'bearish';
type SigSortKey = 'sector' | 'signal' | 'confidence' | 'magnitude' | 'created_at';
type SortDir = 'asc' | 'desc';

const MAGNITUDE_ORDER: Record<string, number> = { major: 3, moderate: 2, minor: 1 };
const CONFIDENCE_ORDER: Record<string, number> = { high: 3, medium: 2, low: 1 };
const SIG_PAGE_SIZE = 25;
const NEWS_PAGE_SIZE = 20;

function MagnitudeBadge({ magnitude }: { magnitude: NtSignal['magnitude'] }) {
  const map: Record<NtSignal['magnitude'], string> = {
    major: 'bg-green-100 text-green-700',
    moderate: 'bg-yellow-100 text-yellow-700',
    minor: 'bg-gray-100 text-gray-500',
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold capitalize ${map[magnitude]}`}>
      {magnitude}
    </span>
  );
}

function SignalsTab({ signals, positions }: { signals: NtSignal[]; positions: NtPosition[] }) {
  const [filter, setFilter] = useState<SigFilter>('all');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SigSortKey>('created_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [page, setPage] = useState(1);

  // Cross-reference: which signal IDs actually resulted in a position
  const tradedSignalIds = new Set(positions.map((p) => p.signal_id).filter(Boolean));

  function handleSort(key: SigSortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
    setPage(1);
  }

  const afterFilter = filter === 'all' ? signals : signals.filter((s) => s.signal === filter);

  const sorted = [...afterFilter].sort((a, b) => {
    // Signals with actual positions always float to top regardless of sort
    const aTraded = tradedSignalIds.has(a._id);
    const bTraded = tradedSignalIds.has(b._id);
    if (aTraded !== bTraded) return aTraded ? -1 : 1;

    let cmp = 0;
    switch (sortKey) {
      case 'sector':       cmp = a.sector.localeCompare(b.sector); break;
      case 'signal':       cmp = a.signal.localeCompare(b.signal); break;
      case 'confidence':   cmp = (CONFIDENCE_ORDER[a.confidence] ?? 0) - (CONFIDENCE_ORDER[b.confidence] ?? 0); break;
      case 'magnitude':    cmp = (MAGNITUDE_ORDER[a.magnitude] ?? 0) - (MAGNITUDE_ORDER[b.magnitude] ?? 0); break;
      case 'created_at':   cmp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime(); break;
    }
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const totalPages = Math.ceil(sorted.length / SIG_PAGE_SIZE);
  const paginated = sorted.slice((page - 1) * SIG_PAGE_SIZE, page * SIG_PAGE_SIZE);

  const sectorCounts: Record<string, number> = {};
  for (const s of signals) {
    sectorCounts[s.sector] = (sectorCounts[s.sector] ?? 0) + 1;
  }
  const topSectors = Object.entries(sectorCounts).sort((a, b) => b[1] - a[1]).slice(0, 5);

  function SortIcon({ col }: { col: SigSortKey }) {
    if (sortKey !== col) return <span className="ml-1 opacity-20">↕</span>;
    return <span className="ml-1 text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span>;
  }

  function ThSort({ col, children }: { col: SigSortKey; children: React.ReactNode }) {
    return (
      <th
        className="cursor-pointer select-none whitespace-nowrap px-4 py-3 text-left font-semibold hover:text-ink transition-colors"
        onClick={() => handleSort(col)}
      >
        {children}
        <SortIcon col={col} />
      </th>
    );
  }

  return (
    <div className="space-y-3">
      {/* Sector summary chips */}
      {topSectors.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {topSectors.map(([sector, count]) => (
            <span
              key={sector}
              className="rounded-full border border-black/8 bg-panel px-3 py-1 text-xs font-semibold text-ink/70 shadow-sm"
            >
              {sector} <span className="text-ink/40">×{count}</span>
            </span>
          ))}
        </div>
      )}

      {/* Filter + meta */}
      <div className="flex items-center gap-1">
        {(['all', 'bullish', 'bearish'] as SigFilter[]).map((f) => (
          <SubTab key={f} active={filter === f} onClick={() => { setFilter(f); setPage(1); }}>
            {f.charAt(0).toUpperCase() + f.slice(1)}
            {f !== 'all' && (
              <span className="ml-1 text-[10px]">
                ({signals.filter((s) => s.signal === f).length})
              </span>
            )}
          </SubTab>
        ))}
        <span className="ml-auto text-xs text-ink/40">
          {tradedSignalIds.size} traded · {signals.filter((s) => s.acted_on).length} evaluated
        </span>
      </div>

      {sorted.length === 0 ? (
        <Empty msg="No signals match the filter." />
      ) : (
        <div className="metric-chip p-0">
          <div className="max-h-[560px] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-panel">
              <tr className="border-b border-black/5 text-xs text-ink/50">
                <ThSort col="sector">Sector</ThSort>
                <ThSort col="signal">Signal</ThSort>
                <ThSort col="confidence">Confidence</ThSort>
                <th className="px-4 py-3 text-left font-semibold">Stocks</th>
                <ThSort col="magnitude">Magnitude</ThSort>
                <th className="px-4 py-3 text-left font-semibold">Action</th>
                <ThSort col="created_at">Time</ThSort>
              </tr>
            </thead>
            <tbody>
              {paginated.map((s) => (
                <Fragment key={s._id}>
                  <tr
                    className={`cursor-pointer border-b border-black/5 last:border-0 hover:bg-black/[0.02] ${tradedSignalIds.has(s._id) ? 'bg-accent/[0.03]' : ''}`}
                    onClick={() => setExpanded(expanded === s._id ? null : s._id)}
                  >
                    <td className="px-4 py-3 font-semibold">
                      {s.sector}
                      {tradedSignalIds.has(s._id) && (
                        <span className="ml-1.5 text-[9px] font-bold uppercase tracking-wide text-accent">traded</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <SignalBadge signal={s.signal} />
                    </td>
                    <td className="px-4 py-3">
                      <ConfBadge confidence={s.confidence} />
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-display text-xs font-semibold">
                        {s.stocks.slice(0, 3).join(', ')}
                      </span>
                      {s.stocks.length > 3 && (
                        <span className="text-xs text-ink/40"> +{s.stocks.length - 3}</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <MagnitudeBadge magnitude={s.magnitude} />
                    </td>
                    <td className="px-4 py-3">
                      {tradedSignalIds.has(s._id) ? (
                        <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-bold text-accent">
                          Trade opened
                        </span>
                      ) : s.acted_on ? (
                        <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] text-ink/50">
                          Evaluated
                        </span>
                      ) : (
                        <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] text-ink/30">
                          Pending
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-ink/40">{fmtDate(s.created_at)}</td>
                  </tr>
                  {expanded === s._id && (
                    <tr className="border-b border-black/5 bg-black/[0.015]">
                      <td colSpan={7} className="px-4 pb-3 pt-1">
                        <p className="text-xs font-semibold text-ink/50">AI Reasoning</p>
                        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink/70">
                          {s.reasoning || 'No reasoning recorded.'}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {s.stocks.map((sym) => (
                            <span
                              key={sym}
                              className="rounded bg-black/5 px-1.5 py-0.5 font-display text-[10px] font-bold text-ink/60"
                            >
                              {sym}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-1">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="rounded px-2.5 py-1 text-xs font-semibold disabled:opacity-30 hover:bg-black/5"
          >
            ← Prev
          </button>
          <span className="text-xs text-ink/40">
            {page * SIG_PAGE_SIZE - SIG_PAGE_SIZE + 1}–{Math.min(page * SIG_PAGE_SIZE, sorted.length)} of {sorted.length}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="rounded px-2.5 py-1 text-xs font-semibold disabled:opacity-30 hover:bg-black/5"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

// ─── News tab ─────────────────────────────────────────────────────────────────

function NewsTab({ news, signals, newsTotal }: { news: NtNews[]; signals: NtSignal[]; newsTotal: number }) {
  const [page, setPage] = useState(1);

  // Sector analysis derived from signals
  const sectorStats: Record<string, { bullish: number; bearish: number; neutral: number }> = {};
  for (const s of signals) {
    if (!sectorStats[s.sector]) sectorStats[s.sector] = { bullish: 0, bearish: 0, neutral: 0 };
    (sectorStats[s.sector] as Record<string, number>)[s.signal] =
      ((sectorStats[s.sector] as Record<string, number>)[s.signal] ?? 0) + 1;
  }
  const sectorEntries = Object.entries(sectorStats).sort(
    (a, b) => b[1].bullish + b[1].bearish - (a[1].bullish + a[1].bearish),
  );

  const totalPages = Math.ceil(news.length / NEWS_PAGE_SIZE);
  const paginated = news.slice((page - 1) * NEWS_PAGE_SIZE, page * NEWS_PAGE_SIZE);
  const classifiedCount = news.filter((a) => a.classified).length;

  return (
    <div className="space-y-4">
      {/* Sector sentiment analysis */}
      {sectorEntries.length > 0 && (
        <div className="metric-chip space-y-3">
          <h2 className="text-sm font-semibold">Sector Sentiment (from signals)</h2>
          <div className="space-y-2">
            {sectorEntries.map(([sector, counts]) => {
              const total = counts.bullish + counts.bearish + counts.neutral;
              const bullPct = total > 0 ? (counts.bullish / total) * 100 : 0;
              const bearPct = total > 0 ? (counts.bearish / total) * 100 : 0;
              return (
                <div key={sector} className="flex items-center gap-3">
                  <span className="w-28 shrink-0 text-xs font-semibold text-ink/70">{sector}</span>
                  <div className="flex h-2 flex-1 overflow-hidden rounded-full bg-black/5">
                    <div
                      className="bg-emerald-500"
                      style={{ width: `${bullPct}%` }}
                      title={`${counts.bullish} bullish`}
                    />
                    <div
                      className="bg-rose-400"
                      style={{ width: `${bearPct}%` }}
                      title={`${counts.bearish} bearish`}
                    />
                  </div>
                  <div className="flex gap-2 text-[10px] text-ink/50">
                    <span className="text-emerald-700">{counts.bullish}↑</span>
                    <span className="text-rose-600">{counts.bearish}↓</span>
                    {counts.neutral > 0 && <span>{counts.neutral}~</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Raw news */}
      {news.length === 0 ? (
        <Empty msg="No news ingested yet." />
      ) : (
        <>
          <div className="metric-chip space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">
                Ingested News{' '}
                <span className="text-xs font-normal text-ink/40">
                  ({classifiedCount}/{newsTotal} classified)
                </span>
              </h2>
              {totalPages > 1 && (
                <span className="text-xs text-ink/40">
                  {(page - 1) * NEWS_PAGE_SIZE + 1}–{Math.min(page * NEWS_PAGE_SIZE, news.length)} of {newsTotal}
                </span>
              )}
            </div>
            {paginated.map((article) => (
              <div
                key={article._id}
                className="flex items-start gap-3 border-b border-black/5 pb-3 last:border-0 last:pb-0"
              >
                <div className="flex-1 min-w-0">
                  {article.url ? (
                    <a
                      href={article.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium hover:text-accent"
                    >
                      {article.headline}
                    </a>
                  ) : (
                    <p className="text-sm font-medium">{article.headline}</p>
                  )}
                  <p className="mt-0.5 text-xs text-ink/40">
                    {article.source} · {fmtDate(article.ingested_at)}
                  </p>
                </div>
                {article.classified && (
                  <span className="shrink-0 rounded-full bg-accent/10 px-1.5 py-0.5 text-[9px] font-bold text-accent">
                    analysed
                  </span>
                )}
              </div>
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="rounded px-2.5 py-1 text-xs font-semibold disabled:opacity-30 hover:bg-black/5"
              >
                ← Prev
              </button>
              <span className="text-xs text-ink/40">page {page} of {totalPages}</span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="rounded px-2.5 py-1 text-xs font-semibold disabled:opacity-30 hover:bg-black/5"
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Overview tab ─────────────────────────────────────────────────────────────

function OverviewTab({
  positions,
  signals,
  stats,
  pipelineStatus,
  historyRefreshKey,
}: {
  positions: NtPosition[];
  signals: NtSignal[];
  stats: NtStats | null;
  pipelineStatus: PipelineStatus | null;
  historyRefreshKey: number;
}) {
  const open = positions.filter((p) => p.status === 'open');

  return (
    <div className="space-y-5">
      <PipelineStatusPanel pipelineStatus={pipelineStatus} />
      <StatsRow stats={stats} />

      {/* Open positions mini-table */}
      {open.length > 0 ? (
        <div className="metric-chip space-y-2">
          <h2 className="text-sm font-semibold">Open Positions</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-black/5 text-xs text-ink/50">
                  <th className="py-2 text-left font-semibold">Symbol</th>
                  <th className="py-2 text-right font-semibold">Entry</th>
                  <th className="py-2 text-right font-semibold">Current</th>
                  <th className="py-2 text-right font-semibold">P&L</th>
                  <th className="py-2 text-right font-semibold">SL</th>
                  <th className="py-2 text-right font-semibold">Target</th>
                </tr>
              </thead>
              <tbody>
                {open.map((p) => {
                  const unrealPnl =
                    p.current_price != null
                      ? (p.current_price - p.entry_price) * p.qty
                      : null;
                  return (
                    <tr key={p._id} className="border-b border-black/5 last:border-0">
                      <td className="py-2">
                        <span className="font-display font-bold">{p.symbol}</span>
                        <SignalBadge signal={p.signal} />
                      </td>
                      <td className="py-2 text-right tabular-nums">{fmtPrice(p.entry_price)}</td>
                      <td className="py-2 text-right tabular-nums">
                        {p.current_price != null ? fmtPrice(p.current_price) : '—'}
                      </td>
                      <td className="py-2 text-right tabular-nums font-semibold">
                        {unrealPnl != null ? (
                          <span className={pnlColor(unrealPnl)}>
                            {pnlSign(unrealPnl)}{formatCurrency(unrealPnl)}
                          </span>
                        ) : '—'}
                      </td>
                      <td className="py-2 text-right tabular-nums text-rose-600">
                        {fmtPrice(p.trailing_sl)}
                      </td>
                      <td className="py-2 text-right tabular-nums text-emerald-700">
                        {fmtPrice(p.target_price)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="metric-chip py-6 text-center text-sm text-ink/40">
          No open positions — signals are checked every 5 minutes during market hours.
        </div>
      )}

      <SignalFunnelPanel signals={signals} positions={positions} />
      {stats && <EquityCurvePanel stats={stats} />}
      {stats && <PnlAttributionPanel stats={stats} />}
      <RiskMetricsPanel positions={positions} />
      <ConfidenceTierPanel positions={positions} />
      <SectorPnlPanel positions={positions} />
      <RMultiplePanel positions={positions} />
      <PipelineHistory refreshKey={historyRefreshKey} />

      {positions.length === 0 && signals.length === 0 && (
        <Empty msg="No data yet — the ingester runs every 5 min during market hours (09:00–15:35 IST)." />
      )}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

type TabId = 'overview' | 'positions' | 'signals' | 'news';

export default function TradingPage() {
  const [tab, setTab] = useState<TabId>('overview');

  const [positions, setPositions] = useState<NtPosition[]>([]);
  const [signals, setSignals] = useState<NtSignal[]>([]);
  const [signalsTotal, setSignalsTotal] = useState(0);
  const [news, setNews] = useState<NtNews[]>([]);
  const [newsTotal, setNewsTotal] = useState(0);
  const [stats, setStats] = useState<NtStats | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [triggerMsg, setTriggerMsg] = useState<{ text: string; kind: 'info' | 'success' | 'error' } | null>(null);
  const triggerMsgTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setTriggerMsgWithAutoDismiss = (msg: { text: string; kind: 'info' | 'success' | 'error' } | null) => {
    if (triggerMsgTimerRef.current) clearTimeout(triggerMsgTimerRef.current);
    setTriggerMsg(msg);
    if (msg) {
      triggerMsgTimerRef.current = setTimeout(() => setTriggerMsg(null), 5000);
    }
  };

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const statusPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadPipelineStatus = useCallback(async () => {
    try {
      const s = await fetchPipelineStatus();
      setPipelineStatus(s);
      return s;
    } catch {
      return null;
    }
  }, []);

  const loadAll = useCallback(async () => {
    try {
      const [pRes, sRes, nRes, stRes] = await Promise.allSettled([
        fetchPositions('all'),
        fetchSignals(100),
        fetchNews(50),
        fetchStats(),
      ]);
      if (pRes.status === 'fulfilled') setPositions(pRes.value.positions ?? []);
      if (sRes.status === 'fulfilled') { setSignals(sRes.value.signals ?? []); setSignalsTotal(sRes.value.count ?? 0); }
      if (nRes.status === 'fulfilled') { setNews(nRes.value.articles ?? []); setNewsTotal(nRes.value.count ?? 0); }
      if (stRes.status === 'fulfilled') setStats(stRes.value);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, []);

  // Start/stop fast status polling when a run is active
  const startStatusPoll = useCallback(() => {
    if (statusPollRef.current) return;
    statusPollRef.current = setInterval(async () => {
      const s = await loadPipelineStatus();
      if (!s?.is_active) {
        clearInterval(statusPollRef.current!);
        statusPollRef.current = null;
        void loadAll(); // refresh data once run completes
      }
    }, 15_000);
  }, [loadPipelineStatus, loadAll]);

  useEffect(() => {
    void Promise.all([loadAll(), loadPipelineStatus().then(s => {
      if (s?.is_active) startStatusPoll();
    })]);
    // Slow poll every 3 min for sl_monitor price updates
    pollRef.current = setInterval(() => void loadAll(), 3 * 60 * 1000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (statusPollRef.current) clearInterval(statusPollRef.current);
    };
  }, [loadAll, loadPipelineStatus, startStatusPoll]);

  async function handleTrigger() {
    setTriggering(true);
    setTriggerMsgWithAutoDismiss(null);
    try {
      const res = await triggerPipeline();
      if (res.status === 'local_dev') {
        setTriggerMsgWithAutoDismiss({
          text: res.message ?? 'Run `pnpm nt:ingester` from apps/signal-engine/ to invoke locally.',
          kind: 'info',
        });
      } else if (res.status === 'skipped') {
        setTriggerMsgWithAutoDismiss({
          text: res.message ?? 'Skipped — outside market hours.',
          kind: 'info',
        });
      } else {
        setTriggerMsgWithAutoDismiss({ text: 'Pipeline started — tracking progress below.', kind: 'success' });
        await loadPipelineStatus();
        startStatusPoll();
      }
    } catch (err) {
      setTriggerMsgWithAutoDismiss({
        text: err instanceof Error ? err.message : 'Failed to trigger pipeline',
        kind: 'error',
      });
    } finally {
      setTriggering(false);
    }
  }

  const openPositions = positions.filter((p) => p.status === 'open');
  const pipelineActive = pipelineStatus?.is_active ?? false;

  return (
    <div className="space-y-4">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight">News Trader</h1>
          <p className="text-sm text-ink/60">
            Paper · NSE/BSE · Event-driven · Trailing SL · AI classifier
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              void loadAll();
              void loadPipelineStatus().then((s) => {
                if (s?.is_active) startStatusPoll();
              });
              setHistoryRefreshKey((k) => k + 1);
            }}
            className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-semibold text-gray-700 shadow-sm transition hover:bg-gray-50"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="inline-block h-4 w-4 mr-1.5 -mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
          <button
            onClick={() => void handleTrigger()}
            disabled={triggering || pipelineActive}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white transition hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? 'Starting…' : pipelineActive ? '⏳ Running…' : '▶ Run Pipeline'}
          </button>
        </div>
      </div>

      {/* Trigger feedback */}
      {triggerMsg && (
        <div
          className={`flex items-center justify-between rounded-xl border p-3 text-sm ${
            triggerMsg.kind === 'success'
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : triggerMsg.kind === 'info'
                ? 'border-amber-200 bg-amber-50 text-amber-800'
                : 'border-rose-200 bg-rose-50 text-rose-700'
          }`}
        >
          <span>{triggerMsg.text}</span>
          <button
            onClick={() => setTriggerMsgWithAutoDismiss(null)}
            className="ml-3 shrink-0 opacity-60 hover:opacity-100"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* ── Tab bar ────────────────────────────────────────────────────── */}
      <div className="flex w-fit items-center gap-1 rounded-full bg-panel p-1 shadow-card">
        <TabBtn active={tab === 'overview'} onClick={() => setTab('overview')}>
          Overview
        </TabBtn>
        <TabBtn
          active={tab === 'positions'}
          onClick={() => setTab('positions')}
          count={openPositions.length}
        >
          Orders
        </TabBtn>
        <TabBtn
          active={tab === 'signals'}
          onClick={() => setTab('signals')}
          count={signalsTotal}
        >
          Signals
        </TabBtn>
        <TabBtn
          active={tab === 'news'}
          onClick={() => setTab('news')}
          count={newsTotal}
        >
          News
        </TabBtn>
      </div>

      {/* ── Tab content ────────────────────────────────────────────────── */}
      {loading ? (
        <div className="metric-chip flex items-center justify-center py-10 text-sm text-ink/40">
          Loading…
        </div>
      ) : (
        <>
          {tab === 'overview' && (
            <OverviewTab
              positions={positions}
              signals={signals}
              stats={stats}
              pipelineStatus={pipelineStatus}
              historyRefreshKey={historyRefreshKey}
            />
          )}
          {tab === 'positions' && <PositionsTab positions={positions} />}
          {tab === 'signals' && <SignalsTab signals={signals} positions={positions} />}
          {tab === 'news' && <NewsTab news={news} signals={signals} newsTotal={newsTotal} />}
        </>
      )}
    </div>
  );
}
