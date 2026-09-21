'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { MomentumTradeChart } from '../../components/momentum-trade-chart';
import { ConfidenceMeter } from '../../components/signals/ConfidenceMeter';
import {
  calculateTradeConfidence,
  type ConfidenceBand,
  type TradeConfidence,
} from '../../lib/momentum-confidence';
import { fetchMomentumTrades, type MomentumTrade } from '../../lib/momentum-api';

const IST = 'Asia/Kolkata';
const money = (value: number | undefined) =>
  value === undefined ? '—' : `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const at = (value: string | undefined) =>
  value
    ? new Date(value).toLocaleTimeString('en-IN', {
        timeZone: IST,
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '—';
const dateKey = (value: string) =>
  new Intl.DateTimeFormat('en-CA', { timeZone: IST }).format(new Date(value));
const dateLabel = (value: string) =>
  new Date(`${value}T12:00:00+05:30`).toLocaleDateString('en-IN', {
    weekday: 'short',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: IST,
  });
const strategyLabel = (value: string | undefined) =>
  ({
    attention_1m: 'Attention control',
    attention_1m_resistance_state: 'Resistance-state',
    attention_1m_false_break_reclaim: 'False-break reclaim',
  })[value ?? ''] ?? 'Earlier paper run';

function pnlClass(value: number | undefined) {
  return value === undefined ? 'text-ink/55' : value >= 0 ? 'text-emerald-700' : 'text-rose-600';
}

function confidenceClass(band: ConfidenceBand) {
  return {
    strong: 'bg-emerald-100 text-emerald-800',
    moderate: 'bg-amber-100 text-amber-800',
    developing: 'bg-rose-100 text-rose-800',
    unavailable: 'bg-slate-100 text-slate-700',
  }[band];
}

function ConfidenceBadge({ confidence }: { confidence: TradeConfidence }) {
  const label =
    confidence.score === null ? 'Confidence unavailable' : `Confidence ${confidence.score}`;
  const coverage = `${confidence.evidenceCoverage}% evidence`;
  return (
    <span
      title={`${label}; ${coverage}`}
      className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${confidenceClass(confidence.band)}`}
    >
      {confidence.score === null ? 'Confidence —' : `Confidence ${confidence.score}`}
      {confidence.evidenceCoverage < 100 ? ` · ${confidence.evidenceCoverage}%` : ''}
    </span>
  );
}

