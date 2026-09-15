'use client';

import { useMemo, useState } from 'react';

import {
  MIN_READABLE_TRADES,
  RANGE_PRESETS,
  type Bucket,
  type Breakdown,
  type DateBounds,
  type DayPoint,
  type EquityPoint,
  type RangePreset,
  type Stats,
} from '../lib/momentum-analytics';

// ── formatting ───────────────────────────────────────────────────────────────

export const inr = (value: number, decimals = 0) =>
  `${value < 0 ? '−' : ''}₹${Math.abs(value).toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;

export const signedInr = (value: number, decimals = 0) =>
  `${value > 0 ? '+' : ''}${inr(value, decimals)}`;

export const pct = (value: number, decimals = 1) => `${(value * 100).toFixed(decimals)}%`;

export const toneClass = (value: number) =>
  value > 0 ? 'text-emerald-700' : value < 0 ? 'text-rose-600' : 'text-ink/55';

// ── small building blocks ────────────────────────────────────────────────────

export function Kpi({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string;
  tone?: number;
  hint?: string;
}) {
  return (
    <div className="metric-chip">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink/50">{label}</p>
      <p className={`mt-0.5 font-display text-lg leading-tight ${tone === undefined ? '' : toneClass(tone)}`}>
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[11px] leading-snug text-ink/45">{hint}</p>}
    </div>
  );
}

/** A caption that explains a number in plain words rather than in jargon. */
export function Note({ children }: { children: React.ReactNode }) {
  return <p className="text-xs leading-relaxed text-ink/55">{children}</p>;
}

// ── date range ───────────────────────────────────────────────────────────────

/**
 * Preset and custom date windows.
 *
 * The presets are anchored to the most recent trade in the set rather than to
 * today, so they mean the same thing on a 2022 backtest as on this morning's
 * paper trades. The resolved dates are always printed underneath — "7 days"
 * over a weekend is fewer sessions than over midweek, and the reader should be
 * able to see that rather than infer it.
 */
export function DateRangePicker({
  preset,
  bounds,
  span,
  tradingDays,
  tradeCount,
  onPreset,
  onCustom,
}: {
  preset: RangePreset;
  bounds: DateBounds | null;
  span: DateBounds | null;
  tradingDays: number;
  tradeCount: number;
  onPreset: (preset: RangePreset) => void;
  onCustom: (bounds: Partial<DateBounds>) => void;
}) {
  return (
    <div>
      <span className="text-[11px] font-semibold uppercase tracking-wide text-ink/50">Date range</span>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {RANGE_PRESETS.map((option) => (
          <button
            key={option.id}
            type="button"
            onClick={() => onPreset(option.id)}
            title={option.hint}
            className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
              preset === option.id ? 'bg-accentWarm text-white' : 'border border-black/10 hover:bg-black/5'
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {preset === 'custom' && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-ink/60">
            From
            <input
              type="date"
              value={bounds?.from ?? ''}
              min={span?.from}
              max={span?.to}
              onChange={(event) => onCustom({ from: event.target.value })}
              className="rounded-lg border border-black/10 bg-white px-2 py-1 text-xs"
            />
          </label>
          <label className="flex items-center gap-1.5 text-xs text-ink/60">
            To
            <input
              type="date"
              value={bounds?.to ?? ''}
              min={span?.from}
              max={span?.to}
              onChange={(event) => onCustom({ to: event.target.value })}
              className="rounded-lg border border-black/10 bg-white px-2 py-1 text-xs"
            />
          </label>
          {span && (
            <span className="text-[11px] text-ink/45">
              available {span.from} → {span.to}
            </span>
          )}
        </div>
      )}

      <p className="mt-1 text-[11px] leading-snug text-ink/50">
        {bounds === null ? (
          'No trades to date-filter.'
        ) : (
          <>
            {bounds.from === bounds.to ? bounds.from : `${bounds.from} → ${bounds.to}`} ·{' '}
            {tradingDays} trading day{tradingDays === 1 ? '' : 's'} · {tradeCount.toLocaleString('en-IN')} trade
            {tradeCount === 1 ? '' : 's'}
          </>
        )}
      </p>
    </div>
  );
}

// ── headline ─────────────────────────────────────────────────────────────────

export function Headline({
  stats,
  drawdown,
  streak,
}: {
  stats: Stats;
  drawdown: number;
  streak: { longestWin: number; longestLoss: number };
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
      <Kpi
        label="Net P&L"
        value={signedInr(stats.net)}
        tone={stats.net}
        hint={`${signedInr(stats.netPerTrade)} per trade · ${stats.netPctPerTrade.toFixed(3)}% of size`}
      />
      <Kpi
        label="Gross P&L"
        value={signedInr(stats.gross)}
        tone={stats.gross}
        hint={`${signedInr(stats.grossPerTrade)} per trade · ${stats.grossPctPerTrade.toFixed(3)}% of size`}
      />
      <Kpi
        label="Costs"
        value={inr(stats.costs)}
        hint={`${inr(stats.trades ? stats.costs / stats.trades : 0)} per trade`}
      />
      <Kpi
        label="Trades"
        value={stats.trades.toLocaleString('en-IN')}
        hint={`${stats.wins} won · ${stats.losses} lost${stats.scratches ? ` · ${stats.scratches} flat` : ''}`}
      />
      <Kpi
        label="Win rate"
        value={pct(stats.winRate)}
        hint={`${pct(stats.grossWinRate)} before costs`}
      />
      <Kpi
        label="Profit factor"
        value={stats.profitFactor === null ? '—' : stats.profitFactor.toFixed(2)}
        tone={stats.profitFactor === null ? undefined : stats.profitFactor - 1}
        hint="₹ won for every ₹1 lost"
      />
      <Kpi label="Average win" value={signedInr(stats.avgWin)} tone={1} />
      <Kpi label="Average loss" value={signedInr(-stats.avgLoss)} tone={-1} />
      <Kpi
        label="Win / loss size"
        value={stats.avgLoss > 0 ? `${(stats.avgWin / stats.avgLoss).toFixed(2)}×` : '—'}
        hint="a winner is this many times a loser"
      />
      <Kpi label="Worst drawdown" value={inr(drawdown)} hint="biggest fall from a running high" />
      <Kpi
        label="Longest streak"
        value={`${streak.longestWin}W / ${streak.longestLoss}L`}
        hint="consecutive winners, then losers"
      />
      <Kpi
        label="Average hold"
        value={stats.avgHoldMinutes === null ? '—' : `${Math.round(stats.avgHoldMinutes)} min`}
        hint={stats.avgR === null ? undefined : `${stats.avgR >= 0 ? '+' : ''}${stats.avgR.toFixed(2)}R average result`}
      />
    </div>
  );
}

// ── breakdown table ──────────────────────────────────────────────────────────

type Metric = 'netPerTrade' | 'grossPerTrade' | 'net' | 'winRate';

const METRICS: { id: Metric; label: string; hint: string }[] = [
  { id: 'netPerTrade', label: 'Net ₹/trade', hint: 'what a trade in this bucket was worth after costs' },
  { id: 'grossPerTrade', label: 'Gross ₹/trade', hint: 'the price move alone, before costs' },
  { id: 'net', label: 'Total net ₹', hint: 'everything this bucket contributed' },
  { id: 'winRate', label: 'Win rate', hint: 'share of trades that finished green after costs' },
];

function metricValue(stats: Stats, metric: Metric): number {
  return metric === 'winRate' ? stats.winRate : stats[metric];
}

function metricText(stats: Stats, metric: Metric): string {
  return metric === 'winRate' ? pct(stats.winRate) : signedInr(metricValue(stats, metric));
}

/**
 * One dimension as a ranked bar table.
 *
 * Buckets below `MIN_READABLE_TRADES` are dimmed and flagged: with a handful of
 * trades the per-trade figure is noise, and the point of this page is to stop a
 * three-trade bucket reading like a rule.
 */
export function BreakdownPanel({
  data,
  metric,
  activeKey,
  onPick,
}: {
  data: Breakdown;
  metric: Metric;
  activeKey?: string;
  onPick: (key: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { dimension, buckets, unrecorded } = data;

  const visible = expanded || buckets.length <= 8 ? buckets : buckets.slice(0, 8);
  const scale = useMemo(
    () => Math.max(...buckets.map((b) => Math.abs(metricValue(b.stats, metric))), 1e-9),
    [buckets, metric],
  );

  if (buckets.length === 0) {
    return (
      <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
        <h3 className="font-display text-base">{dimension.label}</h3>
        <p className="mt-2 text-xs text-ink/50">
          No trade in this selection recorded {dimension.label.toLowerCase()}.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
      <header className="mb-3">
        <h3 className="font-display text-base leading-tight">{dimension.label}</h3>
        <p className="mt-0.5 text-xs text-ink/55">{dimension.question}</p>
      </header>

      <table className="w-full text-xs">
        <thead>
          <tr className="text-[10px] uppercase tracking-wide text-ink/45">
            <th className="pb-1 text-left font-semibold">Bucket</th>
            <th className="pb-1 text-right font-semibold">n</th>
            <th className="pb-1 text-right font-semibold">Win</th>
            <th className="pb-1 pl-2 text-left font-semibold">{METRICS.find((m) => m.id === metric)?.label}</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((bucket) => (
            <BreakdownRow
              key={bucket.key}
              bucket={bucket}
              metric={metric}
              scale={scale}
              active={bucket.key === activeKey}
              onPick={() => onPick(bucket.key)}
            />
          ))}
        </tbody>
      </table>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink/45">
        {buckets.length > 8 && (
          <button type="button" onClick={() => setExpanded((v) => !v)} className="font-semibold text-accent hover:underline">
            {expanded ? 'Show top 8' : `Show all ${buckets.length}`}
          </button>
        )}
        {unrecorded > 0 && <span>{unrecorded} trade{unrecorded === 1 ? '' : 's'} never recorded this field</span>}
      </div>
    </section>
  );
}

function BreakdownRow({
  bucket,
  metric,
  scale,
  active,
  onPick,
}: {
  bucket: Bucket;
  metric: Metric;
  scale: number;
  active: boolean;
  onPick: () => void;
}) {
  const value = metricValue(bucket.stats, metric);
  const thin = bucket.stats.trades < MIN_READABLE_TRADES;
  const width = `${Math.min((Math.abs(value) / scale) * 100, 100)}%`;
  const positive = metric === 'winRate' ? value >= 0.5 : value >= 0;

  return (
    <tr
      onClick={onPick}
      className={`cursor-pointer border-t border-black/5 transition hover:bg-accent/5 ${active ? 'bg-accent/10' : ''}`}
      title={
        thin
          ? `${bucket.stats.trades} trades — too few to read as a pattern`
          : `${bucket.stats.trades} trades · gross ${signedInr(bucket.stats.grossPerTrade)}/trade · net ${signedInr(bucket.stats.netPerTrade)}/trade`
      }
    >
      <td className={`py-1.5 pr-2 font-medium ${thin ? 'text-ink/40' : ''}`}>
        {bucket.key}
        {thin && <span className="ml-1 text-[9px] font-bold uppercase text-amber-600">thin</span>}
      </td>
      <td className={`py-1.5 text-right tabular-nums ${thin ? 'text-ink/40' : 'text-ink/60'}`}>
        {bucket.stats.trades}
      </td>
      <td className={`py-1.5 text-right tabular-nums ${thin ? 'text-ink/40' : 'text-ink/60'}`}>
        {pct(bucket.stats.winRate, 0)}
      </td>
      <td className="py-1.5 pl-2">
        <div className="flex items-center gap-1.5">
          <div className="relative h-3 min-w-0 flex-1 rounded-sm bg-black/[0.04]">
            <div
              className={`absolute inset-y-0 left-0 rounded-sm ${
                positive ? 'bg-emerald-500' : 'bg-rose-500'
              } ${thin ? 'opacity-30' : 'opacity-80'}`}
              style={{ width }}
            />
          </div>
          <span
            className={`w-20 shrink-0 text-right tabular-nums ${thin ? 'text-ink/40' : toneClass(positive ? 1 : -1)}`}
          >
            {metricText(bucket.stats, metric)}
          </span>
        </div>
      </td>
    </tr>
  );
}

export { METRICS };
export type { Metric };

// ── charts ───────────────────────────────────────────────────────────────────

/**
 * Gross and net running P&L on one pair of axes.
 *
 * Drawn as inline SVG rather than with a chart library so the two lines share
 * an exact scale: the gap between them IS the cost drag, and that reading only
 * works if nothing rescales one line independently.
 */
export function EquityCurve({ points }: { points: EquityPoint[] }) {
  const width = 980;
  const height = 220;
  const pad = { top: 14, right: 12, bottom: 22, left: 64 };

  if (points.length < 2) {
    return <Note>At least two closed trades are needed to draw a curve.</Note>;
  }

  const values = points.flatMap((p) => [p.net, p.gross]).concat(0);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => pad.left + (i / (points.length - 1)) * (width - pad.left - pad.right);
  const y = (v: number) => pad.top + (1 - (v - min) / span) * (height - pad.top - pad.bottom);
  const path = (pick: (p: EquityPoint) => number) =>
    points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(pick(p)).toFixed(1)}`).join(' ');

  // Zero always earns a label; the others are dropped when they would collide
  // with a line already placed, which happens whenever one series barely moves.
  const ticks: number[] = [];
  for (const candidate of [min < 0 && max > 0 ? 0 : max, max, min, min + span / 2]) {
    if (!ticks.some((kept) => Math.abs(y(kept) - y(candidate)) < 14)) ticks.push(candidate);
  }

  return (
    <figure className="overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[220px] w-full min-w-[640px]" role="img"
           aria-label="Running gross and net profit and loss across the selected trades">
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)}
                  stroke={tick === 0 ? 'rgba(23,33,42,0.28)' : 'rgba(23,33,42,0.08)'}
                  strokeDasharray={tick === 0 ? '' : '3 4'} />
            <text x={pad.left - 8} y={y(tick) + 3} textAnchor="end" className="fill-ink/45" fontSize="10">
              {inr(tick)}
            </text>
          </g>
        ))}
        <path d={path((p) => p.gross)} fill="none" stroke="#0f766e" strokeWidth="1.5" strokeDasharray="4 3" />
        <path d={path((p) => p.net)} fill="none" stroke="#c75a1b" strokeWidth="2" />
        <text x={pad.left} y={height - 6} className="fill-ink/45" fontSize="10">
          {points[0]!.date}
        </text>
        <text x={width - pad.right} y={height - 6} textAnchor="end" className="fill-ink/45" fontSize="10">
          {points[points.length - 1]!.date} · {points.length} trades
        </text>
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-4 text-xs text-ink/60">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5" style={{ background: 'repeating-linear-gradient(90deg,#0f766e 0 4px,transparent 4px 7px)' }} />
          Gross — the price moves alone
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5 bg-[#c75a1b]" />
          Net — after brokerage, taxes and slippage
        </span>
      </figcaption>
    </figure>
  );
}

