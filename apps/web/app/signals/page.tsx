'use client';

import { useState, useEffect } from 'react';
import type {
  SignalResponse,
  SettingsResponse,
  NewsArticle,
  TechnicalsResponse,
  MacroIndicator,
} from '../../lib/signals-api';
import {
  analyzeSignal,
  fetchSignalsSettings,
  fetchSignalsNews,
  fetchSignalsTechnicals,
  fetchSignalsMacro,
} from '../../lib/signals-api';
import { ModelBadge } from '../../components/signals/ModelBadge';
import { SignalCard } from '../../components/signals/SignalCard';
import { ConfluenceGrid } from '../../components/signals/ConfluenceGrid';
import { TechnicalsPanel } from '../../components/signals/TechnicalsPanel';
import { NewsPanel } from '../../components/signals/NewsPanel';
import { MacroPanel } from '../../components/signals/MacroPanel';

export default function SignalsPage() {
  const [symbol, setSymbol] = useState('');
  const [exchange, setExchange] = useState<'NSE' | 'BSE'>('NSE');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [signal, setSignal] = useState<SignalResponse | null>(null);

  // Side-panel data (pre-fetched independently for display)
  const [news, setNews] = useState<NewsArticle[]>([]);
  const [technicals, setTechnicals] = useState<TechnicalsResponse | null>(null);
  const [macro, setMacro] = useState<MacroIndicator[]>([]);

  useEffect(() => {
    void fetchSignalsSettings()
      .then(setSettings)
      .catch(() => null);

    void fetchSignalsMacro()
      .then((r) => setMacro(r.indicators))
      .catch(() => null);
  }, []);

  async function handleAnalyze(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol.trim()) return;

    setLoading(true);
    setError(null);
    setSignal(null);
    setTechnicals(null);
    setNews([]);

    // Fire analyze + technicals + news in parallel
    const [signalResult, techResult, newsResult] = await Promise.allSettled([
      analyzeSignal({ symbol: symbol.trim().toUpperCase(), exchange }),
      fetchSignalsTechnicals(symbol.trim().toUpperCase(), exchange),
      fetchSignalsNews(symbol.trim().toUpperCase()),
    ]);

    if (signalResult.status === 'fulfilled') {
      setSignal(signalResult.value);
    } else {
      setError((signalResult.reason as Error).message ?? 'Analysis failed');
    }

    if (techResult.status === 'fulfilled') {
      setTechnicals(techResult.value);
    }

    if (newsResult.status === 'fulfilled') {
      setNews(newsResult.value.articles);
    }

    setLoading(false);
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight">Signal Engine</h1>
          <p className="text-sm text-ink/60">AI-powered confluence analysis for Indian equities</p>
        </div>
        <ModelBadge settings={settings} />
      </div>

      {/* Search form */}
      <form
        onSubmit={(e) => void handleAnalyze(e)}
        className="metric-chip flex flex-col gap-3 sm:flex-row sm:items-end"
      >
        <div className="flex-1 space-y-1">
          <label className="text-xs font-semibold text-ink/60">Stock Symbol</label>
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="e.g. RELIANCE, TCS, INFY"
            className="w-full rounded-lg border border-black/10 bg-bg px-3 py-2 text-sm font-medium outline-none focus:border-accent focus:ring-1 focus:ring-accent"
            disabled={loading}
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-semibold text-ink/60">Exchange </label>
          <select
            value={exchange}
            onChange={(e) => setExchange(e.target.value as 'NSE' | 'BSE')}
            className="rounded-lg border border-black/10 bg-bg px-3 py-2 text-sm font-medium outline-none focus:border-accent"
            disabled={loading}
          >
            <option value="NSE">NSE</option>
            <option value="BSE">BSE</option>
          </select>
        </div>
        <button
          type="submit"
          disabled={loading || !symbol.trim()}
          className="rounded-lg bg-accent px-5 py-2 text-sm font-semibold text-white transition hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? 'Analysing…' : 'Analyse'}
        </button>
      </form>

      {/* Loading state */}
      {loading && (
        <div className="metric-chip animate-pulse space-y-3">
          <div className="h-4 w-1/3 rounded bg-black/10" />
          <div className="h-8 w-full rounded bg-black/10" />
          <div className="h-4 w-2/3 rounded bg-black/10" />
          <p className="text-center text-xs text-ink/40">
            Fetching signals and calling AI… this may take up to 60 seconds.
          </p>
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Results */}
      {signal && !loading && (
        <div className="space-y-4">
          <SignalCard signal={signal} />
          <ConfluenceGrid scores={signal.confluenceScore} />
          <TechnicalsPanel data={technicals} />
          <NewsPanel articles={news} />
        </div>
      )}

      {/* Macro panel — always visible */}
      {macro.length > 0 && <MacroPanel indicators={macro} />}
    </div>
  );
}
