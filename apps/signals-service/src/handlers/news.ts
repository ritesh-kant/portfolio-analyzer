import axios from 'axios';
import { json } from '../lib/http.js';
import type { NewsArticle } from '../lib/types.js';

interface NewsdataArticle {
  title: string;
  source_id?: string;
  source_name?: string;
  pubDate?: string;
  link?: string;
}

interface NewsdataResponse {
  status: string;
  results?: NewsdataArticle[];
}

export async function handler(event?: {
  queryStringParameters?: Record<string, string | undefined> | null;
}): Promise<ReturnType<typeof json>> {
  const apiKey = process.env.NEWSDATA_API_KEY;
  if (!apiKey) {
    return json(200, { articles: [], error: 'NEWSDATA_API_KEY not configured' });
  }

  const qs = event?.queryStringParameters ?? {};
  const symbol = qs.symbol;
  const companyName = qs.companyName;

  try {
    const base = 'https://newsdata.io/api/1/news';

    // If we have a specific symbol, we fetch news for that stock.
    // Otherwise we fetch general market news as before.
    if (symbol) {
      const query = companyName ? `${symbol} OR "${companyName}"` : symbol;
      const response = await axios.get<NewsdataResponse>(base, {
        params: { apikey: apiKey, q: query, language: 'en' },
        timeout: 10_000,
      });
      const raw = response.data.results ?? [];
      const articles = processArticles(raw);
      return json(200, { articles });
    }

    const [indiaRes, globalRes] = await Promise.allSettled([
      axios.get<NewsdataResponse>(base, {
        params: { apikey: apiKey, country: 'in', category: 'business', language: 'en' },
        timeout: 10_000,
      }),
      axios.get<NewsdataResponse>(base, {
        params: { apikey: apiKey, language: 'en', category: 'business', q: 'stock market OR Fed OR RBI OR crude oil' },
        timeout: 10_000,
      }),
    ]);

    const raw: NewsdataArticle[] = [];
    if (indiaRes.status === 'fulfilled') raw.push(...(indiaRes.value.data.results ?? []));
    if (globalRes.status === 'fulfilled') raw.push(...(globalRes.value.data.results ?? []));

    const articles = processArticles(raw);
    return json(200, { articles });
  } catch (err) {
    return json(200, { articles: [], error: String(err) });
  }
}

function processArticles(raw: NewsdataArticle[]): NewsArticle[] {
  const seen = new Set<string>();
  const articles: NewsArticle[] = [];
  for (const a of raw) {
    if (!a.title || seen.has(a.title)) continue;
    seen.add(a.title);
    articles.push({
      title: a.title,
      source: a.source_name ?? a.source_id ?? 'Unknown',
      publishedAt: a.pubDate ?? '',
      url: a.link ?? '',
    });
    if (articles.length >= 15) break;
  }
  return articles;
}
