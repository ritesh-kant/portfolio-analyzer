'use client';

import { useEffect } from 'react';

import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { HealthScoreCard } from '@/components/health-score-card';
import { ImportCsvCard } from '@/components/import-csv-card';
import { usePortfolioStore } from '@/store/portfolio-store';

const sectorPalette = ['#0f766e', '#c75a1b', '#3b5b92', '#766530', '#5d4f7d'];

export default function DashboardPage() {
  const {
    holdings,
    growthSeries,
    summary,
    benchmark,
    score,
    loading,
    source,
    error,
    loadDashboardData,
  } = usePortfolioStore();

  useEffect(() => {
    void loadDashboardData();
  }, [loadDashboardData]);

  const invested = summary.investedAmount;
  const current = summary.currentValue;
  const pnl = summary.pnl;
  const portfolioReturn = summary.pnlPct;
  const benchmarkReturn = benchmark.benchmarkReturnPct;
  const alpha = benchmark.alphaPct;

  const sectorMap = new Map<string, number>();
  for (const h of holdings) {
    sectorMap.set(h.sector ?? 'Other', (sectorMap.get(h.sector ?? 'Other') ?? 0) + h.currentValue);
  }
  const sectorData = [...sectorMap.entries()].map(([name, value]) => ({ name, value }));

  return (
    <div className="space-y-4">
      {/* Status bar */}
      <section className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-black/5 bg-panel px-3 py-2 text-xs text-ink/70 shadow-card">
        <p>
          Data source:{' '}
          <span className="font-semibold text-ink">
            {source === 'live' ? 'Live APIs' : 'Fallback mocks'}
          </span>
        </p>
        <p>
          {loading
            ? 'Refreshing…'
            : error
              ? 'Live services unavailable — using fallback data.'
              : 'Synchronized'}
        </p>
      </section>

      {/* Hero metrics */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Invested" value={formatCurrency(invested)} subtitle="Total deployed capital" />
        <MetricCard label="Current" value={formatCurrency(current)} subtitle="Mark-to-market value" />
        <MetricCard
          label="P&L"
          value={formatCurrency(pnl)}
          subtitle={portfolioReturn.toFixed(2) + '% total return'}
          tone={pnl >= 0 ? 'good' : 'bad'}
        />
        <MetricCard
          label="Vs Nifty 50"
          value={(alpha >= 0 ? '+' : '') + alpha.toFixed(2) + '%'}
          subtitle={'Benchmark ' + benchmarkReturn.toFixed(2) + '% · Score ' + score.score + '/100'}
          tone={alpha >= 0 ? 'good' : 'bad'}
        />
      </section>

      {/* Charts row */}
      <section className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <div className="rounded-2xl border border-black/5 bg-panel p-4 shadow-card">
          <div className="mb-4">
            <h2 className="font-display text-xl">Portfolio vs Benchmark</h2>
            <p className="text-sm text-ink/70">5-month trend snapshot</p>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer>
              <AreaChart data={growthSeries}>
                <defs>
                  <linearGradient id="portfolioFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#0f766e" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#0f766e" stopOpacity={0.03} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#00000014" />
                <XAxis dataKey="month" />
                <YAxis />
                <Tooltip />
                <Area type="monotone" dataKey="benchmark" stroke="#c75a1b" fill="#c75a1b22" />
                <Area type="monotone" dataKey="portfolio" stroke="#0f766e" fill="url(#portfolioFill)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded-2xl border border-black/5 bg-panel p-4 shadow-card">
          <h2 className="font-display text-xl">Allocation Mix</h2>
          <p className="mb-2 text-sm text-ink/70">Concentration by current value</p>
          <div className="h-48 w-full">
            <ResponsiveContainer>
              <PieChart>
                <Pie data={sectorData} dataKey="value" nameKey="name" innerRadius={46} outerRadius={72}>
                  {sectorData.map((entry, index) => (
                    <Cell key={entry.name} fill={sectorPalette[index % sectorPalette.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="grid grid-cols-2 gap-2 text-xs">
            {sectorData.map((sector, idx) => (
              <li key={sector.name} className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: sectorPalette[idx % sectorPalette.length] }}
                />
                <span className="truncate">{sector.name}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Score + Import row */}
      <section className="grid gap-4 lg:grid-cols-2">
        <HealthScoreCard score={score} />
        <ImportCsvCard onImported={() => void loadDashboardData()} />
      </section>

      {/* Holdings table */}
      <section className="rounded-2xl border border-black/5 bg-panel p-4 shadow-card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-display text-xl">Holdings</h2>
          <p className="text-xs text-ink/70">{holdings.length} positions</p>
        </div>

        <div className="mb-1 hidden grid-cols-[1.2fr_auto_auto_auto] gap-2 px-3 text-xs font-semibold uppercase tracking-wide text-ink/50 sm:grid">
          <span>Symbol</span>
          <span className="text-right">Invested</span>
          <span className="text-right">Current</span>
          <span className="text-right">Return</span>
        </div>

        <div className="space-y-1.5">
          {holdings.map((h, idx) => {
            const linePnl = h.currentValue - h.investedAmount;
            const linePnlPct = h.investedAmount === 0 ? 0 : (linePnl / h.investedAmount) * 100;
            const isMf = h.assetType === 'mf';
            const qtyDisplay = isMf
              ? h.quantity.toFixed(3) + ' units'
              : h.quantity + ' qty · avg ' + formatCurrency(h.averagePrice);

            return (
              <div
                key={h.symbol + '-' + idx}
                className="grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded-xl border border-black/5 bg-white/70 px-3 py-2 sm:grid-cols-[1fr_auto_auto_auto]"
              >
                <div className="min-w-0">
                  <p className="truncate font-semibold leading-snug" title={h.symbol}>
                    {h.symbol}
                  </p>
                  <p className="truncate text-xs text-ink/60">
                    {qtyDisplay}
                    {h.sector ? (
                      <span className="ml-1.5 rounded bg-black/5 px-1 py-0.5 text-[10px] font-medium">
                        {h.sector}
                      </span>
                    ) : null}
                    {h.broker ? (
                      <span className="ml-1 rounded bg-black/5 px-1 py-0.5 text-[10px] font-medium uppercase">
                        {h.broker}
                      </span>
                    ) : null}
                  </p>
                </div>
                <div className="hidden text-right sm:block">
                  <p className="text-xs text-ink/60">Invested</p>
                  <p className="text-sm font-medium">{formatCurrency(h.investedAmount)}</p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-ink/60">Current</p>
                  <p className="font-semibold">{formatCurrency(h.currentValue)}</p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-ink/60">Return</p>
                  <p className={linePnl >= 0 ? 'font-semibold text-emerald-700' : 'font-semibold text-rose-700'}>
                    {(linePnlPct >= 0 ? '+' : '') + linePnlPct.toFixed(2) + '%'}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function MetricCard({
  label,
  value,
  subtitle,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  subtitle: string;
  tone?: 'neutral' | 'good' | 'bad';
}) {
  const toneClass =
    tone === 'good'
      ? 'border-emerald-700/20 bg-emerald-50'
      : tone === 'bad'
        ? 'border-rose-700/20 bg-rose-50'
        : 'border-black/5 bg-panel';

  return (
    <div className={'metric-chip ' + toneClass}>
      <p className="text-xs uppercase tracking-wide text-ink/60">{label}</p>
      <p className="font-display text-2xl leading-tight">{value}</p>
      <p className="text-xs text-ink/70">{subtitle}</p>
    </div>
  );
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(value);
}