/** Per-day net result, so a single outlier day cannot hide inside a total. */
export function DailyBars({ days }: { days: DayPoint[] }) {
  const width = 980;
  const height = 150;
  const pad = { top: 10, right: 12, bottom: 18, left: 64 };

  if (days.length === 0) return <Note>No days to show.</Note>;

  const max = Math.max(...days.map((d) => Math.abs(d.net)), 1);
  const zero = pad.top + (height - pad.top - pad.bottom) / 2;
  const half = (height - pad.top - pad.bottom) / 2;
  const slot = (width - pad.left - pad.right) / days.length;
  const barWidth = Math.max(Math.min(slot - 1, 14), 1);
  const green = days.filter((d) => d.net > 0).length;

  return (
    <figure className="overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[150px] w-full min-w-[640px]" role="img"
           aria-label="Net profit and loss for each trading day">
        <line x1={pad.left} x2={width - pad.right} y1={zero} y2={zero} stroke="rgba(23,33,42,0.28)" />
        <text x={pad.left - 8} y={zero + 3} textAnchor="end" className="fill-ink/45" fontSize="10">₹0</text>
        <text x={pad.left - 8} y={pad.top + 6} textAnchor="end" className="fill-ink/45" fontSize="10">{inr(max)}</text>
        <text x={pad.left - 8} y={height - pad.bottom} textAnchor="end" className="fill-ink/45" fontSize="10">{inr(-max)}</text>
        {days.map((day, i) => {
          const magnitude = (Math.abs(day.net) / max) * half;
          return (
            <rect
              key={day.date}
              x={pad.left + i * slot + (slot - barWidth) / 2}
              y={day.net >= 0 ? zero - magnitude : zero}
              width={barWidth}
              height={Math.max(magnitude, 0.6)}
              fill={day.net >= 0 ? '#059669' : '#e11d48'}
              opacity="0.85"
            >
              <title>{`${day.date} · ${day.trades} trade${day.trades === 1 ? '' : 's'} · net ${signedInr(day.net)} · gross ${signedInr(day.gross)}`}</title>
            </rect>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-ink/55">
        {days.length} trading day{days.length === 1 ? '' : 's'} · {green} green, {days.length - green} red
        {' '}({pct(green / days.length, 0)} of days finished up). Hover a bar for that day.
      </figcaption>
    </figure>
  );
}

/** Where results land in units of risk — the shape of the edge, if there is one. */
export function RHistogram({ bins }: { bins: { label: string; count: number; win: boolean }[] }) {
  if (bins.length === 0) return <Note>No trade in this selection recorded a usable stop, so R cannot be measured.</Note>;
  const max = Math.max(...bins.map((b) => b.count), 1);
  const total = bins.reduce((sum, b) => sum + b.count, 0);

  return (
    <div>
      <div className="flex items-end gap-1" style={{ height: 120 }}>
        {bins.map((bin) => (
          <div key={bin.label} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1">
            <span className="text-[10px] tabular-nums text-ink/50">{bin.count || ''}</span>
            <div
              className={`w-full rounded-t-sm ${bin.win ? 'bg-emerald-500/75' : 'bg-rose-500/75'}`}
              style={{ height: `${(bin.count / max) * 92}px` }}
              title={`${bin.label}: ${bin.count} trades (${pct(bin.count / total, 0)})`}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1">
        {bins.map((bin) => (
          <span key={bin.label} className="min-w-0 flex-1 text-center text-[9px] leading-tight text-ink/45">
            {bin.label.replace(' to ', '..').replace(/R$/, '')}
          </span>
        ))}
      </div>
      <Note>
        <span className="mt-2 block">
          1R is the rupees you risked — the gap between your entry and your stop. A trade that hit its 2R target
          shows at +2R; a full stop-out shows at −1R. Anything left of −1R means the exit slipped past the stop.
        </span>
      </Note>
    </div>
  );
}
