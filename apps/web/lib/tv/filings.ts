import type { NewsItem, NewsKind } from './news-types';

/*
 * What a company itself disclosed — never media. Same sources and rules as the
 * momentum watchlist (apps/portfolio-service mt-watchlist.ts): NSE corporate
 * announcements for India, SEC EDGAR for the US. An audit on 2026-09-24 found
 * media items were written after the move; filings carry the exchange's own
 * timestamp and mostly precede it.
 */

const BROWSER_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const DAY_MS = 86_400_000;
const cache = new Map<string, { at: number; items: NewsItem[] }>();
const TTL_MS = 10 * 60 * 1000;

const cached = async (key: string, load: () => Promise<NewsItem[]>) => {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.items;
  const items = await load();
  cache.set(key, { at: Date.now(), items });
  return items;
};

// ── NSE ──────────────────────────────────────────────────────────────────

interface NseRow {
  desc?: string;
  sort_date?: string;
  an_dt?: string;
  attchmntText?: string;
  attchmntFile?: string;
}

function nseTime(row: NseRow): number {
  if (row.sort_date && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(row.sort_date)) {
    return Date.parse(`${row.sort_date.replace(' ', 'T')}+05:30`);
  }
  const m = row.an_dt?.match(/^(\d{2})-([A-Za-z]{3})-(\d{4}) (\d{2}:\d{2}:\d{2})$/);
  return m ? Date.parse(`${m[2]} ${m[1]} ${m[3]} ${m[4]} GMT+0530`) : NaN;
}

const NSE_ROUTINE =
  /analysts?\/institutional investor meet|shareholders? meeting|trading window|newspaper publication|compliance certificate|loss of share certificate|duplicate share certificate|record date|book closure|investor presentation|change in (registrar|rta)/i;
const NSE_OFFERING =
  /allotment|preferential issue|qualified institutions? placement|\bqip\b|rights issue|fund raising|issue of (equity|securities)|warrants/i;

const ddmmyyyy = (date: string) => date.split('-').reverse().join('-');

