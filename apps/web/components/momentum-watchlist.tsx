'use client';

import Link from 'next/link';
import { createContext, useContext, useEffect, useMemo, useState } from 'react';

import { GateChecklist } from './momentum-gate-checklist';
import { type ChartLocale, MomentumTradeChart, NSE_LOCALE } from './momentum-trade-chart';
import {
  fetchMomentumWatchlist,
  fetchWatchlistBars,
  fetchWatchlistNews,
  type MomentumBar,
  type MomentumTrade,
  type PatternMatch,
  type WatchlistName,
  type WatchlistNewsItem,
  type WatchlistSession,
} from '../lib/momentum-api';

/**
 * Everything market-specific about the watchlist page. The NSE and US arms
 * share the layout; they differ in clock, currency, data source and wording.
 */
export interface WatchlistMarket {
  code: 'NSE' | 'US';
  locale: ChartLocale;
  /** Regular-session open in the market's clock, HH:mm. */
  open: string;
  title: string;
  intro: string;
  links: { href: string; label: string }[];
  tradesHref: string;
  barsNote: string;
  /** Where the News panel's items come from, shown under its heading. */
  newsNote: string;
  /** Shown when the window had no items. */
  newsEmpty: string;
  fetchSessions: () => Promise<{ sessions: WatchlistSession[] }>;
  fetchBars: (symbol: string, date: string) => Promise<{ bars: MomentumBar[] }>;
  empty: string;
}

export const NSE_WATCHLIST: WatchlistMarket = {
  code: 'NSE',
  locale: NSE_LOCALE,
  open: '09:15',
  title: 'Momentum watchlist',
  intro:
    "Every name the scanner put on its attention watchlist, day by day, with the session's 1-minute and 5-minute charts.",
  links: [
    { href: '/momentum', label: '← Momentum trades' },
    { href: '/momentum/us/watchlist', label: 'US watchlist →' },
  ],
  tradesHref: '/momentum',
  barsNote: 'Exchange 1-minute candles for the whole session.',
  newsNote:
    'Headlines naming this company from the previous close to the end of this session (Google News). Context only: the scanner never reads these.',
  newsEmpty:
    "No headlines naming this company in the window. The move had no reported news catalyst, or the news wasn't indexed.",
  fetchSessions: () => fetchMomentumWatchlist(),
  fetchBars: fetchWatchlistBars,
  empty: 'The scanner has not put any names on its watchlist yet.',
};

const MarketContext = createContext<WatchlistMarket>(NSE_WATCHLIST);

/** Formatters in the market's own clock and currency. */
function useFormat() {
  const market = useContext(MarketContext);
  return useMemo(() => {
    const { symbol, numberLocale, timeZone } = market.locale;
    return {
      money: (value: number | null | undefined) =>
        value == null ? '—' : `${symbol}${value.toLocaleString(numberLocale, { maximumFractionDigits: 2 })}`,
      at: (value: string | null | undefined) =>
        value
          ? new Date(value).toLocaleTimeString(numberLocale, { timeZone, hour: '2-digit', minute: '2-digit', hour12: false })
          : '—',
      // A session date is a calendar date, not an instant: format it in UTC so
      // no zone can move it to the day before.
      dateLabel: (value: string) =>
        new Date(`${value}T12:00:00Z`).toLocaleDateString(numberLocale, {
          weekday: 'short', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
        }),
      newsDay: (value: string) =>
        new Date(value).toLocaleDateString(numberLocale, { timeZone, day: 'numeric', month: 'short' }),
    };
  }, [market]);
}

/** One name's headlines: still loading, fetched (possibly empty), or failed. */
type NameNews = 'loading' | WatchlistNewsItem[] | { error: string };
const newsItems = (news: NameNews | undefined) => (Array.isArray(news) ? news : []);

const strategyLabel = (value: string | undefined) =>
  ({
    attention_1m: 'Attention control',
    attention_1m_resistance_state: 'Resistance-state',
    attention_1m_false_break_reclaim: 'False-break reclaim',
    attention_1m_merged: 'Attention merged',
    warrior_strict: 'Warrior strict',
  })[value ?? ''] ?? (value ? value.replaceAll('_', ' ') : 'Earlier paper run');
const nameKey = (date: string, symbol: string) => `${date}:${symbol}`;
const truncate = (text: string, max: number) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

