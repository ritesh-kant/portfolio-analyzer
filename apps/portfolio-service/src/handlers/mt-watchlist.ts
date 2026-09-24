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
 * What a watched company itself disclosed around one session — never media.
 *
 *   NSE: the symbol's own corporate announcements (the exchange filing, with
 *        its summary and PDF).
 *   US:  SEC EDGAR filings. An 8-K / 6-K's Exhibit 99.1 is the company's press
 *        release — the wire text itself, with an official acceptance time — so
 *        its headline stands in for the wire.
 *
 * Media was dropped after an audit on 2026-09-24: across 16 US names up 15%+
 * that day, 0 of 42 media items (StocksToTrade/timothysykes recaps, Benzinga
 * "why it's trending", RTTNews movers, Yahoo) predated the move, against 15 of
 * 17 EDGAR filings; on NSE, RSS items named the company, not the ticker, and
 * mostly described a move after it happened. The wires themselves refuse
 * scripted access (Business Wire/Accesswire 403) and PR Newswire also carries
 * paid stock promotion, so a wire is not proof the company said it.
 *
 * Context only: nothing here reaches the engine. The window opens at the start
 * of the previous trading day, because a catalyst filed that morning can drive
 * an after-hours move (GCTK: 8-K at 09:21, move from 16:21).
 */

const NEWS_TTL_MS = 10 * 60 * 1000;
const MAX_NEWS_SYMBOLS = 40;
const newsCache = new Map<string, { at: number; items: NewsItem[] }>();

/** news = the company announced something; offering = new shares (dilution); filing = other paperwork. */
export type NewsKind = 'news' | 'offering' | 'filing';

export interface NewsItem {
  headline: string;
  publisher: string;
  url: string | null;
  published_at: string;
  kind: NewsKind;
  /** Filing summary or press-release opening, when there is one. */
  text: string | null;
}

const BROWSER_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

