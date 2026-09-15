'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import {
  BreakdownPanel,
  DailyBars,
  DateRangePicker,
  EquityCurve,
  Headline,
  METRICS,
  Note,
  RHistogram,
  inr,
  signedInr,
  toneClass,
  type Metric,
} from '../../../components/momentum-analytics-panels';
import { fetchAnalyticsSources, fetchAnalyticsTrades } from '../../../lib/momentum-api';
import {
  COST_MODELS,
  DIMENSIONS,
  MIN_READABLE_TRADES,
  applyFilters,
  breakdown,
  byDay,
  equityCurve,
  inDateRange,
  istParts,
  maxDrawdown,
  netUnder,
  rDistribution,
  resolveRange,
  streaks,
  summarise,
  tradeDateSpan,
  tradingDayCount,
  type AnalyticsTrade,
  type CostModel,
  type DateBounds,
  type Filter,
  type RangePreset,
  type TradeSource,
} from '../../../lib/momentum-analytics';

/** Dimensions shown in the top grid; the rest sit under "more breakdowns". */
const HEADLINE_DIMENSIONS = ['time', 'weekday', 'price', 'dayChg', 'rvol', 'hold'];

export default function MomentumAnalyticsPage() {
  const [sources, setSources] = useState<TradeSource[]>([]);
  const [sourceId, setSourceId] = useState<string>('live');
  const [trades, setTrades] = useState<AnalyticsTrade[]>([]);
  const [costModel, setCostModel] = useState<CostModel>('recorded');
  const [metric, setMetric] = useState<Metric>('netPerTrade');
  const [filters, setFilters] = useState<Filter[]>([]);
  const [preset, setPreset] = useState<RangePreset>('all');
  const [custom, setCustom] = useState<Partial<DateBounds>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    fetchAnalyticsSources()
      .then(({ sources: next }) => {
        setSources(next);
        // Open on whichever source actually has trades to look at.
        const richest = [...next].sort((a, b) => b.trades - a.trades)[0];
        if (richest && (next.find((s) => s.id === 'live')?.trades ?? 0) < MIN_READABLE_TRADES) {
          setSourceId(richest.id);
        }
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Could not load trade sources'));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setFilters([]);
    setPreset('all');
    setCustom({});
    fetchAnalyticsTrades(sourceId)
      .then(({ trades: next }) => {
        if (!cancelled) setTrades(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load trades');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sourceId]);

  const source = sources.find((item) => item.id === sourceId);
  const span = useMemo(() => tradeDateSpan(trades), [trades]);
  const bounds = useMemo(() => resolveRange(trades, preset, custom), [trades, preset, custom]);
  // The date window narrows the set first; the bucket filters then slice what is left.
  const inWindow = useMemo(() => inDateRange(trades, bounds), [trades, bounds]);
  const filtered = useMemo(() => applyFilters(inWindow, filters), [inWindow, filters]);

  const stats = useMemo(() => summarise(filtered, costModel), [filtered, costModel]);
  const drawdown = useMemo(() => maxDrawdown(filtered, costModel), [filtered, costModel]);
  const streak = useMemo(() => streaks(filtered, costModel), [filtered, costModel]);
  const curve = useMemo(() => equityCurve(filtered, costModel), [filtered, costModel]);
  const days = useMemo(() => byDay(filtered, costModel), [filtered, costModel]);
  const rBins = useMemo(() => rDistribution(filtered), [filtered]);
  const breakdowns = useMemo(
    () => DIMENSIONS.map((dimension) => breakdown(filtered, dimension, costModel)),
    [filtered, costModel],
  );

  const extremes = useMemo(() => {
    const ranked = [...filtered].sort((a, b) => netUnder(b, costModel) - netUnder(a, costModel));
    return { best: ranked.slice(0, 5), worst: ranked.slice(-5).reverse() };
  }, [filtered, costModel]);

  function togglePick(dimensionId: string, key: string) {
    setFilters((current) => {
      const existing = current.find((f) => f.dimensionId === dimensionId);
      if (existing?.key === key) return current.filter((f) => f.dimensionId !== dimensionId);
      return [...current.filter((f) => f.dimensionId !== dimensionId), { dimensionId, key }];
    });
  }

  const isBacktest = source?.kind === 'backtest';
  const shown = showAll ? breakdowns : breakdowns.filter((b) => HEADLINE_DIMENSIONS.includes(b.dimension.id));

  return (
    <div className="space-y-5">
      {/* Header */}
      <section className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-accent">Trade journal</p>
          <h1 className="font-display text-3xl tracking-tight">Momentum analytics</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink/65">
            Slice every closed trade by when you took it, what you paid for it, and how it behaved. Click any row to
            filter the whole page by that bucket.
          </p>
        </div>
        <Link
          href="/momentum"
          className="rounded-full border border-black/10 px-3 py-1.5 text-xs font-semibold hover:bg-black/5"
        >
          ← Trade-by-trade review
        </Link>
      </section>

      {/* Controls */}
      <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div>
            <label htmlFor="source" className="text-[11px] font-semibold uppercase tracking-wide text-ink/50">
              Trade set
            </label>
            <select
              id="source"
              value={sourceId}
              onChange={(event) => setSourceId(event.target.value)}
              className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm"
            >
              {sources.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.kind === 'backtest' ? 'BACKTEST · ' : ''}
                  {item.label} ({item.trades.toLocaleString('en-IN')} trades)
                </option>
              ))}
            </select>
            {source && (
              <p className="mt-1 text-[11px] leading-snug text-ink/50">
                {source.from ? `${String(source.from).slice(0, 10)} → ${String(source.to).slice(0, 10)}` : 'no trades yet'}
                {isBacktest
                  ? ' · simulated on historical bars, not money you traded'
                  : ' · paper trades recorded live by the scanner'}
              </p>
            )}
          </div>

          <div>
            <span className="text-[11px] font-semibold uppercase tracking-wide text-ink/50">Cost model</span>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {COST_MODELS.map((model) => (
                <button
                  key={model.id}
                  type="button"
                  onClick={() => setCostModel(model.id)}
                  title={model.hint}
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                    costModel === model.id ? 'bg-accent text-white' : 'border border-black/10 hover:bg-black/5'
                  }`}
                >
                  {model.label}
                </button>
              ))}
            </div>
            <p className="mt-1 text-[11px] leading-snug text-ink/50">
              {COST_MODELS.find((model) => model.id === costModel)?.hint}
            </p>
          </div>
        </div>

        <div className="mt-4 border-t border-black/5 pt-3">
          <DateRangePicker
            preset={preset}
            bounds={bounds}
            span={span}
            tradingDays={tradingDayCount(inWindow)}
            tradeCount={inWindow.length}
            onPreset={(next) => {
              setPreset(next);
              // Opening the custom pickers pre-filled with the window already on
              // screen means the first click narrows it rather than resetting it.
              if (next === 'custom') setCustom(bounds ?? {});
            }}
            onCustom={(next) => setCustom((current) => ({ ...current, ...next }))}
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-black/5 pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-ink/50">Rank buckets by</span>
          {METRICS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setMetric(item.id)}
              title={item.hint}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                metric === item.id ? 'bg-ink text-white' : 'border border-black/10 hover:bg-black/5'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        {filters.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-black/5 pt-3">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-ink/50">Filtered to</span>
            {filters.map((filter) => (
              <button
                key={`${filter.dimensionId}:${filter.key}`}
                type="button"
                onClick={() => togglePick(filter.dimensionId, filter.key)}
                className="rounded-full bg-accent/10 px-3 py-1 text-xs font-semibold text-accent hover:bg-accent/20"
              >
                {DIMENSIONS.find((d) => d.id === filter.dimensionId)?.label}: {filter.key} ✕
              </button>
            ))}
            <button
              type="button"
              onClick={() => setFilters([])}
              className="text-xs font-semibold text-ink/50 hover:underline"
            >
              clear all
            </button>
            <span className="text-xs text-ink/50">
              {filtered.length} of {inWindow.length} trades in the window
            </span>
          </div>
        )}
      </section>

      {error && <div className="metric-chip border-rose-200 py-6 text-rose-700">{error}</div>}
      {loading && <div className="metric-chip py-12 text-center text-sm text-ink/55">Loading trades…</div>}

      {!loading && !error && trades.length === 0 && (
        <div className="metric-chip space-y-2 py-10 text-center text-sm text-ink/60">
          <p>This trade set has no closed trades yet.</p>
          <p className="text-xs text-ink/50">
            Import a backtest run to populate the dashboard:
            <code className="ml-1 rounded bg-black/5 px-1.5 py-0.5 text-[11px]">
              python research/backtests/import_trades_to_mongo.py --trades … --tag …
            </code>
          </p>
        </div>
      )}

      {!loading && !error && trades.length > 0 && (
        <>
          {/* Sample-size honesty, stated before any number is read */}
          {filtered.length < MIN_READABLE_TRADES && (
            <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <strong>{filtered.length} trade{filtered.length === 1 ? '' : 's'} in this selection.</strong> Below about{' '}
              {MIN_READABLE_TRADES} trades a per-trade average is noise, not a finding — read the individual trades
              rather than the buckets.
            </div>
          )}

          <Headline stats={stats} drawdown={drawdown} streak={streak} />

          <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
            <h2 className="font-display text-lg">Running P&amp;L</h2>
            <Note>
              The gap between the two lines is what execution costs you. Where gross runs flat and net falls, the
              trades themselves were roughly break-even and costs made the loss.
            </Note>
            <div className="mt-3">
              <EquityCurve points={curve} />
            </div>
          </section>

          <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
            <h2 className="font-display text-lg">Result by day</h2>
            <div className="mt-3">
              <DailyBars days={days} />
            </div>
          </section>

          {/* The four questions, plus the rest */}
          <section className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-display text-lg">Breakdowns</h2>
              <button
                type="button"
                onClick={() => setShowAll((value) => !value)}
                className="rounded-full border border-black/10 px-3 py-1 text-xs font-semibold hover:bg-black/5"
              >
                {showAll ? 'Show the main six' : `Show all ${breakdowns.length} breakdowns`}
              </button>
            </div>
            <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
              {shown.map((data) => (
                <BreakdownPanel
                  key={data.dimension.id}
                  data={data}
                  metric={metric}
                  activeKey={filters.find((f) => f.dimensionId === data.dimension.id)?.key}
                  onPick={(key) => togglePick(data.dimension.id, key)}
                />
              ))}
            </div>
          </section>

          <div className="grid gap-3 lg:grid-cols-2">
            <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
              <h2 className="font-display text-lg">Result in units of risk</h2>
              <div className="mt-3">
                <RHistogram bins={rBins} />
              </div>
            </section>

            <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
              <h2 className="font-display text-lg">Biggest winners and losers</h2>
              <Note>The trades doing the most to the total — worth opening individually in the review page.</Note>
              <div className="mt-3 grid gap-4 sm:grid-cols-2">
                <ExtremeList title="Best" trades={extremes.best} costModel={costModel} />
                <ExtremeList title="Worst" trades={extremes.worst} costModel={costModel} />
              </div>
            </section>
          </div>

          <p className="text-xs leading-relaxed text-ink/45">
            {stats.trades.toLocaleString('en-IN')} closed trades · {inr(stats.turnover)} total position value ·{' '}
            {COST_MODELS.find((model) => model.id === costModel)?.label.toLowerCase()} cost model
            {isBacktest && ' · simulated trades, not executed orders'}.
          </p>
        </>
      )}
    </div>
  );
}

function ExtremeList({
  title,
  trades,
  costModel,
}: {
  title: string;
  trades: AnalyticsTrade[];
  costModel: CostModel;
}) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink/50">{title}</p>
      <ul className="mt-1 space-y-1">
        {trades.map((trade) => {
          const net = netUnder(trade, costModel);
          const { date, hour, minute } = istParts(trade.entry_time);
          return (
            <li key={trade._id} className="flex items-baseline justify-between gap-2 text-xs">
              <span className="min-w-0 truncate">
                <span className="font-semibold">{trade.symbol}</span>
                <span className="ml-1 text-ink/45">
                  {date} {String(hour).padStart(2, '0')}:{String(minute).padStart(2, '0')}
                </span>
              </span>
              <span className={`shrink-0 tabular-nums font-semibold ${toneClass(net)}`}>{signedInr(net)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
