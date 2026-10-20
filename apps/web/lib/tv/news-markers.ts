import type { NewsItem } from './news-types';

/**
 * Attach each filing to the candle it can first affect: the candle containing
 * its timestamp, or — when it landed between candles (after the close, a
 * weekend) — the next one. A filing newer than the last candle sits on it.
 * Filings older than the first candle are dropped. Returns bar index → items,
 * oldest first.
 */
export function groupByBar(
  items: NewsItem[],
  barTimes: number[],
  candleSecs: number,
): Map<number, NewsItem[]> {
  const out = new Map<number, NewsItem[]>();
  const n = barTimes.length;
  if (!n) return out;
  for (const item of items) {
    const t = Date.parse(item.published_at) / 1000;
    if (!Number.isFinite(t) || t < barTimes[0]!) continue;
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (barTimes[mid]! <= t) lo = mid;
      else hi = mid - 1;
    }
    let idx = lo;
    if (t >= barTimes[idx]! + candleSecs && idx + 1 < n) idx += 1;
    const list = out.get(idx) ?? [];
    list.push(item);
    out.set(idx, list);
  }
  for (const list of out.values())
    list.sort((a, b) => a.published_at.localeCompare(b.published_at));
  return out;
}
