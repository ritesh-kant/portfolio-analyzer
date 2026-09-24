import { gunzipSync } from 'node:zlib';

import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

const IST = 'Asia/Kolkata';
const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const SYMBOL_RE = /^[A-Z0-9&._-]{1,30}$/;

/** YYYY-MM-DD of `ms` in IST. */
const istDate = (ms: number) => new Date(ms + IST_OFFSET_MS).toISOString().slice(0, 10);
/** UTC instant of IST midnight at the start of `date`. */
const istMidnight = (date: string) => Date.parse(`${date}T00:00:00Z`) - IST_OFFSET_MS;

/**
 * The NSE attention watchlist, one entry per session.
 *
 * `mt_attention` holds one row per promotion — a name that cleared the day-
 * change and RVOL floors and printed a completed pattern — so a name can
 * appear several times in one session. They are folded per symbol here so the
 * page lists each name once, with every flag it earned kept as a marker.
 * Traded names are joined from `mt_positions` so the page can link through.
 */
export const handler = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;
  const requested = Number(event.queryStringParameters?.days ?? 10);
  const days = Number.isFinite(requested) ? Math.min(Math.max(Math.floor(requested), 1), 60) : 10;

  const dates = (
    await db
      .collection('mt_attention')
      .aggregate<{ _id: string }>([
        { $group: { _id: { $dateToString: { format: '%Y-%m-%d', date: '$time', timezone: IST } } } },
        { $sort: { _id: -1 } },
        { $limit: days },
      ])
      .toArray()
  ).map((d) => d._id);
  if (!dates.length) return json(200, { sessions: [], count: 0 });

  const from = new Date(istMidnight(dates[dates.length - 1]!));
  const to = new Date(istMidnight(dates[0]!) + DAY_MS);
  const [events, positions] = await Promise.all([
    db
      .collection('mt_attention')
      .find(
        { time: { $gte: from, $lt: to } },
        {
          projection: {
            _id: 0, strategy: 1, symbol: 1, time: 1, day_chg_pct: 1, rvol: 1, reason: 1,
            candle_tags: 1, 'evidence.pattern_matches': 1,
          },
        },
      )
      .sort({ time: 1 })
      .toArray(),
    db
      .collection('mt_positions')
      .find(
        { entry_time: { $gte: from, $lt: to } },
        { projection: { symbol: 1, entry_time: 1, exit_time: 1, entry_price: 1, exit_price: 1, net_inr: 1, status: 1, strategy: 1 } },
      )
      .toArray(),
  ]);

  type Flag = {
    time: string;
    strategy?: string;
    reason: string;
    day_chg_pct: number;
    rvol: number;
    candle_tags: string[];
    pattern_matches: unknown[];
  };
  type Name = {
    symbol: string;
    first_seen: string;
    last_seen: string;
    max_day_chg_pct: number;
    max_rvol: number;
    strategies: string[];
    flags: Flag[];
    trades: Record<string, unknown>[];
  };
  const sessions = new Map<string, Map<string, Name>>(dates.map((d) => [d, new Map()]));
  for (const doc of events) {
    const time = doc.time as Date;
    const date = istDate(time.getTime());
    const names = sessions.get(date);
    if (!names) continue;
    const symbol = String(doc.symbol);
    const name = names.get(symbol) ?? {
      symbol,
      first_seen: time.toISOString(),
      last_seen: time.toISOString(),
      max_day_chg_pct: -Infinity,
      max_rvol: -Infinity,
      strategies: [],
      flags: [],
      trades: [],
    };
    name.last_seen = time.toISOString();
    name.max_day_chg_pct = Math.max(name.max_day_chg_pct, Number(doc.day_chg_pct ?? -Infinity));
    name.max_rvol = Math.max(name.max_rvol, Number(doc.rvol ?? -Infinity));
    if (doc.strategy && !name.strategies.includes(doc.strategy)) name.strategies.push(doc.strategy);
    name.flags.push({
      time: time.toISOString(),
      strategy: doc.strategy,
      reason: String(doc.reason ?? ''),
      day_chg_pct: Number(doc.day_chg_pct),
      rvol: Number(doc.rvol),
      candle_tags: doc.candle_tags ?? [],
      pattern_matches: doc.evidence?.pattern_matches ?? [],
    });
    names.set(symbol, name);
  }
  for (const p of positions) {
    const names = sessions.get(istDate((p.entry_time as Date).getTime()));
    const name = names?.get(String(p.symbol));
    if (name) name.trades.push({ ...p, _id: String(p._id) });
  }

  return json(200, {
    sessions: dates.map((date) => ({
      date,
      names: [...sessions.get(date)!.values()].sort((a, b) => a.first_seen.localeCompare(b.first_seen)),
    })),
    count: dates.length,
  });
});

