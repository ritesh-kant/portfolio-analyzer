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
      .aggregate<{ _id: { date: string; symbol: string; side: string; reason: string }; count: number; first: Date; last: Date }>([
        { $match: rejections.match },
        {
          $group: {
            // Rows written before shorts existed have no side: they were longs.
            _id: { date: day, symbol: '$symbol', side: { $ifNull: ['$side', 'long'] }, reason: '$reason' },
            count: { $sum: 1 },
            first: { $min: '$time' },
            last: { $max: '$time' },
          },
        },
      ])
      .toArray(),
    db
      .collection(candidates.collection)
      .aggregate<{ _id: { date: string; symbol: string; side: string }; count: number }>([
        { $match: candidates.match },
        { $group: { _id: { date: day, symbol: '$symbol', side: { $ifNull: ['$side', 'long'] } }, count: { $sum: 1 } } },
      ])
      .toArray(),
  ]);
  const gates = new Map<string, GateHit[]>();
  for (const h of hits) {
    const key = `${h._id.date}:${h._id.symbol}:${h._id.side}`;
    const list = gates.get(key) ?? [];
    list.push({ reason: String(h._id.reason), count: h.count, first: h.first.toISOString(), last: h.last.toISOString() });
    gates.set(key, list);
  }
  // Keyed date:symbol:side, so a name's long and short refusals never mix.
  return { gates, armed: new Map(armed.map((a) => [`${a._id.date}:${a._id.symbol}:${a._id.side}`, a.count])) };
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
            _id: 0, strategy: 1, side: 1, symbol: 1, time: 1, day_chg_pct: 1, rvol: 1, reason: 1,
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
        { projection: { symbol: 1, side: 1, entry_time: 1, exit_time: 1, entry_price: 1, exit_price: 1, net_inr: 1, status: 1, strategy: 1 } },
      )
      .toArray(),
    gateLog(db, { collection: 'mt_rejections', match: window }, { collection: 'mt_candidates', match: window }, IST),
  ]);

  type Flag = {
    time: string;
    strategy?: string;
    side?: string;
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
    /** Which side's screen flagged it. A name on both gets two entries. */
    side: 'long' | 'short';
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
    const side = doc.side === 'short' ? ('short' as const) : ('long' as const);
    const nameKey = `${symbol}:${side}`;
    const name = names.get(nameKey) ?? {
      symbol,
      first_seen: time.toISOString(),
      last_seen: time.toISOString(),
      max_day_chg_pct: 0,
      max_rvol: -Infinity,
      strategies: [],
      side,
      flags: [],
      trades: [],
      gates: log.gates.get(`${date}:${symbol}:${side}`) ?? [],
      armed: log.armed.get(`${date}:${symbol}:${side}`) ?? 0,
    };
    name.last_seen = time.toISOString();
    // The biggest move either way, sign kept: a short-side name reads -6.2%.
    const chg = Number(doc.day_chg_pct);
    if (Number.isFinite(chg) && Math.abs(chg) > Math.abs(name.max_day_chg_pct)) name.max_day_chg_pct = chg;
    name.max_rvol = Math.max(name.max_rvol, Number(doc.rvol ?? -Infinity));
    if (doc.strategy && !name.strategies.includes(doc.strategy)) name.strategies.push(doc.strategy);
    name.flags.push({
      time: time.toISOString(),
      strategy: doc.strategy,
      side,
      reason: String(doc.reason ?? ''),
      day_chg_pct: Number(doc.day_chg_pct),
      rvol: Number(doc.rvol),
      candle_tags: doc.candle_tags ?? [],
      pattern_matches: doc.evidence?.pattern_matches ?? [],
    });
    names.set(nameKey, name);
  }
  for (const p of positions) {
    const names = sessions.get(istDate((p.entry_time as Date).getTime()));
    const name = names?.get(`${String(p.symbol)}:${p.side === 'short' ? 'short' : 'long'}`);
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
 * Context only, like `news_context.py` on the trade page: nothing reaches the
 * engine.
 *
 *   NSE: the symbol's own corporate announcements (summary + PDF).
 *   US:  SEC EDGAR filings — see `edgarNews` for why no aggregator is read.
 *
 * Media was dropped after an audit on 2026-09-24: 0 of 42 media items on 16 US
 * movers predated the move, against 15 of 17 EDGAR filings; on NSE, RSS and
 * Google items named the company, not the ticker, and mostly described a move
 * after it happened.
 *
 * The window opens at the start of the previous trading day: a catalyst filed
 * that morning can drive an after-hours move (GCTK: 8-K at 09:21, move 16:21).
 */

const NEWS_TTL_MS = 10 * 60 * 1000;
const MAX_NEWS_SYMBOLS = 40;
const NEWS_BUDGET_MS = 18_000;
const newsCache = new Map<string, { at: number; items: NewsItem[] }>();

/** news = the company announced something; offering = new shares (dilution); filing = other paperwork. */
export type NewsKind = 'news' | 'offering' | 'filing';

export interface NewsItem {
  headline: string;
  publisher: string;
  url: string | null;
  published_at: string;
  kind: NewsKind;
  /** Filing summary, when the source gives one. */
  text: string | null;
}

const BROWSER_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

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

/**
 * Ticker → CIK. SEC's file lists some warrants and units (GLNDW) but not all
 * (NWCLW), so a miss falls back to the issuer's own ticker: Nasdaq's fifth
 * letter W/U/R and NYSE's .WS/.U mark a warrant, unit or right on the stock.
 */
async function cikOf(symbol: string): Promise<number | null> {
  if (!secCiks || Date.now() - secCiks.at > DAY_MS) {
    const body = (await (await secGet(SEC_TICKERS_URL)).json()) as { fields: string[]; data: unknown[][] };
    const cik = body.fields.indexOf('cik');
    const ticker = body.fields.indexOf('ticker');
    secCiks = { at: Date.now(), byTicker: new Map(body.data.map((row) => [String(row[ticker]), Number(row[cik])])) };
  }
  const issuer = symbol.length >= 5 ? symbol.replace(/[.-]?(WS|W|U|R|RT)$/, '') : symbol;
  return secCiks.byTicker.get(symbol) ?? secCiks.byTicker.get(issuer) ?? null;
}

/** Ownership and correspondence forms: who holds the stock, not what the company did. */
const SKIP_FORMS = /^(3|4|5|144|SC 13[DG]|SCHEDULE 13[DG]|13F-HR|13F-NT|CORRESP|UPLOAD)(\/A)?$/;
/** Forms that sell new shares. A mover filing one is usually being sold into, not catalysed. */
const DILUTION_FORMS = /^(424B\d|S-1|S-3|F-1|F-3|FWP|EFFECT|D)(\/A)?$/;
/**
 * A press release announcing a share sale. "Offering" alone is not enough: a
 * company "expands its service offering" too. It has to be a kind of offering
 * ("public", "underwritten"…), a sized one ("$10 Million Offering", "offering
 * of 2,000,000 shares"), or one being priced or closed.
 */
const DILUTION_TITLE =
  /\b((public|underwritten|registered|secondary|follow-on|best[- ]efforts|direct|proposed|overnight|confidentially marketed|firm[- ]commitment|equity|stock|share|unit) offering|offering of (\$|US\$|up to|approximately|\d|common|ordinary|shares|units|American Depositary|pre-funded|warrants)|\$[\d.,]+\s*(million|billion|[mbk])?\b(\s+[\w-]+){0,3}\s+offering|(pricing|prices|priced|closing|closes|closed|upsized?)\b[^.]{0,60}\boffering|private placement|registered direct|at[- ]the[- ]market|warrant (inducement|exercise)|equity (line|purchase agreement))\b/i;

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
    mdash: '—', ndash: '–', trade: '™', reg: '®', copy: '©', hellip: '…', sup1: '¹', sup2: '²', sup3: '³',
    deg: '°', middot: '·', bull: '•', euro: '€', pound: '£', eacute: 'é', egrave: 'è', ouml: 'ö', uuml: 'ü',
  };
  return value
    .replace(/&#x([0-9a-f]+);/gi, (_m, hex: string) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_m, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&([a-z][a-z0-9]*);/gi, (m, name: string) => named[name.toLowerCase()] ?? m);
}