/** Where a headline sits against the session and the scanner's first flag. */
function newsTiming(
  item: WatchlistNewsItem,
  date: string,
  firstSeen: string,
  market: WatchlistMarket,
): { label: string; className: string } {
  const published = Date.parse(item.published_at);
  // Compare in the market's clock: its date, then its HH:mm against the open.
  const zoned = new Date(published);
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: market.locale.timeZone }).format(zoned);
  const hhmm = new Intl.DateTimeFormat('en-GB', {
    timeZone: market.locale.timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(zoned);
  if (day < date || (day === date && hhmm < market.open)) {
    return { label: 'Before the open', className: 'bg-sky-100 text-sky-800' };
  }
  if (published <= Date.parse(firstSeen)) return { label: 'Before first flag', className: 'bg-sky-100 text-sky-800' };
  return { label: 'After first flag', className: 'bg-black/5 text-ink/60' };
}

function NewsPanel({ date, name, news }: { date: string; name: WatchlistName; news: NameNews | undefined }) {
  const market = useContext(MarketContext);
  const { at, newsDay } = useFormat();
  const items = newsItems(news);
  return (
    <section className="rounded-xl border border-black/10 p-4">
      <h3 className="font-display text-lg">News signal</h3>
      <p className="text-xs text-ink/55">{market.newsNote}</p>
      {(news === undefined || news === 'loading') && <p className="mt-3 text-sm text-ink/55">Searching headlines…</p>}
      {news && !Array.isArray(news) && news !== 'loading' && (
        <p className="mt-3 text-sm text-rose-700">News lookup failed: {news.error}</p>
      )}
      {Array.isArray(news) && items.length === 0 && <p className="mt-3 text-sm text-ink/55">{market.newsEmpty}</p>}
      {items.length > 0 && (
        <ul className="mt-3 space-y-2">
          {items.map((item, i) => {
            const timing = newsTiming(item, date, name.first_seen, market);
            return (
              <li key={`${item.published_at}-${i}`} className="rounded-lg bg-black/[0.03] p-2.5 text-sm">
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noreferrer" className="font-medium text-accent hover:underline">
                    {item.headline}
                  </a>
                ) : (
                  <p className="font-medium">{item.headline}</p>
                )}
                <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink/55">
                  <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${timing.className}`}>{timing.label}</span>
                  {item.kind === 'dilution' && (
                    <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800">
                      Share offering · dilution
                    </span>
                  )}
                  {newsDay(item.published_at)} {at(item.published_at)} · {item.publisher}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function pnlClass(value: number | null | undefined) {
  return value == null ? 'text-ink/55' : value >= 0 ? 'text-emerald-700' : 'text-rose-600';
}

/**
 * The chart component draws trades. A watched name is handed to it as a
 * trade-shaped record with no fill: `entry_time` only focuses the view on the
 * first flag, and watch mode keeps the price fields from being drawn.
 */
function asChartRecord(date: string, name: WatchlistName, bars: MomentumBar[]): MomentumTrade {
  const seen = new Set<string>();
  const patterns: PatternMatch[] = [];
  for (const flag of name.flags) {
    for (const match of flag.pattern_matches) {
      const id = `${match.name}|${match.timeframe}|${match.start}`;
      if (seen.has(id)) continue;
      seen.add(id);
      patterns.push(match);
    }
  }
  return {
    _id: nameKey(date, name.symbol),
    symbol: name.symbol,
    status: 'closed',
    setup: 'watchlist',
    time: name.first_seen,
    entry_time: name.first_seen,
    entry_price: 0,
    stop: 0,
    qty: 0,
    pattern_matches: patterns,
    chart: { interval: '1m', bars },
  };
}

function NameRow({
  name,
  news,
  active,
  onClick,
}: {
  name: WatchlistName;
  news: NameNews | undefined;
  active: boolean;
  onClick: () => void;
}) {
  const { money, at } = useFormat();
  const headlines = newsItems(news);
  const traded = name.trades.length > 0;
  const net = traded ? name.trades.reduce((sum, t) => sum + (t.net_inr ?? t.net_usd ?? 0), 0) : null;
  return (
    <button
      onClick={onClick}
      className={`w-full border-b border-black/5 px-3 py-3 text-left transition last:border-0 hover:bg-accent/5 ${active ? 'bg-accent/10' : ''}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="shrink-0 font-display font-semibold">{name.symbol}</span>
            <span className="rounded-full bg-black/5 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-ink/60">
              {name.flags.length} flag{name.flags.length === 1 ? '' : 's'}
            </span>
            {headlines.length > 0 && (
              <span
                title={`${headlines.length} headline(s) around this session`}
                className="shrink-0 rounded-full bg-sky-100 px-1.5 py-0.5 text-[10px] font-bold text-sky-700"
              >
                📰 {headlines.length}
              </span>
            )}
            {traded && (
              <span className="shrink-0 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-800">
                Traded
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-ink/55">
            First seen {at(name.first_seen)}
            {name.last_seen !== name.first_seen ? ` · last ${at(name.last_seen)}` : ''} ·{' '}
            {name.flags[0]?.reason.replaceAll('_', ' ')}
          </p>
        </div>
        <div className="shrink-0 text-right text-sm font-bold tabular-nums">
          <span className="text-emerald-700">+{name.max_day_chg_pct.toFixed(1)}%</span>
          <p className="mt-1 whitespace-nowrap text-[11px] font-medium text-ink/45">
            RVOL {name.max_rvol.toFixed(1)}×
          </p>
          {net !== null && (
            <p className={`mt-0.5 whitespace-nowrap text-[11px] ${pnlClass(net)}`}>
              {net >= 0 ? '+' : ''}
              {money(net)}
            </p>
          )}
        </div>
      </div>
    </button>
  );
}

function NameDetail({
  date,
  name,
  news,
  onBack,
}: {
  date: string;
  name: WatchlistName;
  news: NameNews | undefined;
  onBack: () => void;
}) {
  const market = useContext(MarketContext);
  const { money, at, dateLabel } = useFormat();
  const [bars, setBars] = useState<MomentumBar[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setBars(null);
    setError(null);
    market
      .fetchBars(name.symbol, date)
      .then(({ bars: next }) => !cancelled && setBars(next))
      .catch((err: unknown) =>
        !cancelled && setError(err instanceof Error ? err.message : 'Could not load candles'),
      );
    return () => {
      cancelled = true;
    };
  }, [date, name.symbol, market]);

  const record = useMemo(() => (bars ? asChartRecord(date, name, bars) : null), [bars, date, name]);
  const markers = useMemo(
    () => [
      ...name.flags.map((flag) => ({
        time: flag.time,
        label: `${at(flag.time)} ${flag.reason.replace(/^pattern:/, '')}`,
        kind: 'flag' as const,
      })),
      // Only headlines published while the market was open land on a candle;
      // earlier ones are listed in the news panel instead.
      ...newsItems(news).map((item) => ({
        time: item.published_at,
        label: `${at(item.published_at)} ${truncate(item.headline, 48)}`,
        kind: 'news' as const,
      })),
    ],
    [name.flags, news, at],
  );

  return (
    <section className="space-y-4 rounded-xl border border-black/10 bg-panel p-4 shadow-card">
      <button
        type="button"
        onClick={onBack}
        className="text-xs font-semibold text-accent lg:hidden"
      >
        ← Back to watchlist
      </button>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-ink/45">{dateLabel(date)}</p>
          <h2 className="font-display text-2xl">{name.symbol}</h2>
          <p className="text-xs text-ink/55">{name.strategies.map(strategyLabel).join(' · ')}</p>
        </div>
        <div className="flex gap-3">
          <div className="metric-chip text-right">
            <p className="text-xs text-ink/55">Max day change</p>
            <p className="font-display text-lg text-emerald-700">+{name.max_day_chg_pct.toFixed(2)}%</p>
          </div>
          <div className="metric-chip text-right">
            <p className="text-xs text-ink/55">Max RVOL</p>
            <p className="font-display text-lg">{name.max_rvol.toFixed(2)}×</p>
          </div>
        </div>
      </div>

      {/* Why it was on the watchlist */}
      <div className="overflow-hidden rounded-xl border border-black/10">
        <table className="w-full text-left text-sm tabular-nums">
          <thead className="bg-black/[0.025]">
            <tr className="text-xs font-bold uppercase tracking-wide text-ink/50">
              <th className="px-3 py-2">Flagged</th>
              <th className="px-3 py-2">Reason</th>
              <th className="px-3 py-2">Day chg</th>
              <th className="px-3 py-2">RVOL</th>
            </tr>
          </thead>
          <tbody>
            {name.flags.map((flag, i) => (
              <tr key={`${flag.time}-${i}`} className="border-t border-black/5">
                <td className="px-3 py-2 font-medium">{at(flag.time)}</td>
                <td className="px-3 py-2 text-ink/70">{flag.reason.replaceAll('_', ' ')}</td>
                <td className="px-3 py-2">{Number.isFinite(flag.day_chg_pct) ? `${flag.day_chg_pct.toFixed(2)}%` : '—'}</td>
                <td className="px-3 py-2">{Number.isFinite(flag.rvol) ? `${flag.rvol.toFixed(2)}×` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {name.details && name.details.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          {name.details.map((d) => (
            <div key={d.label} className="metric-chip">
              <p className="text-xs text-ink/55">{d.label}</p>
              <p className="mt-1 font-semibold">{d.value}</p>
            </div>
          ))}
        </div>
      )}

      <GateChecklist market={market.code} timeZone={market.locale.timeZone} name={name} />

      <NewsPanel date={date} name={name} news={news} />

      {name.trades.length > 0 && (
        <div className="metric-chip">
          <p className="text-xs text-ink/55">Paper trades on this name this session</p>
          {name.trades.map((t) => {
            const net = t.net_inr ?? t.net_usd;
            return (
              <p key={t._id} className="mt-1 text-sm">
                {strategyLabel(t.strategy)} · Buy {money(t.entry_price)} at {at(t.entry_time)}
                {t.exit_price != null ? ` → Sell ${money(t.exit_price)} at ${at(t.exit_time)}` : ' · Open'}
                {net != null && (
                  <span className={`ml-2 font-semibold ${pnlClass(net)}`}>
                    {net >= 0 ? '+' : ''}
                    {money(net)}
                  </span>
                )}
              </p>
            );
          })}
          <Link href={market.tradesHref} className="mt-1 inline-block text-xs font-semibold text-accent">
            Review on the trades page →
          </Link>
        </div>
      )}

      {/* Charts */}
      {error && <div className="metric-chip border-rose-200 py-6 text-rose-700">{error}</div>}
      {!error && !record && (
        <div className="metric-chip py-12 text-center text-sm text-ink/55">Loading candles…</div>
      )}
      {record && (
        <div className="space-y-3">
          <div>
            <h3 className="font-display text-lg">{name.symbol} · 1-minute chart</h3>
            <p className="text-xs text-ink/55">
              {market.barsNote} Amber lines mark each time the scanner
              flagged the name; blue dotted lines mark headlines published during market hours.
            </p>
          </div>
          <MomentumTradeChart trade={record} interval="1m" watchMarkers={markers} locale={market.locale} />
          <div>
            <h3 className="font-display text-lg">{name.symbol} · 5-minute chart</h3>
            <p className="text-xs text-ink/55">
              The same session resampled to the scanner&apos;s 5-minute decision timeframe.
            </p>
          </div>
          <MomentumTradeChart trade={record} interval="5m" watchMarkers={markers} locale={market.locale} />
        </div>
      )}

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

export function MomentumWatchlist({ market }: { market: WatchlistMarket }) {
  return (
    <MarketContext.Provider value={market}>
      <WatchlistView />
    </MarketContext.Provider>
  );
}

function WatchlistView() {
  const market = useContext(MarketContext);
  const { dateLabel } = useFormat();
  const [sessions, setSessions] = useState<WatchlistSession[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [openDates, setOpenDates] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mobileView, setMobileView] = useState<'list' | 'detail'>('list');
  const [news, setNews] = useState<Record<string, NameNews>>({});

  useEffect(() => {
    market
      .fetchSessions()
      .then(({ sessions: next }) => {
        setSessions(next);
        const first = next[0];
        const firstName = first?.names[0];
        setSelected(first && firstName ? nameKey(first.date, firstName.symbol) : null);
        setOpenDates(first ? new Set([first.date]) : new Set());
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : 'Could not load the watchlist'),
      )
      .finally(() => setLoading(false));
  }, [market]);

  // Headlines are looked up per open day, once, so the badges appear as a day
  // is expanded rather than costing a search for every name on every day.
  useEffect(() => {
    for (const session of sessions) {
      if (!openDates.has(session.date)) continue;
      const pending = session.names
        .map((n) => n.symbol)
        .filter((symbol) => news[nameKey(session.date, symbol)] === undefined);
      if (!pending.length) continue;
      setNews((prev) => ({ ...prev, ...Object.fromEntries(pending.map((s) => [nameKey(session.date, s), 'loading' as const])) }));
      fetchWatchlistNews(session.date, pending, market.code)
        .then(({ news: bySymbol }) =>
          setNews((prev) => ({
            ...prev,
            ...Object.fromEntries(pending.map((s) => [nameKey(session.date, s), bySymbol[s] ?? []])),
          })),
        )
        .catch((err: unknown) => {
          const error = { error: err instanceof Error ? err.message : 'News lookup failed' };
          setNews((prev) => ({ ...prev, ...Object.fromEntries(pending.map((s) => [nameKey(session.date, s), error])) }));
        });
    }
  }, [sessions, openDates, news, market.code]);

  const current = useMemo(() => {
    for (const session of sessions) {
      for (const name of session.names) {
        if (nameKey(session.date, name.symbol) === selected) return { date: session.date, name };
      }
    }
    return null;
  }, [sessions, selected]);

  const totalNames = sessions.reduce((sum, s) => sum + s.names.length, 0);

  return (
    <div className="space-y-5">
      <section className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-accent">Scanner review</p>
          <h1 className="font-display text-3xl tracking-tight">{market.title}</h1>
          <p className="mt-1 text-sm text-ink/65">{market.intro}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {market.links.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="inline-block rounded-full border border-black/10 px-3 py-1.5 text-xs font-semibold hover:bg-black/5"
              >
                {link.label}
              </Link>
            ))}
          </div>
        </div>
        <div className="flex gap-3">
          <div className="metric-chip text-right">
            <p className="text-xs text-ink/55">Sessions</p>
            <p className="font-display text-xl">{sessions.length}</p>
          </div>
          <div className="metric-chip text-right">
            <p className="text-xs text-ink/55">Names watched</p>
            <p className="font-display text-xl">{totalNames}</p>
          </div>
        </div>
      </section>

      {!loading && !error && sessions.length > 0 && (
        <section className="overflow-hidden rounded-xl border border-black/10 bg-panel shadow-card">
          <div className="border-b border-black/10 px-4 py-3">
            <h2 className="font-display text-lg">Day-wise watchlist</h2>
            <p className="text-xs text-ink/55">How many names were watched each session, and how many became trades.</p>
          </div>
          <div className="max-h-80 overflow-x-auto overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums">
              <thead className="sticky top-0 z-10 bg-panel">
                <tr className="text-xs font-bold uppercase tracking-wide text-ink/50">
                  <th className="px-4 py-2">Date</th>
                  <th className="px-4 py-2">Names</th>
                  <th className="px-4 py-2">Flags</th>
                  <th className="px-4 py-2">Traded</th>
                  <th className="px-4 py-2">With news</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map(({ date, names }) => (
                  <tr key={date} className="border-t border-black/5">
                    <td className="px-4 py-2 font-medium">{dateLabel(date)}</td>
                    <td className="px-4 py-2 text-ink/60">{names.length}</td>
                    <td className="px-4 py-2 text-ink/60">{names.reduce((sum, n) => sum + n.flags.length, 0)}</td>
                    <td className="px-4 py-2 text-ink/60">{names.filter((n) => n.trades.length > 0).length}</td>
                    <td className="px-4 py-2 text-ink/60">
                      {names.every((n) => Array.isArray(news[nameKey(date, n.symbol)]))
                        ? names.filter((n) => newsItems(news[nameKey(date, n.symbol)]).length > 0).length
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {loading && (
        <div className="metric-chip py-12 text-center text-sm text-ink/55">Loading watchlist…</div>
      )}
      {error && <div className="metric-chip border-rose-200 py-6 text-rose-700">{error}</div>}
      {!loading && !error && sessions.length === 0 && (
        <div className="metric-chip py-12 text-center text-sm text-ink/55">
          {market.empty}
        </div>
      )}

      {!loading && !error && sessions.length > 0 && (
        <div className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
          <aside
            className={`overflow-hidden rounded-xl border border-black/10 bg-panel shadow-card ${mobileView === 'detail' ? 'hidden lg:block' : 'block'}`}
          >
            {sessions.map(({ date, names }) => {
              const isOpen = openDates.has(date);
              return (
                <section key={date} className="border-b border-black/5 last:border-0">
                  <button
                    type="button"
                    onClick={() =>
                      setOpenDates((open) => {
                        const next = new Set(open);
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
                        ({names.length} name{names.length === 1 ? '' : 's'})
                      </span>
                    </span>
                    <span className="text-base leading-none" aria-hidden="true">
                      {isOpen ? '−' : '+'}
                    </span>
                  </button>
                  {isOpen &&
                    names.map((name) => (
                      <NameRow
                        key={name.symbol}
                        name={name}
                        news={news[nameKey(date, name.symbol)]}
                        active={nameKey(date, name.symbol) === selected}
                        onClick={() => {
                          setSelected(nameKey(date, name.symbol));
                          setMobileView('detail');
                        }}
                      />
                    ))}
                </section>
              );
            })}
          </aside>

          <div className={mobileView === 'list' ? 'hidden lg:block' : 'block'}>
            {current && (
              <NameDetail
                date={current.date}
                name={current.name}
                news={news[nameKey(current.date, current.name.symbol)]}
                onBack={() => setMobileView('list')}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