function decodeEntities(value: string): string {
  return value
    .replace(/&nbsp;|&#160;|&#8203;/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;|&ldquo;|&rdquo;/g, '"')
    .replace(/&#39;|&apos;|&rsquo;|&lsquo;/g, "'")
    .replace(/&mdash;|&ndash;/g, '-')
    .replace(/&trade;|&reg;/g, '')
    .replace(/&sup2;/g, '²')
    .replace(/&sup3;/g, '³')
    .replace(/&hellip;/g, '...')
    .replace(/&eacute;/g, 'é')
    .replace(/&#(\d+);/g, (_m, code: string) => String.fromCharCode(Number(code)))
    .replace(/&amp;/g, '&');
}

const htmlText = (html: string) => decodeEntities(html.replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();

/** Shift a YYYY-MM-DD by whole days. */
const addDays = (date: string, days: number) => new Date(Date.parse(`${date}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);

/** The weekday before `date` — Monday's window starts on Friday. */
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

// ── NSE: the symbol's own filings ────────────────────────────────────────────
// The exchange-wide feed only carries the latest ~20 filings, so the symbol is
// asked for. This endpoint answers without the homepage cookie (the homepage
// itself 403s from AWS), unlike the option chain.
const NSE_ANN_URL = 'https://www.nseindia.com/api/corporate-announcements';

interface NseRow {
  symbol?: string;
  desc?: string;
  sort_date?: string;
  an_dt?: string;
  attchmntText?: string;
  attchmntFile?: string;
}

/** "2026-09-24 17:44:16" / "24-Sep-2026 17:44:16", IST wall clock → epoch ms. */
function nseTime(row: NseRow): number {
  if (row.sort_date && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(row.sort_date)) {
    return Date.parse(`${row.sort_date.replace(' ', 'T')}+05:30`);
  }
  const m = row.an_dt?.match(/^(\d{2})-([A-Za-z]{3})-(\d{4}) (\d{2}:\d{2}:\d{2})$/);
  return m ? Date.parse(`${m[2]} ${m[1]} ${m[3]} ${m[4]} GMT+0530`) : NaN;
}

/** Procedural NSE filings — never market-moving on their own. */
const NSE_ROUTINE = /analysts?\/institutional investor meet|shareholders? meeting|trading window|newspaper publication|compliance certificate|loss of share certificate|duplicate share certificate|record date|book closure|investor presentation|change in (registrar|rta)/i;

/** Fund-raising filings on NSE: new shares are coming. */
const NSE_OFFERING = /allotment|preferential issue|qualified institutions? placement|\bqip\b|rights issue|fund raising|issue of (equity|securities)|warrants/i;

const ddmmyyyy = (date: string) => date.split('-').reverse().join('-');

async function nseFilings(symbol: string, date: string, from: number, to: number): Promise<NewsItem[]> {
  const params = new URLSearchParams({
    index: 'equities', symbol, from_date: ddmmyyyy(previousWeekday(date)), to_date: ddmmyyyy(date),
  });
  const res = await fetch(`${NSE_ANN_URL}?${params}`, {
    headers: {
      'User-Agent': BROWSER_UA,
      Accept: 'application/json, text/plain, */*',
      Referer: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements',
    },
  });
  if (!res.ok) throw new Error(`nse announcements HTTP ${res.status}`);
  const rows = (await res.json()) as NseRow[];
  const items: NewsItem[] = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    const at = nseTime(row);
    if (!row.desc || !Number.isFinite(at) || at < from || at >= to) continue;
    const text = row.attchmntText?.trim() || null;
    const kind: NewsKind = NSE_OFFERING.test(`${row.desc} ${text ?? ''}`)
      ? 'offering'
      : NSE_ROUTINE.test(row.desc) ? 'filing' : 'news';
    items.push({
      // "General Updates" is NSE's catch-all; the substance is in the text.
      headline: /^general updates?$/i.test(row.desc) && text ? text.slice(0, 160) : row.desc,
      publisher: 'NSE filing',
      url: row.attchmntFile || null,
      published_at: new Date(at).toISOString(),
      kind,
      text,
    });
  }
  return items;
}

// ── US: SEC EDGAR ────────────────────────────────────────────────────────────
// SEC refuses requests whose User-Agent carries no contact. Set SEC_USER_AGENT
// to "<app name> <your contact email>" in production.
const SEC_UA = process.env.SEC_USER_AGENT || 'portfolio-analyzer research-contact@example.org';
const secGet = (url: string) => fetch(url, { headers: { 'User-Agent': SEC_UA, Accept: 'application/json, text/html' } });
let secCiks: { at: number; byTicker: Map<string, number> } | null = null;
/** Accession → press-release headline/opening; filings never change once accepted. */
const exhibitCache = new Map<string, { headline: string; text: string } | null>();

async function secCik(symbol: string): Promise<number | undefined> {
  if (!secCiks || Date.now() - secCiks.at > DAY_MS) {
    const res = await secGet('https://www.sec.gov/files/company_tickers.json');
    if (!res.ok) throw new Error(`sec ticker list HTTP ${res.status}`);
    const body = (await res.json()) as Record<string, { ticker: string; cik_str: number }>;
    secCiks = { at: Date.now(), byTicker: new Map(Object.values(body).map((r) => [r.ticker.toUpperCase(), r.cik_str])) };
  }
  return secCiks.byTicker.get(symbol.toUpperCase());
}

/** Forms that register or price new shares — dilution, not a catalyst. */
const SEC_OFFERING = /^(424B\d*|S-1|S-3|F-1|F-3|S-1MEF|F-1MEF|FWP|EFFECT)(\/A)?$/;

const SEC_FORM_NAMES: Record<string, string> = {
  '424B': 'Prospectus: shares being sold',
  'S-1': 'Registration of new shares',
  'S-3': 'Shelf registration of new shares',
  'F-1': 'Registration of new shares (foreign issuer)',
  'F-3': 'Shelf registration of new shares (foreign issuer)',
  EFFECT: 'Share registration declared effective',
  FWP: 'Offering free-writing prospectus',
  '6-K': 'Foreign issuer report',
  '10-Q': 'Quarterly report',
  '10-K': 'Annual report',
  '4': 'Insider transaction',
  '3': 'New insider',
  '144': 'Insider intends to sell',
  'SC 13G': 'Ownership stake (passive)',
  'SC 13D': 'Ownership stake (active)',
  DEF14A: 'Proxy statement',
  '425': 'Merger communication',
};

const EIGHT_K_ITEMS: Record<string, string> = {
  '1.01': 'material agreement',
  '1.02': 'agreement terminated',
  '2.01': 'acquisition/disposal completed',
  '2.02': 'results',
  '2.03': 'new debt',
  '3.01': 'listing-rule notice',
  '3.02': 'unregistered share sale',
  '3.03': 'shareholder rights changed',
  '4.01': 'auditor change',
  '5.02': 'director/officer change',
  '5.03': 'charter/bylaws amended',
  '5.07': 'shareholder vote',
  '7.01': 'Reg FD disclosure',
  '8.01': 'other event',
};

function formName(form: string, items: string): string {
  if (form === '8-K' || form === '8-K/A') {
    const named = items.split(',').map((i) => EIGHT_K_ITEMS[i.trim()]).filter(Boolean);
    return named.length ? `8-K: ${named.join(', ')}` : '8-K';
  }
  const base = form.replace(/\/A$/, '');
  const name = SEC_FORM_NAMES[base] ?? SEC_FORM_NAMES[base.replace(/\d+$/, '')];
  return name ? `${form}: ${name}` : form;
}

/**
 * Exhibit 99's opening: "Exhibit 99.1 <headline> <CITY>, <Month> <d>, <yyyy> /PRNewswire/ - <body>".
 * The headline is what precedes the dateline.
 */
function pressRelease(html: string): { headline: string; text: string } | null {
  let text = htmlText(html);
  const lead = text.slice(0, 250).search(/exhibit\s*99(\.\d+)?/i);
  if (lead >= 0) text = text.slice(lead).replace(/^(?:exhibit\s*99(?:\.\d+)?\s*)+/i, '');
  if (!text) return null;
  const month = '(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\\.?';
  // The city is upper-case ("DENVER,", "MIRAMAR, Fla.,", "WOODS CROSS, Utah /"),
  // which is what keeps "...$1.5 Million Private Placement" whole.
  const dateline = new RegExp(`\\s+[A-Z][A-Z.' -]{1,40},(?:\\s*[A-Z][A-Za-z.' ]{1,30},?)?(?:\\s+and\\s+[A-Z][A-Za-z.' ,-]{1,40})?\\s*[/:]?\\s*${month}\\s+\\d{1,2},?\\s+20\\d\\d`);
  const cut = text.slice(0, 400).search(dateline);
  const headline = (cut > 15 ? text.slice(0, cut) : text.slice(0, 160)).trim();
  return { headline, text: text.slice(headline.length, headline.length + 400).trim() };
}

async function exhibit99(cik: number, accession: string): Promise<{ headline: string; text: string; url: string } | null> {
  const base = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}`;
  if (exhibitCache.has(accession)) {
    const hit = exhibitCache.get(accession);
    return hit ? { ...hit, url: `${base}/` } : null;
  }
  const idx = await secGet(`${base}/index.json`);
  if (!idx.ok) return null;
  const files = ((await idx.json()) as { directory?: { item?: { name: string }[] } }).directory?.item ?? [];
  const ex = files.map((f) => f.name).find((n) => /ex[-_]?99|exhibit[-_]?99/i.test(n) && /\.html?$/i.test(n));
  if (!ex) {
    exhibitCache.set(accession, null);
    return null;
  }
  const doc = await secGet(`${base}/${ex}`);
  const release = doc.ok ? pressRelease(await doc.text()) : null;
  exhibitCache.set(accession, release);
  return release ? { ...release, url: `${base}/${ex}` } : null;
}

interface SecRecent {
  form: string[];
  acceptanceDateTime: string[];
  accessionNumber: string[];
  items: string[];
}

async function secFilings(symbol: string, from: number, to: number): Promise<NewsItem[]> {
  const cik = await secCik(symbol);
  if (!cik) return [];   // warrants/units and some ADRs have no ticker row of their own
  const res = await secGet(`https://data.sec.gov/submissions/CIK${String(cik).padStart(10, '0')}.json`);
  if (!res.ok) throw new Error(`sec submissions HTTP ${res.status}`);
  const recent = ((await res.json()) as { filings: { recent: SecRecent } }).filings.recent;
  const items: NewsItem[] = [];
  for (let i = 0; i < recent.form.length; i += 1) {
    const at = Date.parse(recent.acceptanceDateTime[i]!);
    if (!Number.isFinite(at) || at < from || at >= to) continue;
    const form = recent.form[i]!;
    const accession = recent.accessionNumber[i]!;
    const indexUrl = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}/${accession}-index.htm`;
    const label = formName(form, recent.items[i] ?? '');
    const release = /^(8-K|6-K)/.test(form) ? await exhibit99(cik, accession).catch(() => null) : null;
    items.push({
      headline: release?.headline || label,
      publisher: release ? `SEC ${label}` : 'SEC EDGAR',
      url: release?.url ?? indexUrl,
      published_at: new Date(at).toISOString(),
      kind: SEC_OFFERING.test(form) ? 'offering' : release ? 'news' : 'filing',
      text: release?.text || null,
    });
  }
  return items;
}

type Market = 'NSE' | 'US';
const MARKETS: Record<Market, { timeZone: string; source: string }> = {
  NSE: { timeZone: 'Asia/Kolkata', source: 'nse_filings' },
  US: { timeZone: 'America/New_York', source: 'sec_edgar' },
};

async function sessionNews(symbol: string, date: string, market: Market): Promise<NewsItem[]> {
  const cacheKey = `${market}:${symbol}:${date}`;
  const cached = newsCache.get(cacheKey);
  if (cached && Date.now() - cached.at < NEWS_TTL_MS) return cached.items;

  // From the start of the previous trading day to the end of this one, in the
  // market's own clock.
  const { timeZone } = MARKETS[market];
  const from = zonedTime(previousWeekday(date), '00:00', timeZone);
  const to = zonedTime(addDays(date, 1), '00:00', timeZone);
  const items = market === 'US' ? await secFilings(symbol, from, to) : await nseFilings(symbol, date, from, to);
  items.sort((a, b) => a.published_at.localeCompare(b.published_at));
  newsCache.set(cacheKey, { at: Date.now(), items });
  return items;
}

/** Filings for several watched names on one session: `?date=YYYY-MM-DD&symbols=A,B[&market=US]`. */
export const news = requireAuth(async (event) => {
  const date = event.queryStringParameters?.date ?? '';
  const market: Market = event.queryStringParameters?.market === 'US' ? 'US' : 'NSE';
  const symbols = [...new Set((event.queryStringParameters?.symbols ?? '').split(',').filter(Boolean))];
  if (!DATE_RE.test(date) || !symbols.length || symbols.length > MAX_NEWS_SYMBOLS || !symbols.every((s) => SYMBOL_RE.test(s))) {
    return json(400, { error: `date (YYYY-MM-DD) and 1-${MAX_NEWS_SYMBOLS} symbols are required` });
  }
  const out: Record<string, NewsItem[] | { error: string }> = {};
  // A few at a time: SEC allows ~10 requests/s, and each 8-K costs two more.
  for (let i = 0; i < symbols.length; i += 3) {
    await Promise.all(
      symbols.slice(i, i + 3).map(async (symbol) => {
        try {
          out[symbol] = await sessionNews(symbol, date, market);
        } catch (err) {
          out[symbol] = { error: err instanceof Error ? err.message : String(err) };
        }
      }),
    );
  }
  return json(200, { date, market, source: MARKETS[market].source, news: out });
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