// ── candles ──────────────────────────────────────────────────────────────────

const INSTRUMENTS_URL = 'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz';
const UPSTOX = 'https://api.upstox.com';
type Instrument = { key: string; name: string };
let instruments: { at: number; bySymbol: Map<string, Instrument> } | null = null;

/** trading_symbol → Upstox instrument for NSE_EQ/EQ, held for a day per container. */
async function instrument(symbol: string): Promise<Instrument | undefined> {
  if (!instruments || Date.now() - instruments.at > DAY_MS) {
    const res = await fetch(INSTRUMENTS_URL);
    if (!res.ok) throw new Error(`instrument master HTTP ${res.status}`);
    const rows = JSON.parse(gunzipSync(Buffer.from(await res.arrayBuffer())).toString('utf8')) as {
      segment?: string;
      instrument_type?: string;
      trading_symbol?: string;
      instrument_key?: string;
      name?: string;
    }[];
    const bySymbol = new Map<string, Instrument>();
    for (const row of rows) {
      if (row.segment === 'NSE_EQ' && row.instrument_type === 'EQ' && row.trading_symbol && row.instrument_key) {
        bySymbol.set(row.trading_symbol, { key: row.instrument_key, name: row.name ?? '' });
      }
    }
    instruments = { at: Date.now(), bySymbol };
  }
  return instruments.bySymbol.get(symbol);
}

/**
 * One session's exchange 1-minute candles for one name, from Upstox's public
 * candle API (no token needed). Watched-but-untraded names have no stored
 * snapshot, so these are fetched on demand; today's session uses the intraday
 * endpoint and is therefore live up to the last completed minute.
 */
export const bars = requireAuth(async (event) => {
  const symbol = event.queryStringParameters?.symbol ?? '';
  const date = event.queryStringParameters?.date ?? '';
  if (!SYMBOL_RE.test(symbol) || !DATE_RE.test(date)) {
    return json(400, { error: 'symbol and date (YYYY-MM-DD) are required' });
  }
  const inst = await instrument(symbol);
  if (!inst) return json(404, { error: `no NSE equity instrument for ${symbol}` });

  const encoded = encodeURIComponent(inst.key);
  const path =
    date === istDate(Date.now())
      ? `/v3/historical-candle/intraday/${encoded}/minutes/1`
      : `/v3/historical-candle/${encoded}/minutes/1/${date}/${date}`;
  const res = await fetch(`${UPSTOX}${path}`, { headers: { Accept: 'application/json' } });
  if (!res.ok) return json(502, { error: `upstox candles HTTP ${res.status}` });
  const body = (await res.json()) as { data?: { candles?: [string, number, number, number, number, number][] } };
  // Upstox returns newest first; the chart wants ascending start times.
  const candles = (body.data?.candles ?? [])
    .map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }))
    .sort((a, b) => Date.parse(a.time) - Date.parse(b.time));
  return json(200, { symbol, date, interval: '1m', source: 'upstox', bars: candles });
});

// ── news ─────────────────────────────────────────────────────────────────────

/*
 * Headlines for a watched name around one session, from Google News RSS.
 *
 * Context only, like `news_context.py` on the trade page: nothing here reaches
 * the engine. Google News is used because it is the one source that answers
 * for an arbitrary past date without a key — the stored feeds cannot: the
 * news-trader pipeline (`nt_signals`) has been paused since 2026-06-26, and
 * BSE's announcement API refuses per-scrip queries off a browser (Akamai 403).
 * A search on the company name also returns peers and sector round-ups, so an
 * item is kept only when its headline names the company or the symbol.
 */

const NEWS_TTL_MS = 10 * 60 * 1000;
const MAX_NEWS_SYMBOLS = 40;
const newsCache = new Map<string, { at: number; items: NewsItem[] }>();

export interface NewsItem {
  headline: string;
  publisher: string;
  url: string | null;
  published_at: string;
}