function TradeRow({
  trade,
  active,
  onClick,
}: {
  trade: MomentumTrade;
  active: boolean;
  onClick: () => void;
}) {
  const net = trade.net_inr;
  const confidence = calculateTradeConfidence(trade);
  return (
    <button
      onClick={onClick}
      className={`w-full border-b border-black/5 px-3 py-3 text-left transition last:border-0 hover:bg-accent/5 ${active ? 'bg-accent/10' : ''}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="shrink-0 font-display font-semibold">{trade.symbol}</span>
            <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-ink/60">
              {trade.setup.replaceAll('_', ' ')}
            </span>
            <ConfidenceBadge confidence={confidence} />
            {trade.news_context && trade.news_context.length > 0 && (
              <span
                title={`${trade.news_context.length} news item(s) near entry`}
                className="shrink-0 rounded-full bg-sky-100 px-1.5 py-0.5 text-[10px] font-bold text-sky-700"
              >
                📰 {trade.news_context.length}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-ink/55">
            {strategyLabel(trade.strategy)} · Buy {money(trade.entry_price)} at{' '}
            {at(trade.entry_time)}
            {trade.exit_price !== undefined
              ? ` → Sell ${money(trade.exit_price)} at ${at(trade.exit_time)}`
              : ' · Open'}
          </p>
        </div>
        <div className={`shrink-0 text-right text-sm font-bold ${pnlClass(net)}`}>
          {net === undefined ? 'Open' : `${net >= 0 ? '+' : ''}${money(net)}`}
          <p className="mt-1 whitespace-nowrap text-[11px] font-medium text-ink/45">
            {trade.exit_reason?.replaceAll('_', ' ') ?? 'in progress'}
          </p>
        </div>
      </div>
    </button>
  );
}

function TradeConfidenceCard({ trade }: { trade: MomentumTrade }) {
  const confidence = calculateTradeConfidence(trade);
  const label = {
    strong: 'Strong entry evidence',
    moderate: 'Moderate entry evidence',
    developing: 'Developing entry evidence',
    unavailable: 'Evidence unavailable',
  }[confidence.band];

  return (
    <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-lg">Entry confidence</h3>
          <p className="text-xs text-ink/60">
            A display-only score of recorded technical and execution evidence at entry. It is not a
            profit prediction.
          </p>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-bold ${confidenceClass(confidence.band)}`}
        >
          {label}
        </span>
      </div>

      {confidence.score === null ? (
        <p className="mt-4 rounded-lg bg-black/[0.03] p-3 text-sm text-ink/65">
          No score can be calculated because this historical trade has no recorded scoring evidence.
        </p>
      ) : (
        <div className="mt-4 max-w-md">
          <ConfidenceMeter confidence={confidence.score} />
        </div>
      )}

      <p className="mt-3 text-xs text-ink/60">
        Evidence coverage: <strong className="text-ink">{confidence.evidenceCoverage}%</strong>.
        Missing historical fields are labeled below and do not receive invented values.
      </p>

      <ul className="mt-4 divide-y divide-black/5 rounded-lg border border-black/5">
        {confidence.factors.map((item) => (
          <li
            key={item.id}
            className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 px-3 py-2.5 text-sm"
          >
            <div className="min-w-0 flex-1">
              <p className="font-medium">{item.label}</p>
              <p className="text-xs text-ink/60">{item.detail}</p>
            </div>
            <span
              className={`shrink-0 text-xs font-bold ${
                item.status === 'earned'
                  ? 'text-emerald-700'
                  : item.status === 'not_met'
                    ? 'text-rose-600'
                    : 'text-ink/45'
              }`}
            >
              {item.status === 'not_recorded' ? 'Not recorded' : `${item.earned}/${item.maximum}`}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-ink/45">Rubric: {confidence.rubricVersion}</p>
    </section>
  );
}

function EntryReason({ trade }: { trade: MomentumTrade }) {
  const attention = trade.setup === 'attention_1m_confirmation';
  const reclaim = trade.setup === 'attention_false_break_reclaim';
  const volumeRatio = trade.setup_meta?.volume_ratio;
  const promotion = trade.entry_evidence?.promotion;
  const trend = trade.entry_evidence?.trend;
  const confirmation = trade.entry_evidence?.confirmation;
  const trendDetail = trend
    ? `5m candle at ${at(trend.bar_start)}: EMA 9 ${money(trend.ema9)}, EMA 20 ${money(trend.ema20)}, close ${money(trend.close)}, VWAP ${money(trend.vwap)}.`
    : 'This strategy requires EMA 9 above EMA 20 and the completed 5m close above VWAP. The measured indicator snapshot was not stored for this trade.';
  const confirmationDetail = confirmation
    ? `1m candle at ${at(confirmation.bar_start)}: open ${money(confirmation.open)}, close ${money(confirmation.close)}; close position ${(confirmation.close_position * 100).toFixed(1)}% (minimum ${(confirmation.minimum_close_position * 100).toFixed(0)}%); volume ${confirmation.volume_ratio.toFixed(2)}× (minimum ${confirmation.minimum_volume_ratio}×).`
    : `This strategy requires a green candle closing in its upper 40% with at least 2.5× recent 1m volume. ${typeof volumeRatio === 'number' ? `Stored volume ratio: ${volumeRatio.toFixed(2)}×.` : 'Volume ratio unavailable.'} The full confirmation snapshot was not stored.`;
  const gates = reclaim
    ? [
        {
          label: 'Failed first breakout',
          detail: `The original attention entry exited through its broken level ${money(typeof trade.setup_meta?.reclaim_level === 'number' ? trade.setup_meta.reclaim_level : (trade.level ?? trade.trigger_px))}. This arm permits only one retry.`,
        },
        {
          label: 'Trend still intact',
          detail: trendDetail,
        },
        {
          label: '1-minute reclaim',
          detail: confirmationDetail,
        },
        {
          label: 'New buy-stop',
          detail: `A later quote traded through the reclaim candle high at ${money(trade.trigger_px ?? trade.entry_price)}; the stop was rebuilt from the reclaim structure.`,
        },
      ]
    : attention
      ? [
          {
            label: 'Attention watchlist',
            detail: promotion?.observed_at
              ? `Promoted at ${at(promotion.observed_at)}: day change ${promotion.day_chg_pct.toFixed(2)}% (minimum ${promotion.minimum_day_chg_pct}%) and RVOL ${promotion.rvol.toFixed(2)}× (minimum ${promotion.minimum_rvol}×). Reason: ${promotion.reason.replaceAll('_', ' ')}.`
              : `Promotion-time evidence was not stored in this trade. Signal-time day change ${trade.day_chg_pct?.toFixed(2) ?? '—'}% and RVOL ${trade.rvol?.toFixed(2) ?? '—'}× cannot verify the earlier promotion.`,
          },
          {
            label: '5-minute trend context',
            detail: trendDetail,
          },
          {
            label: '1-minute confirmation',
            detail: confirmationDetail,
          },
          {
            label: 'Breakout trigger',
            detail: `Recorded trigger ${money(trade.trigger_px ?? trade.entry_price)}; paper fill ${money(trade.entry_price)} at ${at(trade.entry_time)}.${trade.level != null ? ` Defended breakout level: ${money(trade.level)}.` : ''}`,
          },
        ]
      : [
          {
            label: 'Named setup',
            detail: `${trade.setup.replaceAll('_', ' ')} fired on the completed strategy timeframe.`,
          },
          {
            label: 'Recorded mover context',
            detail: `Day change was ${trade.day_chg_pct?.toFixed(2) ?? '—'}% and RVOL was ${trade.rvol?.toFixed(2) ?? '—'}× at signal time.`,
          },
          {
            label: 'Breakout trigger',
            detail: `The recorded trigger was ${money(trade.trigger_px ?? trade.entry_price)} and the paper fill was ${money(trade.entry_price)}.`,
          },
        ];
  const strictPatterns = trade.pattern_matches
    ?.map((match) => `${match.name.replaceAll('_', ' ')} · ${match.timeframe}`)
    .join(', ');
  return (
    <section className="rounded-xl border border-accent/15 bg-accent/[0.045] p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="font-display text-lg">Why the scanner entered</h3>
          <p className="text-xs text-ink/60">
            Recorded measurements and strategy requirements. Missing historical evidence is
            identified explicitly.
          </p>
        </div>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-bold text-slate-700">
          {promotion?.observed_at && trend && confirmation
            ? 'Decision evidence recorded'
            : 'Partial historical evidence'}
        </span>
      </div>
      <ol className="mt-3 space-y-2">
        {gates.map((gate, index) => (
          <li key={gate.label} className="flex gap-2 text-sm">
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-bold text-white">
              {index + 1}
            </span>
            <span>
              <strong>{gate.label}:</strong> <span className="text-ink/70">{gate.detail}</span>
            </span>
          </li>
        ))}
      </ol>
      <div className="mt-3 border-t border-accent/15 pt-3 text-sm">
        <strong>Risk plan:</strong> entry {money(trade.entry_price)}, invalidation stop{' '}
        {money(trade.stop)}, {trade.target ? `target ${money(trade.target)}, ` : ''}
        {trade.qty} shares.{' '}
        {strictPatterns
          ? `Recorded formations: ${strictPatterns}. Formation alone does not confirm a trade.`
          : trade.candle_tags?.length
            ? `Legacy candle context: ${trade.candle_tags.join(', ')}.`
            : ''}
      </div>
      {(trade.pattern_matches ?? []).map((match) => (
        <details
          key={`${match.name}-${match.timeframe}-${match.start}`}
          className="mt-3 border-t border-accent/15 pt-2 text-sm"
        >
          <summary className="cursor-pointer font-medium">
            {match.name.replaceAll('_', ' ')} · {match.timeframe} · {at(match.start)}–
            {at(match.end)}
          </summary>
          <p className="my-2 text-ink/70">
            {match.direction ?? 'Direction not stored'} · {match.kind ?? 'Historical formation'}.{' '}
            Preceding trend: {match.prior_trend ?? 'not stored'}.
            {match.formed_at ? ` Fully formed at ${at(match.formed_at)}.` : ''}
            {match.rules_version
              ? ` Rules: ${match.rules_version}.`
              : ' Legacy detector; rule version not stored.'}
          </p>
          {match.evidence?.candles && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs tabular-nums">
                <thead>
                  <tr>
                    {['Candle start', 'Open', 'High', 'Low', 'Close', 'Volume'].map((label) => (
                      <th key={label} className="p-1">
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {match.evidence.candles.map((bar) => (
                    <tr key={bar.time}>
                      <td className="p-1">{at(bar.time)}</td>
                      {[bar.open, bar.high, bar.low, bar.close].map((value, i) => (
                        <td key={i} className="p-1">
                          {money(value)}
                        </td>
                      ))}
                      <td className="p-1">{bar.volume.toLocaleString('en-IN')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {match.evidence?.trend_closes && (
            <p className="mt-2 text-xs text-ink/60">
              Pre-pattern closes: {match.evidence.trend_closes.map(money).join(' → ')}
            </p>
          )}
        </details>
      ))}
    </section>
  );
}

const newsAt = (value: string) =>
  new Date(value).toLocaleString('en-IN', {
    timeZone: IST,
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

/**
 * Headlines mentioning the symbol in the 24h before entry — descriptive
 * context only, gathered independently of and never used by the strategy's
 * entry/exit logic. Absent on trades opened before this was wired up.
 */
function NewsContext({ trade }: { trade: MomentumTrade }) {
  if (!trade.news_context || trade.news_context.length === 0) return null;
  return (
    <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
      <h3 className="font-display text-lg">News around entry</h3>
      <p className="text-xs text-ink/55">
        Headlines mentioning {trade.symbol} in the 24h before entry — for context only, not a signal
        input.
      </p>
      <ul className="mt-3 space-y-2">
        {trade.news_context.map((item, i) => (
          <li
            key={`${item.published_at}-${i}`}
            className="rounded-lg bg-black/[0.03] p-2.5 text-sm"
          >
            {item.url ? (
              <a
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className="font-medium text-accent hover:underline"
              >
                {item.headline}
              </a>
            ) : (
              <p className="font-medium">{item.headline}</p>
            )}
            <p className="mt-0.5 text-xs text-ink/55">
              {newsAt(item.published_at)} · {item.publisher ?? item.source}
              {item.tier ? ` · ${item.tier}` : ''}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TradeDetail({ trade, onBack }: { trade: MomentumTrade; onBack: () => void }) {
  return (
    <section className="space-y-4">
      {/* Back button — only visible on mobile (hidden on lg where the list is always shown) */}
      <button
        type="button"
        onClick={onBack}
        className="flex items-center gap-1.5 text-sm font-medium text-accent lg:hidden"
        aria-label="Back to trade list"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <polyline points="15 18 9 12 15 6" />
        </svg>
        All trades
      </button>

      {/* Header card */}
      <div className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-2xl">
              {trade.symbol}{' '}
              <span className="text-base font-medium text-ink/55">
                · {trade.setup.replaceAll('_', ' ')}
              </span>
            </h2>
            <p className="mt-1 text-sm text-ink/60">
              {strategyLabel(trade.strategy)} · Entry {money(trade.entry_price)} at{' '}
              {at(trade.entry_time)} · Stop {money(trade.stop)}
              {trade.target ? ` · Target ${money(trade.target)}` : ''}
            </p>
          </div>
          <p className={`shrink-0 font-display text-xl ${pnlClass(trade.net_inr)}`}>
            {trade.net_inr === undefined
              ? 'Open'
              : `${trade.net_inr >= 0 ? '+' : ''}${money(trade.net_inr)}`}
          </p>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-accent/10 px-2.5 py-1 text-accent">
            Day change {trade.day_chg_pct?.toFixed(2) ?? '—'}%
          </span>
          <span className="rounded-full bg-black/5 px-2.5 py-1">
            RVOL {trade.rvol?.toFixed(2) ?? '—'}×
          </span>
          <span className="rounded-full bg-black/5 px-2.5 py-1">
            Catalyst {trade.catalyst ? 'yes' : 'no'}
          </span>
          <span className="rounded-full bg-black/5 px-2.5 py-1">
            News{' '}
            {trade.news_context && trade.news_context.length > 0
              ? trade.news_context.length
              : 'none'}
          </span>
          {trade.candle_tags?.map((tag) => (
            <span key={tag} className="rounded-full bg-black/5 px-2.5 py-1 text-ink/65">
              legacy: {tag}
            </span>
          ))}
          {trade.pattern_matches?.map((match) => (
            <span
              key={`${match.name}-${match.start}`}
              className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-800"
            >
              {match.name.replaceAll('_', ' ')} · {match.timeframe}
            </span>
          ))}
        </div>
      </div>

      <TradeConfidenceCard trade={trade} />
      <EntryReason trade={trade} />
      <NewsContext trade={trade} />

      {/* Charts */}
      <div className="space-y-3">
        <div>
          <h3 className="font-display text-lg">{trade.symbol} · 1-minute execution chart</h3>
          <p className="text-xs text-ink/55">
            Precise candles and fills at the execution timeframe.
          </p>
        </div>
        <MomentumTradeChart trade={trade} interval="1m" />
        <div>
          <h3 className="font-display text-lg">{trade.symbol} · 5-minute decision chart</h3>
          <p className="text-xs text-ink/55">
            The scanner&apos;s EMA, VWAP, and MACD decision timeframe.
          </p>
        </div>
        <MomentumTradeChart trade={trade} interval="5m" />
      </div>

      {/* Metrics row */}
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="metric-chip">
          <p className="text-xs text-ink/55">Exit</p>
          <p className="mt-1 font-semibold">
            {trade.exit_price === undefined
              ? 'Still open'
              : `${money(trade.exit_price)} · ${trade.exit_reason?.replaceAll('_', ' ')}`}
          </p>
        </div>
        <div className="metric-chip">
          <p className="text-xs text-ink/55">Position</p>
          <p className="mt-1 font-semibold">
            {trade.qty} shares · {money(trade.notional_inr)}
          </p>
        </div>
        <div className="metric-chip">
          <p className="text-xs text-ink/55">Costs</p>
          <p className="mt-1 font-semibold">{money(trade.costs_inr)}</p>
        </div>
      </div>

      <p className="text-xs leading-relaxed text-ink/55">
        Focus either chart, then use <kbd className="rounded bg-black/5 px-1">+</kbd> /{' '}
        <kbd className="rounded bg-black/5 px-1">−</kbd> to zoom,{' '}
        <kbd className="rounded bg-black/5 px-1">0</kbd> to reset, and{' '}
        <kbd className="rounded bg-black/5 px-1">←</kbd> /{' '}
        <kbd className="rounded bg-black/5 px-1">→</kbd> to pan.
      </p>
    </section>
  );
}

export default function MomentumPage() {
  const [trades, setTrades] = useState<MomentumTrade[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [openDates, setOpenDates] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // On mobile, track whether the user is viewing the list or the detail panel
  const [mobileView, setMobileView] = useState<'list' | 'detail'>('list');

  useEffect(() => {
    fetchMomentumTrades()
      .then(({ trades: next }) => {
        setTrades(next);
        setSelected(next[0]?._id ?? null);
        setOpenDates(next[0] ? new Set([dateKey(next[0].entry_time)]) : new Set());
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : 'Could not load momentum trades'),
      )
      .finally(() => setLoading(false));
  }, []);

  const groups = useMemo(() => {
    const next = new Map<string, MomentumTrade[]>();
    for (const trade of trades) {
      const key = dateKey(trade.entry_time);
      next.set(key, [...(next.get(key) ?? []), trade]);
    }
    return [...next.entries()].sort(([a], [b]) => b.localeCompare(a));
  }, [trades]);

  const trade = trades.find((item) => item._id === selected) ?? null;

  const totalGross = trades.reduce((sum, item) => sum + (item.gross_inr ?? 0), 0);
  const totalNet = trades.reduce((sum, item) => sum + (item.net_inr ?? 0), 0);

  const dayTotals = useMemo(
    () =>
      groups.map(([date, items]) => ({
        date,
        count: items.length,
        gross: items.reduce((sum, item) => sum + (item.gross_inr ?? 0), 0),
        net: items.reduce((sum, item) => sum + (item.net_inr ?? 0), 0),
      })),
    [groups],
  );

  function handleSelectTrade(id: string) {
    setSelected(id);
    setMobileView('detail');
  }

  return (
    <div className="space-y-5">
      {/* Page header */}
      <section className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-accent">
            Paper-trade review
          </p>
          <h1 className="font-display text-3xl tracking-tight">Momentum trades</h1>
          <p className="mt-1 text-sm text-ink/65">
            Select any trade to review the exact one-minute candles, overlays, and execution points.
          </p>
          <Link
            href="/momentum/analytics"
            className="mt-2 inline-block rounded-full border border-black/10 px-3 py-1.5 text-xs font-semibold hover:bg-black/5"
          >
            Analytics dashboard →
          </Link>
        </div>
        <div className="flex gap-3">
          <div className="metric-chip text-right">
            <p className="text-xs text-ink/55">Gross P&amp;L</p>
            <p className={`font-display text-xl ${pnlClass(totalGross)}`}>
              {totalGross >= 0 ? '+' : ''}
              {money(totalGross)}
            </p>
          </div>
          <div className="metric-chip text-right">
            <p className="text-xs text-ink/55">Net P&amp;L</p>
            <p className={`font-display text-xl ${pnlClass(totalNet)}`}>
              {totalNet >= 0 ? '+' : ''}
              {money(totalNet)}
            </p>
          </div>
        </div>
      </section>

      {/* Day-wise P&L, all arms combined */}
      {!loading && !error && dayTotals.length > 0 && (
        <section className="overflow-hidden rounded-xl border border-black/10 bg-panel shadow-card">
          <div className="border-b border-black/10 px-4 py-3">
            <h2 className="font-display text-lg">Day-wise P&amp;L</h2>
            <p className="text-xs text-ink/55">
              Gross and net across every paper trade, all arms combined.
            </p>
          </div>
          <div className="max-h-80 overflow-y-auto overflow-x-auto">
            <table className="w-full text-left text-sm tabular-nums">
              <thead className="sticky top-0 z-10 bg-panel">
                <tr className="text-xs font-bold uppercase tracking-wide text-ink/50">
                  <th className="px-4 py-2">Date</th>
                  <th className="px-4 py-2">Trades</th>
                  <th className="px-4 py-2">Gross</th>
                  <th className="px-4 py-2">Net</th>
                </tr>
              </thead>
              <tbody>
                {dayTotals.map(({ date, count, gross, net }) => (
                  <tr key={date} className="border-t border-black/5">
                    <td className="px-4 py-2 font-medium">{dateLabel(date)}</td>
                    <td className="px-4 py-2 text-ink/60">{count}</td>
                    <td className={`px-4 py-2 font-semibold ${pnlClass(gross)}`}>
                      {gross >= 0 ? '+' : ''}
                      {money(gross)}
                    </td>
                    <td className={`px-4 py-2 font-semibold ${pnlClass(net)}`}>
                      {net >= 0 ? '+' : ''}
                      {money(net)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* States */}
      {loading && (
        <div className="metric-chip py-12 text-center text-sm text-ink/55">
          Loading momentum trade history…
        </div>
      )}
      {error && <div className="metric-chip border-rose-200 py-6 text-rose-700">{error}</div>}
      {!loading && !error && trades.length === 0 && (
        <div className="metric-chip py-12 text-center text-sm text-ink/55">
          No momentum paper trades have been recorded yet.
        </div>
      )}

      {/* Main content: list + detail */}
      {!loading && !error && trades.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
          {/* Trade list sidebar
              - Mobile: full width, hidden when a trade is selected (mobileView === 'detail')
              - lg+: always visible as a fixed-width sidebar */}
          <aside
            className={`overflow-hidden rounded-xl border border-black/10 bg-panel shadow-card ${mobileView === 'detail' ? 'hidden lg:block' : 'block'}`}
          >
            {groups.map(([date, items]) => {
              const isOpen = openDates.has(date);
              return (
                <section key={date} className="border-b border-black/5 last:border-0">
                  <button
                    type="button"
                    onClick={() =>
                      setOpenDates((current) => {
                        const next = new Set(current);
                        if (next.has(date)) next.delete(date);
                        else next.add(date);
                        return next;
                      })
                    }
                    className="flex w-full items-center justify-between bg-black/[0.025] px-3 py-2 text-left text-xs font-bold text-ink/60 hover:bg-black/[0.05]"
                    aria-expanded={isOpen}
                  >
                    <span>
                      {dateLabel(date)}{' '}
                      <span className="ml-1 font-medium">
                        ({items.length} trade{items.length === 1 ? '' : 's'})
                      </span>
                    </span>
                    <span className="text-base leading-none" aria-hidden="true">
                      {isOpen ? '−' : '+'}
                    </span>
                  </button>
                  {isOpen &&
                    items.map((item) => (
                      <TradeRow
                        key={item._id}
                        trade={item}
                        active={item._id === selected}
                        onClick={() => handleSelectTrade(item._id)}
                      />
                    ))}
                </section>
              );
            })}
          </aside>

          {/* Trade detail panel
              - Mobile: full width, hidden when on list view
              - lg+: always visible next to the sidebar */}
          <div className={mobileView === 'list' ? 'hidden lg:block' : 'block'}>
            {trade && <TradeDetail trade={trade} onBack={() => setMobileView('list')} />}
          </div>
        </div>
      )}
    </div>
  );
}
