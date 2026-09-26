'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { MomentumTradeChart, US_LOCALE } from '../../../components/momentum-trade-chart';
import type { MomentumTrade } from '../../../lib/momentum-api';
import { orderVerbs } from '../../../lib/momentum-side';
import {
  CRITERION_FLAG,
  US_CRITERIA,
  fetchUSMomentumTrades,
  fetchUSWatchlist,
  type USMomentumTrade,
  type USWatchlistSession,
} from '../../../lib/us-momentum-api';

const ET = 'America/New_York';
const money = (value: number | undefined) =>
  value === undefined ? '—' : `$${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
const at = (value: string | undefined) =>
  value
    ? new Date(value).toLocaleTimeString('en-US', { timeZone: ET, hour: '2-digit', minute: '2-digit', hour12: false })
    : '—';
const dateKey = (value: string) => new Intl.DateTimeFormat('en-CA', { timeZone: ET }).format(new Date(value));
const dateLabel = (value: string) =>
  new Date(`${value}T12:00:00-04:00`).toLocaleDateString('en-US', {
    weekday: 'short',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: ET,
  });
const shares = (value: number | null | undefined) =>
  value === null || value === undefined ? '—' : `${(value / 1e6).toFixed(1)}M`;

/** Why a name did not make the watchlist, in the guide's own words. */
const REJECTION_LABEL: Record<string, string> = {
  price: 'Outside the $1–$20 band',
  day_chg: 'Not up 10% on the day',
  rvol: 'Relative volume too low',
  catalyst: 'No news catalyst',
  catalyst_unavailable: 'Catalyst required but no feed',
  float: '10M shares or more available',
  no_facts: 'No reference data for the name',
  ok: 'Passed',
};
const rejectionLabel = (reason: string) =>
  REJECTION_LABEL[reason] ?? (reason.startsWith('exchange:') ? `Not a listed venue (${reason.slice(9)})` : reason);

function pnlClass(value: number | undefined) {
  return value === undefined ? 'text-ink/55' : value >= 0 ? 'text-emerald-700' : 'text-rose-600';
}

/**
 * The five criteria and whether each one is actually running.
 *
 * This panel has no equivalent on the Indian page, and it is the reason this
 * arm exists as its own screen. `research/specs/warrior-patterns-nse.md` §1
 * records that the NSE version substituted criterion 5 wholesale (a live
 * sample found 0 of 120 NIFTY 500 names under 10M shares), bent criterion 2 to
 * +4–8% because of circuit bands, and never had a source for criterion 3. A
 * result from a screen with two criteria missing is a result about a different
 * screen — so this page states which ones ran instead of leaving it implicit.
 */
function ScreenStatus({ session }: { session: USWatchlistSession | null }) {
  const ran = (id: string) => {
    const flag = CRITERION_FLAG[id];
    if (!flag) return true; // price and day-change need no external feed
    if (!session) return null;
    return session.names.some((name) => name.flags?.[flag] === 1);
  };

  return (
    <section className="rounded-xl border border-accent/15 bg-accent/[0.045] p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="font-display text-lg">Indicators of high demand and low supply</h2>
          <p className="text-xs text-ink/60">
            The five criteria, applied literally. The Indian arm could not run three of them.
          </p>
        </div>
        {session && (
          <span
            className={`rounded-full px-2.5 py-1 text-xs font-bold ${
              session.complete === session.passed && session.passed > 0
                ? 'bg-emerald-100 text-emerald-800'
                : 'bg-amber-100 text-amber-800'
            }`}
          >
            {session.complete === session.passed && session.passed > 0
              ? 'Full five-criteria screen'
              : 'Partial screen'}
          </span>
        )}
      </div>

      <ol className="mt-3 grid gap-2 sm:grid-cols-2">
        {US_CRITERIA.map((criterion) => {
          const state = ran(criterion.id);
          return (
            <li key={criterion.id} className="flex gap-2 rounded-lg bg-panel/70 p-2.5 text-sm">
              <span
                className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ${
                  state === false ? 'bg-ink/25' : 'bg-accent'
                }`}
              >
                {criterion.n}
              </span>
              <span className="min-w-0">
                <strong className={state === false ? 'text-ink/50' : ''}>{criterion.label}</strong>
                <span className="ml-1.5 rounded-full bg-black/5 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-ink/55">
                  {criterion.side}
                </span>
                <p className="mt-0.5 text-xs text-ink/60">
                  {state === null
                    ? 'No screening session recorded yet.'
                    : state
                      ? 'Running.'
                      : 'Not running — no data source, so this criterion is skipped and flagged rather than silently passing every name.'}
                </p>
              </span>
            </li>
          );
        })}
      </ol>

      {session && session.missing_criteria.length > 0 && (
        <p className="mt-3 border-t border-accent/15 pt-3 text-sm text-ink/70">
          <strong>This is not the guide&apos;s screen yet.</strong> Missing: {session.missing_criteria.join(', ')}.
          Names passing an incomplete screen are counted separately so a forward result cannot quietly claim to have
          tested all five.
        </p>
      )}
    </section>
  );
}