/** "CONCORD BIOTECH LIMITED" → "Concord Biotech"; the exchange's legal-suffix noise dropped. */
function companyName(raw: string): string {
  return raw
    .replace(/\b(LIMITED|LTD\.?|LIMIT|LIMI|LIM)\b/gi, '')
    .replace(/[.,]+$/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function decodeXml(value: string): string {
  return value
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&#(\d+);/g, (_m, code: string) => String.fromCharCode(Number(code)))
    .replace(/&amp;/g, '&');
}

const tag = (xml: string, name: string) => {
  const match = xml.match(new RegExp(`<${name}[^>]*>([\\s\\S]*?)</${name}>`));
  return match ? decodeXml(match[1]!).trim() : '';
};

/** Shift a YYYY-MM-DD by whole days. */
const addDays = (date: string, days: number) => new Date(Date.parse(`${date}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);

/** The weekday before `date` — Monday's overnight window starts at Friday's close. */
function previousWeekday(date: string): string {
  let prev = addDays(date, -1);
  while ([0, 6].includes(new Date(`${prev}T00:00:00Z`).getUTCDay())) prev = addDays(prev, -1);
  return prev;
}

/** The instant `hh:mm` on `date` is reached in `timeZone` (DST-aware). */
function zonedTime(date: string, hhmm: string, timeZone: string): number {
  const guess = Date.parse(`${date}T${hhmm}:00Z`);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(guess));
  const part = (type: string) => Number(parts.find((p) => p.type === type)!.value);
  const asLocal = Date.UTC(part('year'), part('month') - 1, part('day'), part('hour'), part('minute'), part('second'));
  return guess - (asLocal - guess);
}

// ── US company names ─────────────────────────────────────────────────────────
// Nasdaq's public screener lists every Nasdaq/NYSE/AMEX stock with its name.
// SEC's ticker file would do too, but it refuses any request whose User-Agent
// does not carry a contact email.
const NASDAQ_LIST_URL = 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true';
const BROWSER_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
let usNames: { at: number; bySymbol: Map<string, string> } | null = null;

async function usCompanyName(symbol: string): Promise<string> {
  if (!usNames || Date.now() - usNames.at > DAY_MS) {
    const res = await fetch(NASDAQ_LIST_URL, { headers: { 'User-Agent': BROWSER_UA, Accept: 'application/json' } });
    if (!res.ok) throw new Error(`nasdaq listing HTTP ${res.status}`);
    const body = (await res.json()) as { data?: { rows?: { symbol: string; name: string }[] } };
    usNames = { at: Date.now(), bySymbol: new Map((body.data?.rows ?? []).map((r) => [r.symbol.trim(), r.name])) };
  }
  const raw = usNames.bySymbol.get(symbol) ?? '';
  // "SoundHound AI Inc Class A Common Stock" → "SoundHound AI"
  return raw
    .replace(/\b(Common Stock|Ordinary Shares?|American Depositary Shares?|Class [A-Z]|Inc\.?|Corp\.?|Corporation|Company|Co\.|Ltd\.?|Limited|plc|Holdings?|N\.V\.|S\.A\.)(?=\s|,|$)/gi, '')
    .replace(/[,.]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

type Market = 'NSE' | 'US';
const MARKETS: Record<Market, {
  timeZone: string;
  close: string;
  edition: { hl: string; gl: string; ceid: string };
  name: (symbol: string) => Promise<string>;
}> = {
  NSE: {
    timeZone: 'Asia/Kolkata',
    close: '15:30',
    edition: { hl: 'en-IN', gl: 'IN', ceid: 'IN:en' },
    name: async (symbol) => {
      const inst = await instrument(symbol);
      return inst ? companyName(inst.name) : '';
    },
  },
  US: {
    timeZone: 'America/New_York',
    close: '16:00',
    edition: { hl: 'en-US', gl: 'US', ceid: 'US:en' },
    name: usCompanyName,
  },
};

async function sessionNews(symbol: string, date: string, market: Market): Promise<NewsItem[]> {
  const cacheKey = `${market}:${symbol}:${date}`;
  const cached = newsCache.get(cacheKey);
  if (cached && Date.now() - cached.at < NEWS_TTL_MS) return cached.items;

  const m = MARKETS[market];
  const prev = previousWeekday(date);
  const name = await m.name(symbol).catch(() => '');
  const query = `${name ? `"${symbol}" OR "${name}"` : `"${symbol}"`} after:${addDays(prev, -1)} before:${addDays(date, 1)}`;
  const url = `https://news.google.com/rss/search?${new URLSearchParams({ q: query, ...m.edition })}`;
  const res = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!res.ok) throw new Error(`google news HTTP ${res.status}`);
  const xml = await res.text();

  // From the previous session's close to the end of this session's day, in
  // the market's own clock.
  const from = zonedTime(prev, m.close, m.timeZone);
  const to = zonedTime(addDays(date, 1), '00:00', m.timeZone);
  // The name's first two words ("Concord Biotech", "SoundHound AI"): enough to
  // reject peers, loose enough to survive the exchange's abbreviations.
  const nameKey = name.split(' ').slice(0, 2).join(' ').toLowerCase();
  const symbolRe = new RegExp(`\\b${symbol.replace(/[.*+?^${}()|[\]\\&]/g, '\\$&')}\\b`, market === 'US' ? '' : 'i');
  const seen = new Set<string>();
  const items: NewsItem[] = [];
  for (const block of xml.match(/<item>[\s\S]*?<\/item>/g) ?? []) {
    const publisher = tag(block, 'source');
    let headline = tag(block, 'title');
    if (publisher && headline.endsWith(` - ${publisher}`)) headline = headline.slice(0, -publisher.length - 3);
    const published = Date.parse(tag(block, 'pubDate'));
    if (!headline || !Number.isFinite(published) || published < from || published >= to) continue;
    const lower = headline.toLowerCase();
    if (!symbolRe.test(headline) && !(nameKey && lower.includes(nameKey))) continue;
    if (seen.has(lower)) continue;
    seen.add(lower);
    items.push({ headline, publisher, url: tag(block, 'link') || null, published_at: new Date(published).toISOString() });
  }
  items.sort((a, b) => a.published_at.localeCompare(b.published_at));
  newsCache.set(cacheKey, { at: Date.now(), items });
  return items;
}

/** Headlines for several watched names on one session: `?date=YYYY-MM-DD&symbols=A,B[&market=US]`. */
export const news = requireAuth(async (event) => {
  const date = event.queryStringParameters?.date ?? '';
  const market: Market = event.queryStringParameters?.market === 'US' ? 'US' : 'NSE';
  const symbols = [...new Set((event.queryStringParameters?.symbols ?? '').split(',').filter(Boolean))];
  if (!DATE_RE.test(date) || !symbols.length || symbols.length > MAX_NEWS_SYMBOLS || !symbols.every((s) => SYMBOL_RE.test(s))) {
    return json(400, { error: `date (YYYY-MM-DD) and 1-${MAX_NEWS_SYMBOLS} symbols are required` });
  }
  const out: Record<string, NewsItem[] | { error: string }> = {};
  // A handful at a time: Google throttles a burst of 30 parallel searches.
  for (let i = 0; i < symbols.length; i += 6) {
    await Promise.all(
      symbols.slice(i, i + 6).map(async (symbol) => {
        try {
          out[symbol] = await sessionNews(symbol, date, market);
        } catch (err) {
          out[symbol] = { error: err instanceof Error ? err.message : String(err) };
        }
      }),
    );
  }
  return json(200, { date, market, source: 'google_news', news: out });
});

// ── US watchlist ─────────────────────────────────────────────────────────────

/**
 * The US arm's watchlist in the same day-wise shape as the NSE one.
 *
 * `mt_us_watchlist` keeps one document per session holding every name the
 * screen SAW; the watchlist is the names that passed it at least once. Their
 * flags are when the screen first passed them plus the attention promotions
 * the session logged to `mt_us_candidates`. Money stays in dollars.
 */
export const usHandler = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;
  const requested = Number(event.queryStringParameters?.days ?? 10);
  const days = Number.isFinite(requested) ? Math.min(Math.max(Math.floor(requested), 1), 60) : 10;

  const docs = await db
    .collection('mt_us_watchlist')
    .find({ market: 'US' }, { projection: { date: 1, names: 1, considered: 1, missing_criteria: 1 } })
    .sort({ date: -1 })
    .limit(days)
    .toArray();
  if (!docs.length) return json(200, { sessions: [], count: 0 });

  const dates = docs.map((d) => String(d.date));
  const from = new Date(zonedTime(dates[dates.length - 1]!, '00:00', 'America/New_York'));
  const to = new Date(zonedTime(addDays(dates[0]!, 1), '00:00', 'America/New_York'));
  const [attention, positions] = await Promise.all([
    db
      .collection('mt_us_candidates')
      .find(
        { kind: 'attention', time: { $gte: from, $lt: to } },
        { projection: { _id: 0, symbol: 1, time: 1, reason: 1, day_chg_pct: 1, rvol: 1, candle_tags: 1, 'evidence.pattern_matches': 1, strategy: 1 } },
      )
      .sort({ time: 1 })
      .toArray(),
    db
      .collection('mt_us_positions')
      .find(
        { entry_time: { $gte: from, $lt: to } },
        { projection: { symbol: 1, entry_time: 1, exit_time: 1, entry_price: 1, exit_price: 1, net_usd: 1, status: 1, strategy: 1 } },
      )
      .toArray(),
  ]);
  const etDate = (d: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(d);

  const sessions = docs.map((doc) => {
    const date = String(doc.date);
    type USName = {
      symbol: string; passed: boolean; reason: string; price: number; day_chg_pct: number;
      rvol: number | null; float_shares: number | null; observed_at?: string;
      first_seen?: string; first_passed_at?: string | null;
    };
    const watched = ((doc.names ?? []) as USName[]).filter((n) => n.passed);
    const names = watched.map((n) => {
      const firstPassed = n.first_passed_at ?? n.first_seen ?? n.observed_at ?? `${date}T09:30:00-04:00`;
      const promotions = attention.filter((a) => a.symbol === n.symbol && etDate(a.time as Date) === date);
      const flags = [
        {
          time: new Date(firstPassed).toISOString(),
          reason: 'passed_screen',
          day_chg_pct: Number(n.day_chg_pct),
          rvol: n.rvol == null ? Number.NaN : Number(n.rvol),
          candle_tags: [] as string[],
          pattern_matches: [] as unknown[],
        },
        ...promotions.map((a) => ({
          time: (a.time as Date).toISOString(),
          strategy: a.strategy,
          reason: String(a.reason ?? 'attention'),
          day_chg_pct: Number(a.day_chg_pct),
          rvol: Number(a.rvol),
          candle_tags: a.candle_tags ?? [],
          pattern_matches: a.evidence?.pattern_matches ?? [],
        })),
      ].sort((a, b) => a.time.localeCompare(b.time));
      const numbers = (key: 'day_chg_pct' | 'rvol') => flags.map((f) => f[key]).filter(Number.isFinite);
      return {
        symbol: n.symbol,
        first_seen: flags[0]!.time,
        last_seen: flags[flags.length - 1]!.time,
        max_day_chg_pct: Math.max(...numbers('day_chg_pct'), Number(n.day_chg_pct)),
        max_rvol: Math.max(...numbers('rvol'), 0),
        strategies: [...new Set(promotions.map((a) => a.strategy).filter(Boolean))],
        flags,
        trades: positions
          .filter((p) => p.symbol === n.symbol && etDate(p.entry_time as Date) === date)
          .map((p) => ({ ...p, _id: String(p._id) })),
        details: [
          { label: 'Price', value: `$${Number(n.price).toFixed(2)}` },
          { label: 'Float', value: n.float_shares == null ? 'unknown' : `${(n.float_shares / 1e6).toFixed(1)}M shares` },
          { label: 'Latest screen verdict', value: n.reason.replaceAll('_', ' ') },
        ],
      };
    });
    names.sort((a, b) => a.first_seen.localeCompare(b.first_seen));
    return { date, names, screened: doc.considered ?? null, missing_criteria: doc.missing_criteria ?? [] };
  });
  return json(200, { sessions, count: sessions.length });
});

/**
 * A watched US name's session bars, as the US session saved them
 * (`mt_us_watch_bars`), falling back to a closed trade's own snapshot.
 */
export const usBars = requireAuth(async (event) => {
  const symbol = event.queryStringParameters?.symbol ?? '';
  const date = event.queryStringParameters?.date ?? '';
  if (!SYMBOL_RE.test(symbol) || !DATE_RE.test(date)) {
    return json(400, { error: 'symbol and date (YYYY-MM-DD) are required' });
  }
  await connect();
  const db = mongoose.connection.db!;
  const saved = await db.collection('mt_us_watch_bars').findOne({ market: 'US', date, symbol });
  let barsOut = (saved?.bars ?? []) as unknown[];
  if (!barsOut.length) {
    const from = new Date(zonedTime(date, '00:00', 'America/New_York'));
    const to = new Date(zonedTime(addDays(date, 1), '00:00', 'America/New_York'));
    const trade = await db
      .collection('mt_us_positions')
      .find({ symbol, entry_time: { $gte: from, $lt: to }, 'chart.bars.0': { $exists: true } }, { projection: { 'chart.bars': 1 } })
      .sort({ exit_time: -1 })
      .limit(1)
      .next();
    barsOut = (trade?.chart?.bars ?? []) as unknown[];
  }
  return json(200, { symbol, date, interval: '1m', source: saved ? 'us_session' : 'trade_snapshot', bars: barsOut });
});