/** A press release's dateline: a wire name, "RUTHERFORD, N.J.," or "Sept. 23, 2026". */
const DATELINE =
  /(PRNewswire|GLOBE NEWSWIRE|GlobeNewswire|ACCESSWIRE|ACCESS Newswire|Business Wire|BUSINESS WIRE|Newsfile|^[A-Z][A-Z .'’-]{2,},|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, 20\d\d\b)/;
/** Exhibit furniture ("EX-99.1", "PRESS RELEASE, DATED …"). Above the headline it is skipped; below, it ends it. */
const PREAMBLE = /^(ex(hibit)?[\s.\-_]*99([.\-_]?\d+)?|exhibit|99[.\-]\d+|\d+|[\w.\-]+\.html?|for immediate release)$|^(press|news) release\b/i;

/** The headline of an Exhibit 99 press release: the lines above its dateline. */
export function pressReleaseTitle(html: string): string {
  const text = decodeHtml(
    html
      .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
      .replace(/<br\b[^>]*>/gi, ' ')
      .replace(/<\/?(p|div|tr|td|h[1-6]|li|table)\b[^>]*>/gi, '\n')
      .replace(/<[^>]+>/g, ''),
  );
  let joined = '';
  let lines = 0;
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\s+/g, ' ').trim();
    if (!line) continue;
    if (PREAMBLE.test(line)) {
      if (lines) break;
      continue;
    }
    if (DATELINE.test(line)) break;
    // A headline split across paragraphs continues with a short fragment
    // ("Lōkahi" / "Therapeutics™ and …") or a lowercase word ("… Extension" /
    // "of Jameson Land …"); anything else is its sub-headline.
    const words = (t: string) => t.split(' ').length;
    const subhead = words(joined) >= 5 && words(line) >= 5 && !/^[a-z]/.test(line);
    joined = !lines ? line : `${joined}${subhead ? ' — ' : ' '}${line}`;
    if (++lines === 3) break;
  }
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
async function edgarNews(symbol: string, date: string, deadline: number): Promise<{ items: NewsItem[]; complete: boolean }> {
  const cik = await cikOf(symbol);
  if (cik == null) throw new Error(`${symbol} is not in SEC's ticker list`);
  const body = (await (await secGet(`https://data.sec.gov/submissions/CIK${String(cik).padStart(10, '0')}.json`)).json()) as {
    filings: { recent: SecRecent };
  };
  const recent = body.filings.recent;
  // From the start of the previous trading day to the end of this one, New York time.
  const from = zonedTime(previousWeekday(date), '00:00', 'America/New_York');
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
      kind: dilution ? 'offering' : headline ? 'news' : 'filing',
      text: null,
    });
  }
  items.sort((a, b) => a.published_at.localeCompare(b.published_at));
  return { items, complete };
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