export function nseFilings(symbol: string, from: string, to: string): Promise<NewsItem[]> {
  return cached(`NSE:${symbol}:${from}:${to}`, async () => {
    const params = new URLSearchParams({ index: 'equities', symbol, from_date: ddmmyyyy(from), to_date: ddmmyyyy(to) });
    const res = await fetch(`https://www.nseindia.com/api/corporate-announcements?${params}`, {
      headers: {
        'User-Agent': BROWSER_UA,
        Accept: 'application/json, text/plain, */*',
        Referer: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements',
      },
      cache: 'no-store',
    });
    if (!res.ok) throw new Error(`NSE announcements HTTP ${res.status}`);
    const rows = (await res.json()) as NseRow[];
    const items: NewsItem[] = [];
    for (const row of Array.isArray(rows) ? rows : []) {
      const at = nseTime(row);
      if (!row.desc || !Number.isFinite(at)) continue;
      const text = row.attchmntText?.trim() || null;
      const kind: NewsKind = NSE_OFFERING.test(`${row.desc} ${text ?? ''}`)
        ? 'offering'
        : NSE_ROUTINE.test(row.desc)
          ? 'filing'
          : 'news';
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
    return items.sort((a, b) => a.published_at.localeCompare(b.published_at));
  });
}

// ── SEC EDGAR ────────────────────────────────────────────────────────────

let secNextAt = 0;
let secCiks: { at: number; byTicker: Map<string, number> } | null = null;

async function secGet(url: string, userAgent: string): Promise<Response> {
  const wait = secNextAt - Date.now();
  secNextAt = Math.max(Date.now(), secNextAt) + 120; // SEC allows 10 req/s
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  const res = await fetch(url, { headers: { 'User-Agent': userAgent }, cache: 'no-store' });
  if (!res.ok) throw new Error(`SEC ${new URL(url).pathname} HTTP ${res.status}`);
  return res;
}

async function cikOf(symbol: string, ua: string): Promise<number | null> {
  if (!secCiks || Date.now() - secCiks.at > DAY_MS) {
    const body = (await (await secGet('https://www.sec.gov/files/company_tickers_exchange.json', ua)).json()) as {
      fields: string[];
      data: unknown[][];
    };
    const cik = body.fields.indexOf('cik');
    const ticker = body.fields.indexOf('ticker');
    secCiks = { at: Date.now(), byTicker: new Map(body.data.map((row) => [String(row[ticker]), Number(row[cik])])) };
  }
  const issuer = symbol.length >= 5 ? symbol.replace(/[.-]?(WS|W|U|R|RT)$/, '') : symbol;
  return secCiks.byTicker.get(symbol) ?? secCiks.byTicker.get(issuer) ?? null;
}

const SKIP_FORMS = /^(3|4|5|144|SC 13[DG]|SCHEDULE 13[DG]|13F-HR|13F-NT|CORRESP|UPLOAD)(\/A)?$/;
const DILUTION_FORMS = /^(424B\d|S-1|S-3|F-1|F-3|FWP|EFFECT|D)(\/A)?$/;
const DILUTION_TITLE =
  /\b((public|underwritten|registered|secondary|follow-on|best[- ]efforts|direct|proposed|overnight|firm[- ]commitment|equity|stock|share|unit) offering|offering of (\$|US\$|up to|approximately|\d|common|ordinary|shares|units|warrants)|private placement|registered direct|at[- ]the[- ]market|warrant (inducement|exercise))\b/i;

const FORM_NAMES: Record<string, string> = {
  '424B5': 'Prospectus supplement (share offering)',
  'S-1': 'Registration of new shares',
  'S-3': 'Shelf registration of new shares',
  '10-Q': 'Quarterly report',
  '10-K': 'Annual report',
  '20-F': 'Annual report (foreign issuer)',
  '6-K': 'Report of a foreign issuer',
  '425': 'Merger communication',
  'DEF 14A': 'Proxy statement',
};
const ITEM_NAMES: Record<string, string> = {
  '1.01': 'Material agreement',
  '1.02': 'Agreement terminated',
  '2.01': 'Acquisition or sale completed',
  '2.02': 'Results of operations',
  '2.03': 'New debt',
  '3.01': 'Listing-rule notice',
  '3.02': 'Unregistered share sale',
  '4.01': 'Auditor change',
  '5.02': 'Director or officer change',
  '5.07': 'Shareholder vote',
  '7.01': 'Reg FD disclosure',
  '8.01': 'Other events',
};

const decodeHtml = (value: string) =>
  value
    .replace(/&#x([0-9a-f]+);/gi, (_m, hex: string) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_m, code: string) => String.fromCodePoint(Number(code)))
    .replace(/&(amp|lt|gt|quot|apos|nbsp|rsquo|lsquo|rdquo|ldquo|mdash|ndash);/gi, (_m, n: string) =>
      ({ amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', mdash: '—', ndash: '–' })[n.toLowerCase()] ?? _m,
    );

const DATELINE =
  /(PRNewswire|GLOBE NEWSWIRE|GlobeNewswire|ACCESSWIRE|Business Wire|BUSINESS WIRE|Newsfile|^[A-Z][A-Z .'’-]{2,},|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, 20\d\d\b)/;
const PREAMBLE =
  /^(ex(hibit)?[\s.\-_]*99([.\-_]?\d+)?|exhibit|99[.-]\d+|\d+|[\w.-]+\.html?|for immediate release)$|^(press|news) release\b/i;

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
    joined = lines ? `${joined} ${line}` : line;
    if (++lines === 3) break;
  }
  return joined.length > 240 ? `${joined.slice(0, 239)}…` : joined;
}

async function exhibit99(cik: number, accession: string, ua: string): Promise<string | null> {
  const base = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}`;
  const index = (await (await secGet(`${base}/index.json`, ua)).json()) as { directory?: { item?: { name: string }[] } };
  const names = (index.directory?.item ?? []).map((i) => i.name).filter((n) => /ex[-_]?99.*\.(htm|html|txt)$/i.test(n));
  const first = names.find((n) => /99[-_.]?0?1\b|991/i.test(n)) ?? names[0];
  return first ? `${base}/${first}` : null;
}

/** Press-release headlines are fetched for only this many of the newest 8-K/6-Ks (2 requests each). */
const MAX_EXHIBITS = 10;

export function edgarFilings(symbol: string, from: string, to: string, ua: string): Promise<NewsItem[]> {
  return cached(`US:${symbol}:${from}:${to}`, async () => {
    const cik = await cikOf(symbol, ua);
    if (cik == null) throw new Error(`${symbol} is not in SEC's ticker list`);
    const body = (await (await secGet(`https://data.sec.gov/submissions/CIK${String(cik).padStart(10, '0')}.json`, ua)).json()) as {
      filings: { recent: { accessionNumber: string[]; acceptanceDateTime: string[]; form: string[]; items: string[] } };
    };
    const recent = body.filings.recent;
    const lo = Date.parse(`${from}T00:00:00Z`);
    const hi = Date.parse(`${to}T00:00:00Z`) + DAY_MS;
    const picked: { i: number; accepted: number }[] = [];
    for (let i = 0; i < recent.form.length; i += 1) {
      const accepted = Date.parse(recent.acceptanceDateTime[i]!);
      if (!Number.isFinite(accepted) || accepted < lo || accepted >= hi || SKIP_FORMS.test(recent.form[i]!)) continue;
      picked.push({ i, accepted });
    }
    const items: NewsItem[] = [];
    let exhibits = 0;
    for (const { i, accepted } of picked) {
      const form = recent.form[i]!;
      const accession = recent.accessionNumber[i]!;
      const codes = (recent.items[i] ?? '').split(',').map((c) => c.trim()).filter((c) => c && c !== '9.01');
      const baseForm = form.replace(/\/A$/, '');
      let url = `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}/${accession}-index.htm`;
      let headline = '';
      if ((baseForm === '8-K' || baseForm === '6-K') && exhibits < MAX_EXHIBITS) {
        exhibits += 1;
        const ex = await exhibit99(cik, accession, ua).catch(() => null);
        if (ex) {
          url = ex;
          headline = pressReleaseTitle(await (await secGet(ex, ua)).text().catch(() => ''));
        }
      }
      const described = codes.length
        ? codes.map((c) => `${c} ${ITEM_NAMES[c] ?? ''}`.trim()).join(' · ')
        : (FORM_NAMES[baseForm] ?? form);
      const dilution = DILUTION_FORMS.test(form) || codes.includes('3.02') || DILUTION_TITLE.test(headline);
      items.push({
        headline: headline || described,
        publisher: `SEC EDGAR · ${form}`,
        url,
        published_at: new Date(accepted).toISOString(),
        kind: dilution ? 'offering' : headline ? 'news' : 'filing',
        text: null,
      });
    }
    return items.sort((a, b) => a.published_at.localeCompare(b.published_at));
  });
}
