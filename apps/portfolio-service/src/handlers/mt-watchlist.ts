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

/** One engine gate that refused a watched name, folded over the session. */
export interface GateHit {
  reason: string;
  count: number;
  first: string;
  last: string;
}

/**
 * Every refusal the engine logged for each watched name, grouped by reason,
 * plus how many buy-stops it armed. Keyed `${date}:${symbol}` in the market's
 * own clock. Most reasons fire once per minute while the name waits (a red
 * candle is a refusal), so `count` is minutes spent blocked, not attempts.
 */
async function gateLog(
  db: NonNullable<typeof mongoose.connection.db>,
  rejections: { collection: string; match: Record<string, unknown> },
  candidates: { collection: string; match: Record<string, unknown> },
  timezone: string,
) {
  const day = { $dateToString: { format: '%Y-%m-%d', date: '$time', timezone } };
  const [hits, armed] = await Promise.all([
    db
      .collection(rejections.collection)
      .aggregate<{ _id: { date: string; symbol: string; reason: string }; count: number; first: Date; last: Date }>([
        { $match: rejections.match },
        {
          $group: {
            _id: { date: day, symbol: '$symbol', reason: '$reason' },
            count: { $sum: 1 },
            first: { $min: '$time' },
            last: { $max: '$time' },
          },
        },
      ])
      .toArray(),
    db
      .collection(candidates.collection)
      .aggregate<{ _id: { date: string; symbol: string }; count: number }>([
        { $match: candidates.match },
        { $group: { _id: { date: day, symbol: '$symbol' }, count: { $sum: 1 } } },
      ])
      .toArray(),
  ]);
  const gates = new Map<string, GateHit[]>();
  for (const h of hits) {
    const key = `${h._id.date}:${h._id.symbol}`;
    const list = gates.get(key) ?? [];
    list.push({ reason: String(h._id.reason), count: h.count, first: h.first.toISOString(), last: h.last.toISOString() });
    gates.set(key, list);
  }
  return { gates, armed: new Map(armed.map((a) => [`${a._id.date}:${a._id.symbol}`, a.count])) };
}

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
  const window = { time: { $gte: from, $lt: to } };
  const [events, positions, log] = await Promise.all([
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
    gateLog(db, { collection: 'mt_rejections', match: window }, { collection: 'mt_candidates', match: window }, IST),
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
    gates: GateHit[];
    armed: number;
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
      gates: log.gates.get(`${date}:${symbol}`) ?? [],
      armed: log.armed.get(`${date}:${symbol}`) ?? 0,
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
 * News for a watched name around one session. Context only, like
 * `news_context.py` on the trade page: nothing here reaches the engine.
 *
 * US: SEC EDGAR filings only — see `edgarNews` for why no aggregator is read.
 *
 * NSE: Google News RSS, because it is the one source that answers for an
 * arbitrary past date without a key — the stored feeds cannot: the
 * news-trader pipeline (`nt_signals`) has been paused since 2026-06-26, and
 * BSE's announcement API refuses per-scrip queries off a browser (Akamai 403).
 * A search on the company name also returns peers and sector round-ups, so an
 * item is kept only when its headline names the company or the symbol.
 */

const NEWS_TTL_MS = 10 * 60 * 1000;
const MAX_NEWS_SYMBOLS = 40;
const NEWS_BUDGET_MS = 18_000;
const newsCache = new Map<string, { at: number; items: NewsItem[] }>();

export interface NewsItem {
  headline: string;
  publisher: string;
  url: string | null;
  published_at: string;
  /** US only: an Exhibit 99 press release, a share offering, or any other filing. */
  kind?: 'press_release' | 'dilution' | 'filing';
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

// ── US: SEC EDGAR filings ────────────────────────────────────────────────────
/*
 * The US arm reads EDGAR and nothing else. An audit of 16 big movers on
 * 2026-09-24 found EDGAR carried 15 of its 17 items BEFORE the move, and an
 * 8-K/6-K's Exhibit 99 is the wire press release itself (its dateline says
 * /PRNewswire/ or GLOBE NEWSWIRE), with SEC's own acceptance timestamp. Every
 * aggregator checked (Google News, Yahoo, Benzinga, StocksToTrade, RTTNews,
 * MarketBeat…) had 0 of 42 items before the move: recaps written because the
 * price jumped, which make every mover look catalysed. The wires themselves
 * refuse scrapers, and PR Newswire also carries paid stock promotion.
 *
 * SEC refuses requests whose User-Agent has no contact email, and allows 10
 * requests a second; `secGet` spaces calls under that.
 */
const SEC_TICKERS_URL = 'https://www.sec.gov/files/company_tickers_exchange.json';
const SEC_GAP_MS = 120;
let secNextAt = 0;
let secCiks: { at: number; byTicker: Map<string, number> } | null = null;

async function secGet(url: string): Promise<Response> {
  if (!config.secUserAgent) throw new Error('SEC_USER_AGENT is not set (SEC refuses requests without a contact email)');
  const wait = secNextAt - Date.now();
  secNextAt = Math.max(Date.now(), secNextAt) + SEC_GAP_MS;
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  const res = await fetch(url, { headers: { 'User-Agent': config.secUserAgent } });
  if (!res.ok) throw new Error(`sec ${new URL(url).pathname} HTTP ${res.status}`);
  return res;
}

/** Ticker → CIK. SEC's file lists warrants and units too (GLNDW → Greenland Energy). */
async function cikOf(symbol: string): Promise<number | null> {
  if (!secCiks || Date.now() - secCiks.at > DAY_MS) {
    const body = (await (await secGet(SEC_TICKERS_URL)).json()) as { fields: string[]; data: unknown[][] };
    const cik = body.fields.indexOf('cik');
    const ticker = body.fields.indexOf('ticker');
    secCiks = { at: Date.now(), byTicker: new Map(body.data.map((row) => [String(row[ticker]), Number(row[cik])])) };
  }
  return secCiks.byTicker.get(symbol) ?? null;
}

/** Ownership and correspondence forms: who holds the stock, not what the company did. */
const SKIP_FORMS = /^(3|4|5|144|SC 13[DG]|SCHEDULE 13[DG]|13F-HR|13F-NT|CORRESP|UPLOAD)(\/A)?$/;
/** Forms that sell new shares. A mover filing one is usually being sold into, not catalysed. */
const DILUTION_FORMS = /^(424B\d|S-1|S-3|F-1|F-3|FWP|EFFECT|D)(\/A)?$/;
const DILUTION_TITLE = /\b(offering|private placement|registered direct|at-the-market|warrant (inducement|exercise))\b/i;

const FORM_NAMES: Record<string, string> = {
  '424B1': 'Prospectus (share offering)',
  '424B3': 'Prospectus (share offering)',
  '424B4': 'Prospectus (share offering priced)',
  '424B5': 'Prospectus supplement (share offering)',
  '424B7': 'Prospectus (resale of shares)',
  'S-1': 'Registration of new shares',
  'S-3': 'Shelf registration of new shares',
  'F-1': 'Registration of new shares',
  'F-3': 'Shelf registration of new shares',
  FWP: 'Offering term sheet',
  EFFECT: 'Share registration declared effective',
  D: 'Exempt share sale notice',
  '10-Q': 'Quarterly report',
  '10-K': 'Annual report',
  '20-F': 'Annual report (foreign issuer)',
  '6-K': 'Report of a foreign issuer',
  '425': 'Merger communication',
  'DEF 14A': 'Proxy statement',
  'PRE 14A': 'Preliminary proxy statement',
};

const ITEM_NAMES: Record<string, string> = {
  '1.01': 'Material agreement',
  '1.02': 'Agreement terminated',
  '2.01': 'Acquisition or sale completed',
  '2.02': 'Results of operations',
  '2.03': 'New debt',
  '3.01': 'Listing-rule notice',
  '3.02': 'Unregistered share sale',
  '3.03': 'Change to shareholder rights',
  '4.01': 'Auditor change',
  '5.02': 'Director or officer change',
  '5.03': 'Charter or bylaw change',
  '5.07': 'Shareholder vote',
  '7.01': 'Reg FD disclosure',
  '8.01': 'Other events',
};

function decodeHtml(value: string): string {
  const named: Record<string, string> = {
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“',
    mdash: '—', ndash: '–', trade: '™', reg: '®', copy: '©', hellip: '…',
  };
  return value
    .replace(/&#x([0-9a-f]+);/gi, (_m, hex: string) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_m, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&([a-z]+);/gi, (m, name: string) => named[name.toLowerCase()] ?? m);
}

/** A press release's dateline: a wire name, "RUTHERFORD, N.J.," or "Sept. 23, 2026". */
const DATELINE =
  /(PRNewswire|GLOBE NEWSWIRE|GlobeNewswire|ACCESSWIRE|ACCESS Newswire|Business Wire|BUSINESS WIRE|Newsfile|^[A-Z][A-Z .'’-]{2,},|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, 20\d\d\b)/;
/** Exhibit furniture that sits above the headline. */
const PREAMBLE = /^(ex(hibit)?[\s.\-_]*99([.\-_]?\d+)?|exhibit|99[.\-]\d+|\d+|[\w.\-]+\.html?|(press|news) release|for immediate release)$/i;

/** The headline of an Exhibit 99 press release: the lines above its dateline. */
export function pressReleaseTitle(html: string): string {
  const text = decodeHtml(
    html
      .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
      .replace(/<\/?(p|div|br|tr|td|h[1-6]|li|table)\b[^>]*>/gi, '\n')
      .replace(/<[^>]+>/g, ''),
  );
  const title: string[] = [];
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\s+/g, ' ').trim();
    if (!line || (!title.length && PREAMBLE.test(line))) continue;
    if (DATELINE.test(line)) break;
    title.push(line);
    if (title.length === 3) break;
  }
  const joined = title.join(' ');
  return joined.length > 240 ? `${joined.slice(0, 239)}…` : joined;
}

/** The filing's Exhibit 99 (99.1 first), if it has one. */
async function exhibit99(cik: number, accession: string): Promise<string | null> {
  const base = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}`;
  const index = (await (await secGet(`${base}/index.json`)).json()) as { directory?: { item?: { name: string }[] } };
  const names = (index.directory?.item ?? []).map((i) => i.name).filter((n) => /ex[-_]?99.*\.(htm|html|txt)$/i.test(n));
  const first = names.find((n) => /99[-_.]?0?1\b|991/i.test(n)) ?? names[0];
  return first ? `${base}/${first}` : null;
}

interface SecRecent {
  accessionNumber: string[];
  acceptanceDateTime: string[];
  form: string[];
  items: string[];
}

/**
 * Every filing from the previous close to the end of `date`. Exhibit fetches
 * stop at `deadline` (the page asks for a whole session's names in one call);
 * `complete` is false when any headline fell back to the filing type.
 */
export async function edgarNews(symbol: string, date: string, deadline: number): Promise<{ items: NewsItem[]; complete: boolean }> {
  const cik = await cikOf(symbol);
  if (cik == null) throw new Error(`${symbol} is not in SEC's ticker list`);
  const body = (await (await secGet(`https://data.sec.gov/submissions/CIK${String(cik).padStart(10, '0')}.json`)).json()) as {
    filings: { recent: SecRecent };
  };
  const recent = body.filings.recent;
  // From the previous session's close to the end of this session's day, New York time.
  const from = zonedTime(previousWeekday(date), '16:00', 'America/New_York');
  const to = zonedTime(addDays(date, 1), '00:00', 'America/New_York');

  const items: NewsItem[] = [];
  let complete = true;
  for (let i = 0; i < recent.form.length; i++) {
    const form = recent.form[i]!;
    // acceptanceDateTime is true UTC: an 8-K accepted at 21:20 ET reads 01:20Z the next day.
    const accepted = Date.parse(recent.acceptanceDateTime[i]!);
    if (!Number.isFinite(accepted) || accepted < from || accepted >= to || SKIP_FORMS.test(form)) continue;
    const accession = recent.accessionNumber[i]!;
    const codes = (recent.items[i] ?? '').split(',').map((c) => c.trim()).filter((c) => c && c !== '9.01');
    const baseForm = form.replace(/\/A$/, '');
    let url = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}/${accession}-index.htm`;
    let headline = '';
    if ((baseForm === '8-K' || baseForm === '6-K') && Date.now() > deadline) {
      complete = false;
    } else if (baseForm === '8-K' || baseForm === '6-K') {
      const ex = await exhibit99(cik, accession).catch(() => null);
      if (ex) {
        url = ex;
        headline = pressReleaseTitle(await (await secGet(ex)).text().catch(() => ''));
      }
    }
    const described = codes.length
      ? codes.map((c) => `${c} ${ITEM_NAMES[c] ?? ''}`.trim()).join(' · ')
      : (FORM_NAMES[baseForm] ?? form);
    const dilution = DILUTION_FORMS.test(form) || codes.includes('3.02') || DILUTION_TITLE.test(headline);
    items.push({
      headline: headline || described,
      publisher: `SEC EDGAR · ${form}${headline && codes.length ? ` · ${described}` : ''}`,
      url,
      published_at: new Date(accepted).toISOString(),
      kind: dilution ? 'dilution' : headline ? 'press_release' : 'filing',
    });
  }
  items.sort((a, b) => a.published_at.localeCompare(b.published_at));
  return { items, complete };
}

// ── NSE: Google News ─────────────────────────────────────────────────────────

async function googleNews(symbol: string, date: string): Promise<NewsItem[]> {
  const prev = previousWeekday(date);
  const inst = await instrument(symbol).catch(() => null);
  const name = inst ? companyName(inst.name) : '';
  const query = `${name ? `"${symbol}" OR "${name}"` : `"${symbol}"`} after:${addDays(prev, -1)} before:${addDays(date, 1)}`;
  const url = `https://news.google.com/rss/search?${new URLSearchParams({ q: query, hl: 'en-IN', gl: 'IN', ceid: 'IN:en' })}`;
  const res = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (!res.ok) throw new Error(`google news HTTP ${res.status}`);
  const xml = await res.text();

  // From the previous session's close to the end of this session's day, IST.
  const from = zonedTime(prev, '15:30', IST);
  const to = zonedTime(addDays(date, 1), '00:00', IST);
  // The name's first two words ("Concord Biotech"): enough to reject peers,
  // loose enough to survive the exchange's abbreviations.
  const nameKey = name.split(' ').slice(0, 2).join(' ').toLowerCase();
  const symbolRe = new RegExp(`\\b${symbol.replace(/[.*+?^${}()|[\]\\&]/g, '\\$&')}\\b`, 'i');
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
  return items;
}

type Market = 'NSE' | 'US';
const NEWS_SOURCE: Record<Market, string> = { NSE: 'google_news', US: 'sec_edgar' };

async function sessionNews(symbol: string, date: string, market: Market, deadline: number): Promise<NewsItem[]> {
  const cacheKey = `${market}:${symbol}:${date}`;
  const cached = newsCache.get(cacheKey);
  if (cached && Date.now() - cached.at < NEWS_TTL_MS) return cached.items;
  const { items, complete } =
    market === 'US' ? await edgarNews(symbol, date, deadline) : { items: await googleNews(symbol, date), complete: true };
  if (complete) newsCache.set(cacheKey, { at: Date.now(), items });
  return items;
}

/** News for several watched names on one session: `?date=YYYY-MM-DD&symbols=A,B[&market=US]`. */
export const news = requireAuth(async (event) => {
  const date = event.queryStringParameters?.date ?? '';
  const market: Market = event.queryStringParameters?.market === 'US' ? 'US' : 'NSE';
  const symbols = [...new Set((event.queryStringParameters?.symbols ?? '').split(',').filter(Boolean))];
  if (!DATE_RE.test(date) || !symbols.length || symbols.length > MAX_NEWS_SYMBOLS || !symbols.every((s) => SYMBOL_RE.test(s))) {
    return json(400, { error: `date (YYYY-MM-DD) and 1-${MAX_NEWS_SYMBOLS} symbols are required` });
  }
  // The function times out at 25 s; past this, US filings keep their form name instead of a headline.
  const deadline = Date.now() + NEWS_BUDGET_MS;
  const out: Record<string, NewsItem[] | { error: string }> = {};
  // A handful at a time: Google throttles a burst of 30 parallel searches, and
  // SEC calls are spaced by `secGet` anyway.
  for (let i = 0; i < symbols.length; i += 6) {
    await Promise.all(
      symbols.slice(i, i + 6).map(async (symbol) => {
        try {
          out[symbol] = await sessionNews(symbol, date, market, deadline);
        } catch (err) {
          out[symbol] = { error: err instanceof Error ? err.message : String(err) };
        }
      }),
    );
  }
  return json(200, { date, market, source: NEWS_SOURCE[market], news: out });
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
  const window = { time: { $gte: from, $lt: to } };
  const [attention, positions, log] = await Promise.all([
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
    gateLog(
      db,
      { collection: 'mt_us_candidates', match: { ...window, kind: 'rejection' } },
      { collection: 'mt_us_candidates', match: { ...window, kind: 'candidate' } },
      'America/New_York',
    ),
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
        gates: log.gates.get(`${date}:${n.symbol}`) ?? [],
        armed: log.armed.get(`${date}:${n.symbol}`) ?? 0,
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
