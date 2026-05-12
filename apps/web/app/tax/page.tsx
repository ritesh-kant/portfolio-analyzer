"use client";

import { useEffect } from 'react';

import { useTaxStore } from '@/store/tax-store';

export default function TaxPage() {
  const { taxSummary, source, loading, error, loadTaxSummary } = useTaxStore();

  useEffect(() => {
    void loadTaxSummary();
  }, [loadTaxSummary]);

  const ltcgUsed = taxSummary.ltcgExemptionUsed;
  const ltcgLimit = taxSummary.ltcgExemptionLimit;
  const remaining = ltcgLimit - ltcgUsed;
  const progress = ltcgLimit === 0 ? 0 : (ltcgUsed / ltcgLimit) * 100;

  return (
    <div className="space-y-4">
      <section className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-black/5 bg-panel px-3 py-2 text-xs text-ink/70 shadow-card">
        <p>
          Data source: <span className="font-semibold text-ink">{source === 'live' ? 'Live APIs' : 'Fallback mocks'}</span>
        </p>
        <p>{loading ? 'Refreshing...' : error ? 'Live services unavailable, using fallback.' : 'Synchronized'}</p>
      </section>

      <section className="rounded-2xl border border-black/5 bg-panel p-4 shadow-card">
        <h1 className="font-display text-2xl">Tax Harvest Dashboard</h1>
        <p className="text-sm text-ink/70">Deterministic FIFO summary for the current FY</p>

        <div className="mt-4 rounded-xl border border-black/5 bg-white/70 p-3">
          <p className="text-xs uppercase tracking-wide text-ink/60">Tax-Free LTCG Used</p>
          <p className="font-display text-2xl">
            {formatCurrency(ltcgUsed)} / {formatCurrency(ltcgLimit)}
          </p>
          <div className="mt-2 h-2 w-full rounded-full bg-black/10">
            <div className="h-2 rounded-full bg-accentWarm" style={{ width: `${Math.min(progress, 100)}%` }} />
          </div>
          <p className="mt-2 text-sm text-ink/70">Remaining: {formatCurrency(remaining)}</p>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card label="Realized LTCG" value={formatCurrency(taxSummary.realized.ltcg)} />
        <Card label="Realized STCG" value={formatCurrency(taxSummary.realized.stcg)} />
        <Card label="Unrealized Profits" value={formatCurrency(taxSummary.unrealized.profits)} />
        <Card label="Unrealized Losses" value={formatCurrency(taxSummary.unrealized.losses)} />
      </section>

      <section className="rounded-2xl border border-black/5 bg-panel p-4 shadow-card">
        <h2 className="font-display text-xl">Harvestable Losses</h2>
        <div className="mt-3 space-y-2">
          {taxSummary.harvestable.map((row) => (
            <div
              key={row.symbol}
              className="grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded-xl border border-black/5 bg-white/70 px-3 py-2"
            >
              <p className="font-semibold">{row.symbol}</p>
              <p className="text-right text-sm text-ink/70">Qty {row.quantity}</p>
              <p className="text-right font-semibold text-rose-700">-{formatCurrency(row.potentialLoss)}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-chip">
      <p className="text-xs uppercase tracking-wide text-ink/60">{label}</p>
      <p className="font-display text-2xl leading-tight">{value}</p>
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
