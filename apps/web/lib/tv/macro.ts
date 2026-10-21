import type { NewsItem } from './news-types';

/*
 * Primary-source "news" for precious metals, which have no company filings:
 *   • Federal Reserve monetary-policy releases (FOMC statements, minutes) — the Fed's own RSS.
 *   • Scheduled US data releases that move gold (jobs report, CPI, PPI) — the BLS release calendar.
 *   • CFTC Commitments of Traders — weekly managed-money positioning, from the CFTC's public API.
 * Every item carries its publisher's own timestamp. No media is read: an audit on 2026-09-24
 * found media items mostly describe a move after it happened.
 */

/** Futures roots these sources apply to, and the CFTC market name for each. */
export const METALS_COT: Record<string, string> = {
  GC: 'GOLD - COMMODITY EXCHANGE INC.',
  MGC: 'GOLD - COMMODITY EXCHANGE INC.',
  SI: 'SILVER - COMMODITY EXCHANGE INC.',
  PL: 'PLATINUM - NEW YORK MERCANTILE EXCHANGE',
  PA: 'PALLADIUM - NEW YORK MERCANTILE EXCHANGE',
};

export const metalRoot = (symbol: string): string | null => {
  const m = symbol.toUpperCase().match(/^([A-Z]{1,3})=F$/);
  return m && METALS_COT[m[1]!] ? m[1]! : null;
};

/** The instant `YYYY-MM-DD` `HH:MM` is reached in `timeZone` (DST-aware), as epoch ms. */
export function zonedInstant(date: string, hhmm: string, timeZone: string): number {
  const guess = Date.parse(`${date}T${hhmm}:00Z`);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(guess));
  const n = (type: string) => Number(parts.find((p) => p.type === type)!.value);
  const asLocal = Date.UTC(
    n('year'),
    n('month') - 1,
    n('day'),
    n('hour'),
    n('minute'),
    n('second'),
  );
  return guess - (asLocal - guess);
}

