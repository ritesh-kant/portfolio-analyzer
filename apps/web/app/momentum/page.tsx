'use client';

import { useEffect, useMemo, useState } from 'react';

import { MomentumTradeChart } from '../../components/momentum-trade-chart';
import { fetchMomentumTrades, type MomentumTrade } from '../../lib/momentum-api';

const IST = 'Asia/Kolkata';
const money = (value: number | undefined) => value === undefined ? '—' : `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const at = (value: string | undefined) => value ? new Date(value).toLocaleTimeString('en-IN', { timeZone: IST, hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
const dateKey = (value: string) => new Intl.DateTimeFormat('en-CA', { timeZone: IST }).format(new Date(value));
const dateLabel = (value: string) => new Date(`${value}T12:00:00+05:30`).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric', timeZone: IST });
const strategyLabel = (value: string | undefined) => ({
  attention_1m: 'Attention control',
  attention_1m_resistance_state: 'Resistance-state',
  attention_1m_false_break_reclaim: 'False-break reclaim',
}[value ?? ''] ?? 'Earlier paper run');

function pnlClass(value: number | undefined) {
  return value === undefined ? 'text-ink/55' : value >= 0 ? 'text-emerald-700' : 'text-rose-600';
}

function TradeRow({ trade, active, onClick }: { trade: MomentumTrade; active: boolean; onClick: () => void }) {
  const net = trade.net_inr;
  return <button onClick={onClick} className={`w-full border-b border-black/5 px-3 py-3 text-left transition last:border-0 hover:bg-accent/5 ${active ? 'bg-accent/10' : ''}`}>
    <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className="font-display font-semibold">{trade.symbol}</span><span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-ink/60">{trade.setup.replaceAll('_', ' ')}</span></div><p className="mt-1 text-xs text-ink/55">{strategyLabel(trade.strategy)} · Buy {money(trade.entry_price)} at {at(trade.entry_time)}{trade.exit_price !== undefined ? ` → Sell ${money(trade.exit_price)} at ${at(trade.exit_time)}` : ' · Open'}</p></div><div className={`text-right text-sm font-bold ${pnlClass(net)}`}>{net === undefined ? 'Open' : `${net >= 0 ? '+' : ''}${money(net)}`}<p className="mt-1 text-[11px] font-medium text-ink/45">{trade.exit_reason?.replaceAll('_', ' ') ?? 'in progress'}</p></div></div>
  </button>;
}

function EntryReason({ trade }: { trade: MomentumTrade }) {
  const attention = trade.setup === 'attention_1m_confirmation';
  const reclaim = trade.setup === 'attention_false_break_reclaim';
  const volumeRatio = trade.setup_meta?.volume_ratio;
  const gates = reclaim
    ? [
        { label: 'Failed first breakout', detail: `The original attention entry exited through its broken level ${money(typeof trade.setup_meta?.reclaim_level === 'number' ? trade.setup_meta.reclaim_level : trade.level ?? trade.trigger_px)}. This arm permits only one retry.` },
        { label: 'Trend still intact', detail: 'EMA 9 remained above EMA 20 and the completed 5-minute close remained above session VWAP.' },
        { label: '1-minute reclaim', detail: `A later green candle closed strongly back above the failed level with at least 2.5× recent one-minute volume${typeof volumeRatio === 'number' ? `; recorded volume was ${volumeRatio.toFixed(2)}×.` : '.'}` },
        { label: 'New buy-stop', detail: `A later quote traded through the reclaim candle high at ${money(trade.trigger_px ?? trade.entry_price)}; the stop was rebuilt from the reclaim structure.` },
      ]
    : attention
    ? [
        { label: 'Attention watchlist', detail: `Day change ${trade.day_chg_pct?.toFixed(2) ?? '—'}% (minimum 1.5%) and RVOL ${trade.rvol?.toFixed(2) ?? '—'}× (minimum 1.5×).` },
        { label: '5-minute trend context', detail: 'EMA 9 was above EMA 20 and the completed 5-minute close was above session VWAP.' },
        { label: '1-minute confirmation', detail: `A green candle closed in its upper 40% with at least 2.5× recent one-minute volume${typeof volumeRatio === 'number' ? `; recorded confirmation volume was ${volumeRatio.toFixed(2)}×.` : '.'}` },
        { label: 'Breakout trigger', detail: `A later quote traded through ${money(trade.trigger_px ?? trade.entry_price)} within the three-minute pending window; paper fill was ${money(trade.entry_price)}.` },
      ]
    : [
        { label: 'Named setup', detail: `${trade.setup.replaceAll('_', ' ')} fired on the completed strategy timeframe.` },
        { label: 'Recorded mover context', detail: `Day change was ${trade.day_chg_pct?.toFixed(2) ?? '—'}% and RVOL was ${trade.rvol?.toFixed(2) ?? '—'}× at signal time.` },
        { label: 'Breakout trigger', detail: `The recorded trigger was ${money(trade.trigger_px ?? trade.entry_price)} and the paper fill was ${money(trade.entry_price)}.` },
      ];
  const strictPatterns = trade.pattern_matches?.map((match) => `${match.name.replaceAll('_', ' ')} · ${match.timeframe}`).join(', ');
  return <section className="rounded-xl border border-accent/15 bg-accent/[0.045] p-4"><div className="flex flex-wrap items-baseline justify-between gap-2"><div><h3 className="font-display text-lg">Why the scanner entered</h3><p className="text-xs text-ink/60">Recorded entry conditions — a checklist of what was required at the time, not an outcome-based explanation.</p></div><span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-bold text-emerald-800">All required gates passed</span></div><ol className="mt-3 space-y-2">{gates.map((gate, index) => <li key={gate.label} className="flex gap-2 text-sm"><span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-bold text-white">{index + 1}</span><span><strong>{gate.label}:</strong> <span className="text-ink/70">{gate.detail}</span></span></li>)}</ol><div className="mt-3 border-t border-accent/15 pt-3 text-sm"><strong>Risk plan:</strong> entry {money(trade.entry_price)}, invalidation stop {money(trade.stop)}, {trade.target ? `target ${money(trade.target)}, ` : ''}{trade.qty} shares. {strictPatterns ? `Strict completed pattern evidence: ${strictPatterns}.` : trade.candle_tags?.length ? `Legacy candle context: ${trade.candle_tags.join(', ')}.` : ''}</div></section>;
}

export default function MomentumPage() {
  const [trades, setTrades] = useState<MomentumTrade[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [openDates, setOpenDates] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchMomentumTrades().then(({ trades: next }) => { setTrades(next); setSelected(next[0]?._id ?? null); setOpenDates(next[0] ? new Set([dateKey(next[0].entry_time)]) : new Set()); }).catch((err: unknown) => setError(err instanceof Error ? err.message : 'Could not load momentum trades')).finally(() => setLoading(false));
  }, []);
  const groups = useMemo(() => {
    const next = new Map<string, MomentumTrade[]>();
    for (const trade of trades) { const key = dateKey(trade.entry_time); next.set(key, [...(next.get(key) ?? []), trade]); }
    return [...next.entries()].sort(([a], [b]) => b.localeCompare(a));
  }, [trades]);
  const trade = trades.find((item) => item._id === selected) ?? null;
  const totalNet = trades.reduce((sum, item) => sum + (item.net_inr ?? 0), 0);
  const strategyTotals = useMemo(() => {
    const totals = new Map<string, { count: number; net: number }>();
    for (const item of trades) {
      const key = item.strategy ?? 'earlier_paper_run';
      const current = totals.get(key) ?? { count: 0, net: 0 };
      totals.set(key, { count: current.count + 1, net: current.net + (item.net_inr ?? 0) });
    }
    // Show a newly deployed arm before its first trade, so the review page
    // makes its paper-only status visible from the first session onward.
    if (!totals.has('attention_1m_false_break_reclaim')) {
      totals.set('attention_1m_false_break_reclaim', { count: 0, net: 0 });
    }
    return [...totals.entries()];
  }, [trades]);
  return <div className="space-y-5">
    <section className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-[0.18em] text-accent">Paper-trade review</p><h1 className="font-display text-3xl tracking-tight">Momentum trades</h1><p className="mt-1 text-sm text-ink/65">Select any trade to review the exact one-minute candles, overlays, and execution points.</p></div><div className="metric-chip text-right"><p className="text-xs text-ink/55">Recorded P&amp;L</p><p className={`font-display text-xl ${pnlClass(totalNet)}`}>{totalNet >= 0 ? '+' : ''}{money(totalNet)}</p></div></section>
    {!loading && !error && strategyTotals.length > 0 && <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{strategyTotals.map(([strategy, summary]) => <div key={strategy} className="metric-chip"><p className="text-xs text-ink/55">{strategyLabel(strategy)}</p><p className={`mt-1 font-display text-xl ${pnlClass(summary.net)}`}>{summary.net >= 0 ? '+' : ''}{money(summary.net)}</p><p className="mt-1 text-xs text-ink/50">{summary.count ? `${summary.count} paper trade${summary.count === 1 ? '' : 's'} · kept separate` : 'Forward paper arm · starts next session'}</p></div>)}</section>}
    {loading && <div className="metric-chip py-12 text-center text-sm text-ink/55">Loading momentum trade history…</div>}
    {error && <div className="metric-chip border-rose-200 py-6 text-rose-700">{error}</div>}
    {!loading && !error && trades.length === 0 && <div className="metric-chip py-12 text-center text-sm text-ink/55">No momentum paper trades have been recorded yet.</div>}
    {!loading && !error && trades.length > 0 && <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]"><aside className="overflow-hidden rounded-xl border border-black/10 bg-panel shadow-card">{groups.map(([date, items]) => { const isOpen = openDates.has(date); return <section key={date} className="border-b border-black/5 last:border-0"><button type="button" onClick={() => setOpenDates((current) => { const next = new Set(current); if (next.has(date)) next.delete(date); else next.add(date); return next; })} className="flex w-full items-center justify-between bg-black/[0.025] px-3 py-2 text-left text-xs font-bold text-ink/60 hover:bg-black/[0.05]" aria-expanded={isOpen}><span>{dateLabel(date)} <span className="ml-1 font-medium">({items.length} trade{items.length === 1 ? '' : 's'})</span></span><span className="text-base leading-none" aria-hidden="true">{isOpen ? '−' : '+'}</span></button>{isOpen && items.map((item) => <TradeRow key={item._id} trade={item} active={item._id === selected} onClick={() => setSelected(item._id)} />)}</section>; })}</aside>
      {trade && <section className="space-y-4"><div className="rounded-xl border border-black/10 bg-panel p-4 shadow-card"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-display text-2xl">{trade.symbol} <span className="text-base font-medium text-ink/55">· {trade.setup.replaceAll('_', ' ')}</span></h2><p className="mt-1 text-sm text-ink/60">{strategyLabel(trade.strategy)} · Entry {money(trade.entry_price)} at {at(trade.entry_time)} · Stop {money(trade.stop)}{trade.target ? ` · Target ${money(trade.target)}` : ''}</p></div><p className={`font-display text-xl ${pnlClass(trade.net_inr)}`}>{trade.net_inr === undefined ? 'Open' : `${trade.net_inr >= 0 ? '+' : ''}${money(trade.net_inr)}`}</p></div><div className="mt-3 flex flex-wrap gap-2 text-xs"><span className="rounded-full bg-accent/10 px-2.5 py-1 text-accent">Day change {trade.day_chg_pct?.toFixed(2) ?? '—'}%</span><span className="rounded-full bg-black/5 px-2.5 py-1">RVOL {trade.rvol?.toFixed(2) ?? '—'}×</span><span className="rounded-full bg-black/5 px-2.5 py-1">Catalyst {trade.catalyst ? 'yes' : 'no'}</span>{trade.candle_tags?.map((tag) => <span key={tag} className="rounded-full bg-black/5 px-2.5 py-1 text-ink/65">legacy: {tag}</span>)}{trade.pattern_matches?.map((match) => <span key={`${match.name}-${match.start}`} className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-800">{match.name.replaceAll('_', ' ')} · {match.timeframe}</span>)}</div></div><EntryReason trade={trade} /><div className="space-y-3"><div><h3 className="font-display text-lg">1-minute execution chart</h3><p className="text-xs text-ink/55">Precise candles and fills at the execution timeframe.</p></div><MomentumTradeChart trade={trade} interval="1m" /><div><h3 className="font-display text-lg">5-minute decision chart</h3><p className="text-xs text-ink/55">The scanner’s EMA, VWAP, and MACD decision timeframe.</p></div><MomentumTradeChart trade={trade} interval="5m" /></div><div className="grid gap-3 sm:grid-cols-3"><div className="metric-chip"><p className="text-xs text-ink/55">Exit</p><p className="mt-1 font-semibold">{trade.exit_price === undefined ? 'Still open' : `${money(trade.exit_price)} · ${trade.exit_reason?.replaceAll('_', ' ')}`}</p></div><div className="metric-chip"><p className="text-xs text-ink/55">Position</p><p className="mt-1 font-semibold">{trade.qty} shares · {money(trade.notional_inr)}</p></div><div className="metric-chip"><p className="text-xs text-ink/55">Costs</p><p className="mt-1 font-semibold">{money(trade.costs_inr)}</p></div></div><p className="text-xs leading-relaxed text-ink/55">Focus either chart, then use <kbd className="rounded bg-black/5 px-1">+</kbd> / <kbd className="rounded bg-black/5 px-1">−</kbd> to zoom, <kbd className="rounded bg-black/5 px-1">0</kbd> to reset, and <kbd className="rounded bg-black/5 px-1">←</kbd> / <kbd className="rounded bg-black/5 px-1">→</kbd> to pan.</p></section>}</div>}
  </div>;
}