type Market = 'NSE' | 'US';
const NEWS_SOURCE: Record<Market, string> = { NSE: 'nse_filings', US: 'sec_edgar' };

async function sessionNews(symbol: string, date: string, market: Market, deadline: number): Promise<NewsItem[]> {
  const cacheKey = `${market}:${symbol}:${date}`;
  const cached = newsCache.get(cacheKey);
  if (cached && Date.now() - cached.at < NEWS_TTL_MS) return cached.items;
  const nse = async () => {
    const from = zonedTime(previousWeekday(date), '00:00', IST);
    const to = zonedTime(addDays(date, 1), '00:00', IST);
    const items = await nseFilings(symbol, date, from, to);
    items.sort((a, b) => a.published_at.localeCompare(b.published_at));
    return { items, complete: true };
  };
  const { items, complete } = market === 'US' ? await edgarNews(symbol, date, deadline) : await nse();
  if (complete) newsCache.set(cacheKey, { at: Date.now(), items });
  return items;
}

/** Company filings for several watched names on one session: `?date=YYYY-MM-DD&symbols=A,B[&market=US]`. */
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
  // A handful at a time; SEC calls are spaced by `secGet` anyway.
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
    .find({ market: 'US' }, { projection: { date: 1, names: 1, short: 1, considered: 1, missing_criteria: 1 } })
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
        { projection: { _id: 0, symbol: 1, side: 1, time: 1, reason: 1, day_chg_pct: 1, rvol: 1, candle_tags: 1, 'evidence.pattern_matches': 1, strategy: 1 } },
      )
      .sort({ time: 1 })
      .toArray(),
    db
      .collection('mt_us_positions')
      .find(
        { entry_time: { $gte: from, $lt: to } },
        { projection: { symbol: 1, side: 1, entry_time: 1, exit_time: 1, entry_price: 1, exit_price: 1, net_usd: 1, status: 1, strategy: 1 } },
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
      first_seen?: string; first_passed_at?: string | null; side?: 'long' | 'short';
    };
    // Long screen survivors, then the short (losers) screen's, each tagged.
    const watched = [
      ...((doc.names ?? []) as USName[]).filter((n) => n.passed).map((n) => ({ ...n, side: 'long' as const })),
      ...((doc.short?.names ?? []) as USName[]).filter((n) => n.passed).map((n) => ({ ...n, side: 'short' as const })),
    ];
    const names = watched.map((n) => {
      const firstPassed = n.first_passed_at ?? n.first_seen ?? n.observed_at ?? `${date}T09:30:00-04:00`;
      const promotions = attention.filter(
        (a) =>
          a.symbol === n.symbol &&
          etDate(a.time as Date) === date &&
          (a.side === 'short' ? 'short' : 'long') === n.side,
      );
      const flags = [
        {
          time: new Date(firstPassed).toISOString(),
          side: n.side,
          reason: n.side === 'short' ? 'passed_short_screen' : 'passed_screen',
          day_chg_pct: Number(n.day_chg_pct),
          rvol: n.rvol == null ? Number.NaN : Number(n.rvol),
          candle_tags: [] as string[],
          pattern_matches: [] as unknown[],
        },
        ...promotions.map((a) => ({
          time: (a.time as Date).toISOString(),
          strategy: a.strategy,
          side: n.side,
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
        // Biggest move either way, sign kept (a short-side name reads negative).
        max_day_chg_pct: [...numbers('day_chg_pct'), Number(n.day_chg_pct)]
          .filter(Number.isFinite)
          .reduce((best, v) => (Math.abs(v) > Math.abs(best) ? v : best), 0),
        max_rvol: Math.max(...numbers('rvol'), 0),
        strategies: [...new Set(promotions.map((a) => a.strategy).filter(Boolean))],
        side: n.side,
        flags,
        trades: positions
          .filter(
            (p) =>
              p.symbol === n.symbol &&
              etDate(p.entry_time as Date) === date &&
              (p.side === 'short' ? 'short' : 'long') === n.side,
          )
          .map((p) => ({ ...p, _id: String(p._id) })),
        gates: log.gates.get(`${date}:${n.symbol}:${n.side}`) ?? [],
        armed: log.armed.get(`${date}:${n.symbol}:${n.side}`) ?? 0,
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