const decode = (s: string) =>
  s
    .replace(/<!\[CDATA\[|\]\]>/g, '')
    .replace(/&#(\d+);/g, (_m, c: string) => String.fromCodePoint(Number(c)))
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .trim();

/** Fed press-release RSS → items. */
export function parseFedRss(xml: string): NewsItem[] {
  const out: NewsItem[] = [];
  for (const item of xml.match(/<item>[\s\S]*?<\/item>/g) ?? []) {
    const tag = (name: string) =>
      decode(item.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`))?.[1] ?? '');
    const when = Date.parse(tag('pubDate'));
    const title = tag('title');
    if (!title || !Number.isFinite(when)) continue;
    out.push({
      headline: title,
      publisher: 'Federal Reserve',
      url: tag('link') || null,
      published_at: new Date(when).toISOString(),
      kind: 'news',
      text: null,
    });
  }
  return out;
}

const BLS_RELEASES: Record<string, { name: string; url: string }> = {
  'Employment Situation': {
    name: 'Jobs report (Employment Situation)',
    url: 'https://www.bls.gov/news.release/empsit.nr0.htm',
  },
  'Consumer Price Index': {
    name: 'Consumer Price Index (CPI)',
    url: 'https://www.bls.gov/news.release/cpi.nr0.htm',
  },
  'Producer Price Index': {
    name: 'Producer Price Index (PPI)',
    url: 'https://www.bls.gov/news.release/ppi.nr0.htm',
  },
};

/** BLS release calendar (iCalendar) → the releases that move gold. Times are US Eastern. */
export function parseBlsCalendar(ics: string): NewsItem[] {
  const text = ics.replace(/\r/g, '').replace(/\n[ \t]/g, '');
  const out: NewsItem[] = [];
  for (const ev of text.match(/BEGIN:VEVENT[\s\S]*?END:VEVENT/g) ?? []) {
    const summary = ev.match(/SUMMARY:(.*)/)?.[1]?.trim() ?? '';
    const release = BLS_RELEASES[summary];
    const start = ev.match(/DTSTART[^:]*:(\d{8})T(\d{4})/);
    if (!release || !start) continue;
    const d = start[1]!;
    const hhmm = `${start[2]!.slice(0, 2)}:${start[2]!.slice(2)}`;
    const date = `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
    out.push({
      headline: `${release.name} released`,
      publisher: `BLS · ${hhmm} ET`,
      url: release.url,
      published_at: new Date(zonedInstant(date, hhmm, 'America/New_York')).toISOString(),
      kind: 'news',
      text: 'Scheduled US data release',
    });
  }
  return out.sort((a, b) => a.published_at.localeCompare(b.published_at));
}

interface CotRow {
  report_date_as_yyyy_mm_dd?: string;
  m_money_positions_long_all?: string;
  m_money_positions_short_all?: string;
  change_in_m_money_long_all?: string;
  change_in_m_money_short_all?: string;
}

const signed = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toLocaleString('en-US')}`;

/** CFTC rows → one item per weekly report, stamped at the Friday 15:30 ET release. */
export function cotItems(rows: CotRow[], label: string, reportUrl: string): NewsItem[] {
  const out: NewsItem[] = [];
  for (const r of rows) {
    const day = r.report_date_as_yyyy_mm_dd?.slice(0, 10);
    const long = Number(r.m_money_positions_long_all);
    const short = Number(r.m_money_positions_short_all);
    if (!day || !Number.isFinite(long) || !Number.isFinite(short)) continue;
    const net = long - short;
    const weekly = Number(r.change_in_m_money_long_all) - Number(r.change_in_m_money_short_all);
    const friday = new Date(Date.parse(`${day}T00:00:00Z`) + 3 * 86_400_000)
      .toISOString()
      .slice(0, 10);
    out.push({
      headline: `Funds' net ${net >= 0 ? 'long' : 'short'} in ${label}: ${Math.abs(net).toLocaleString('en-US')} contracts${
        Number.isFinite(weekly) ? ` (${signed(weekly)} on the week)` : ''
      }`,
      publisher: 'CFTC Commitments of Traders',
      url: reportUrl,
      published_at: new Date(zonedInstant(friday, '15:30', 'America/New_York')).toISOString(),
      kind: 'news',
      text: `Managed money: ${long.toLocaleString('en-US')} long, ${short.toLocaleString('en-US')} short (positions as of Tuesday ${day})`,
    });
  }
  return out.sort((a, b) => a.published_at.localeCompare(b.published_at));
}

// ── fetchers ─────────────────────────────────────────────────────────────

const cache = new Map<string, { at: number; items: NewsItem[] }>();
const TTL_MS = 30 * 60 * 1000;
const cached = async (key: string, load: () => Promise<NewsItem[]>) => {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.items;
  const items = await load();
  cache.set(key, { at: Date.now(), items });
  return items;
};

/** BLS (like SEC) refuses browser-like agents; identify the tool honestly. */
const POLITE_UA = process.env.NEWS_CONTACT_UA ?? 'portfolio-analyzer personal research';

const get = async (url: string, headers: Record<string, string> = {}) => {
  const res = await fetch(url, { headers, cache: 'no-store' });
  if (!res.ok) throw new Error(`${new URL(url).host} HTTP ${res.status}`);
  return res;
};

export const fedReleases = () =>
  cached('fed', async () =>
    parseFedRss(
      await (
        await get('https://www.federalreserve.gov/feeds/press_monetary.xml', {
          'User-Agent': POLITE_UA,
        })
      ).text(),
    ),
  );

export const blsReleases = () =>
  cached('bls', async () =>
    parseBlsCalendar(
      await (
        await get('https://www.bls.gov/schedule/news_release/bls.ics', { 'User-Agent': POLITE_UA })
      ).text(),
    ),
  );

const COT_LABEL: Record<string, string> = {
  GC: 'gold',
  MGC: 'gold',
  SI: 'silver',
  PL: 'platinum',
  PA: 'palladium',
};

export const cotReports = (root: string) =>
  cached(`cot:${METALS_COT[root]}`, async () => {
    const url = new URL('https://publicreporting.cftc.gov/resource/72hh-3qpy.json');
    url.searchParams.set('$limit', '120');
    url.searchParams.set('$order', 'report_date_as_yyyy_mm_dd DESC');
    url.searchParams.set('$where', `market_and_exchange_names='${METALS_COT[root]}'`);
    const rows = (await (await get(url.toString())).json()) as CotRow[];
    return cotItems(
      rows,
      COT_LABEL[root] ?? 'metals',
      'https://www.cftc.gov/dea/futures/deacmxsf.htm',
    );
  });