/** Survivors and rejections from the most recent screening session. */
function Funnel({ session }: { session: USWatchlistSession }) {
  const rejections = Object.entries(session.rejected_by).sort(([, a], [, b]) => b - a);
  return (
    <section className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-lg">Screen funnel · {dateLabel(session.date)}</h2>
        <p className="text-xs text-ink/55">
          {session.considered} considered → {session.passed} passed → {session.complete} on the full screen
        </p>
      </div>
      <p className="mt-1 text-xs text-ink/60">
        Rejections are kept, not discarded. An empty watchlist from a quiet market and one from a broken input look
        identical unless the reasons are recorded.
      </p>
      {rejections.length === 0 ? (
        <p className="mt-3 text-sm text-ink/55">Nothing was rejected in this session.</p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {rejections.map(([reason, count]) => (
            <li key={reason} className="flex items-center gap-2 text-sm">
              <span className="w-10 shrink-0 text-right font-semibold tabular-nums">{count}</span>
              <span
                className="h-2 rounded-full bg-accent/35"
                style={{ width: `${Math.max(4, (count / session.considered) * 60)}%` }}
                aria-hidden="true"
              />
              <span className="text-ink/70">{rejectionLabel(reason)}</span>
            </li>
          ))}
        </ul>
      )}
      {session.names.some((name) => name.passed) && (
        <div className="mt-4 overflow-x-auto border-t border-black/5 pt-3">
          <table className="w-full text-left text-xs tabular-nums">
            <thead>
              <tr className="text-ink/55">
                {['Symbol', 'Price', 'Day change', 'RVOL', 'Float', 'Full screen'].map((label) => (
                  <th key={label} className="p-1 font-semibold">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {session.names
                .filter((name) => name.passed)
                .map((name) => (
                  <tr key={name.symbol} className="border-t border-black/5">
                    <td className="p-1 font-display font-semibold">{name.symbol}</td>
                    <td className="p-1">{money(name.price)}</td>
                    <td className="p-1 text-emerald-700">+{name.day_chg_pct.toFixed(1)}%</td>
                    <td className="p-1">{name.rvol === null ? '—' : `${name.rvol.toFixed(1)}×`}</td>
                    <td className="p-1">{shares(name.float_shares)}</td>
                    <td className="p-1">{name.screen_complete ? 'yes' : 'partial'}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function TradeRow({ trade, active, onClick }: { trade: USMomentumTrade; active: boolean; onClick: () => void }) {
  const net = trade.net_usd;
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
            {trade.screen_complete === false && (
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-800">
                partial screen
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-ink/55">
            {orderVerbs(trade).open} {money(trade.entry_price)} at {at(trade.entry_time)}
            {trade.exit_price !== undefined ? ` → ${orderVerbs(trade).close} ${money(trade.exit_price)} at ${at(trade.exit_time)}` : ' · Open'}
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

function TradeDetail({ trade, onBack }: { trade: USMomentumTrade; onBack: () => void }) {
  const costOverRisk = trade.cost_over_risk;
  return (
    <section className="space-y-4">
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

      <div className="rounded-xl border border-black/10 bg-panel p-4 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display text-2xl">
              {trade.symbol}{' '}
              <span className="text-base font-medium text-ink/55">· {trade.setup.replaceAll('_', ' ')}</span>
            </h2>
            <p className="mt-1 text-sm text-ink/60">
              Entry {money(trade.entry_price)} at {at(trade.entry_time)} · Stop {money(trade.stop)}
              {trade.target ? ` · Target ${money(trade.target)}` : ''}
            </p>
          </div>
          <p className={`shrink-0 font-display text-xl ${pnlClass(trade.net_usd)}`}>
            {trade.net_usd === undefined ? 'Open' : `${trade.net_usd >= 0 ? '+' : ''}${money(trade.net_usd)}`}
          </p>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-accent/10 px-2.5 py-1 text-accent">
            Day change {trade.day_chg_pct?.toFixed(2) ?? '—'}%
          </span>
          <span className="rounded-full bg-black/5 px-2.5 py-1">RVOL {trade.rvol?.toFixed(2) ?? '—'}×</span>
          <span className="rounded-full bg-black/5 px-2.5 py-1">Float {shares(trade.float_shares)}</span>
          <span className="rounded-full bg-black/5 px-2.5 py-1">Catalyst {trade.catalyst == null ? 'unknown' : trade.catalyst ? 'yes' : 'no'}</span>
          {trade.exchange && <span className="rounded-full bg-black/5 px-2.5 py-1">{trade.exchange}</span>}
        </div>
      </div>

      {/* The number that decides whether a US setup is worth taking at all. */}
      {costOverRisk !== undefined && (
        <section className="rounded-xl border border-accent/15 bg-accent/[0.045] p-4 text-sm">
          <h3 className="font-display text-lg">Could this trade pay for itself?</h3>
          <p className="mt-1 text-ink/70">
            Round-trip cost {money(trade.costs_usd)} against {money(trade.risk_usd)} at risk ={' '}
            <strong>{costOverRisk.toFixed(2)}×</strong>. At a 2:1 target that means this trade needed{' '}
            <strong>{(((1 + costOverRisk) / 3) * 100).toFixed(1)}%</strong> of trades like it to win, against the 33.3%
            the 2:1 rule advertises when costs are ignored.
          </p>
          <p className="mt-2 text-xs text-ink/60">
            US fees are charged per share, and the one-cent minimum tick is a fixed cost too — both are a larger
            percentage the cheaper the stock. Inside the guide&apos;s $1–$20 band that is the binding constraint, not
            commission rates.
          </p>
        </section>
      )}

      {trade.chart?.bars?.length ? (
        <div className="space-y-3">
          <div>
            <h3 className="font-display text-lg">{trade.symbol} · 1-minute execution chart</h3>
            <p className="text-xs text-ink/55">Eastern time. Precise candles and fills at the execution timeframe.</p>
          </div>
          <MomentumTradeChart trade={trade as unknown as MomentumTrade} interval="1m" locale={US_LOCALE} />
          <div>
            <h3 className="font-display text-lg">{trade.symbol} · 5-minute decision chart</h3>
            <p className="text-xs text-ink/55">The scanner&apos;s EMA, VWAP, and MACD decision timeframe.</p>
          </div>
          <MomentumTradeChart trade={trade as unknown as MomentumTrade} interval="5m" locale={US_LOCALE} />
        </div>
      ) : (
        <p className="metric-chip py-6 text-center text-sm text-ink/55">
          No candle snapshot was stored with this trade.
        </p>
      )}

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
            {trade.qty} shares · {money(trade.notional_usd)}
          </p>
        </div>
        <div className="metric-chip">
          <p className="text-xs text-ink/55">Costs</p>
          <p className="mt-1 font-semibold">{money(trade.costs_usd)}</p>
        </div>
      </div>
    </section>
  );
}

export default function USMomentumPage() {
  const [trades, setTrades] = useState<USMomentumTrade[]>([]);
  const [sessions, setSessions] = useState<USWatchlistSession[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [openDates, setOpenDates] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mobileView, setMobileView] = useState<'list' | 'detail'>('list');

  useEffect(() => {
    Promise.all([fetchUSMomentumTrades(), fetchUSWatchlist()])
      .then(([{ trades: nextTrades }, { sessions: nextSessions }]) => {
        setTrades(nextTrades);
        setSessions(nextSessions);
        setSelected(nextTrades[0]?._id ?? null);
        setOpenDates(nextTrades[0] ? new Set([dateKey(nextTrades[0].entry_time)]) : new Set());
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : 'Could not load US momentum data'))
      .finally(() => setLoading(false));
  }, []);

  const groups = useMemo(() => {
    const next = new Map<string, USMomentumTrade[]>();
    for (const trade of trades) {
      const key = dateKey(trade.entry_time);
      next.set(key, [...(next.get(key) ?? []), trade]);
    }
    return [...next.entries()].sort(([a], [b]) => b.localeCompare(a));
  }, [trades]);

  const trade = trades.find((item) => item._id === selected) ?? null;
  const totalNet = trades.reduce((sum, item) => sum + (item.net_usd ?? 0), 0);
  const latest = sessions[0] ?? null;

  function handleSelectTrade(id: string) {
    setSelected(id);
    setMobileView('detail');
  }

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-accent">Paper-trade review · US</p>
          <h1 className="font-display text-3xl tracking-tight">US momentum trades</h1>
          <p className="mt-1 text-sm text-ink/65">
            The Warrior five-criteria screen, run on US equities in Eastern time. Separate universe, separate cost
            model, separate ledger from the NSE arm — the two are never summed.
          </p>
          <Link
            href="/momentum"
            className="mt-2 inline-block rounded-full border border-black/10 px-3 py-1.5 text-xs font-semibold hover:bg-black/5"
          >
            ← Indian momentum trades
          </Link>
          <Link
            href="/momentum/us/watchlist"
            className="ml-2 mt-2 inline-block rounded-full border border-black/10 px-3 py-1.5 text-xs font-semibold hover:bg-black/5"
          >
            US watchlist →
          </Link>
        </div>
        <div className="metric-chip text-right">
          <p className="text-xs text-ink/55">Recorded P&amp;L</p>
          <p className={`font-display text-xl ${pnlClass(totalNet)}`}>
            {totalNet >= 0 ? '+' : ''}
            {money(totalNet)}
          </p>
        </div>
      </section>

      {!loading && !error && <ScreenStatus session={latest} />}
      {!loading && !error && latest && <Funnel session={latest} />}

      {loading && <div className="metric-chip py-12 text-center text-sm text-ink/55">Loading US momentum data…</div>}
      {error && <div className="metric-chip border-rose-200 py-6 text-rose-700">{error}</div>}

      {!loading && !error && trades.length === 0 && (
        <div className="metric-chip space-y-2 py-10 text-center text-sm text-ink/60">
          <p className="font-semibold text-ink/75">No US paper trades recorded yet.</p>
          <p className="mx-auto max-w-xl">
            The screen, the cost model and this review page are built. What is missing is a US market-data feed to run
            them against — until one is wired up, no session can be screened and no trade can be taken.
          </p>
        </div>
      )}

      {!loading && !error && trades.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
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

          <div className={mobileView === 'list' ? 'hidden lg:block' : 'block'}>
            {trade && <TradeDetail trade={trade} onBack={() => setMobileView('list')} />}
          </div>
        </div>
      )}
    </div>
  );
}
