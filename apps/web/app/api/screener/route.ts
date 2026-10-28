import { NextResponse } from 'next/server';
import { FIELD, DEFAULT_SORT } from '../../../lib/screener/fields';
import { marketOf } from '../../../lib/screener/markets';
import { sanitizeFilters } from '../../../lib/screener/query';
import { DEFAULT_LIMIT, MAX_LIMIT, runScreen } from '../../../lib/screener/run';
import type { ScreenerRequest } from '../../../lib/screener/types';
import { pocSetups, profiles, rvolsAtTime, screenPage } from '../../../lib/screener/yahoo';

export const dynamic = 'force-dynamic';
/** Per-name lookups (float, rel vol at time) can take a few seconds on a cold cache. */
export const maxDuration = 30;

/**
 * POST { filters, sort, columns, limit } → { rows, total, asOf, notes }.
 * Everything in the body is untrusted: unknown fields and malformed values are dropped, not passed to Yahoo.
 */
export async function POST(req: Request) {
  let body: Partial<ScreenerRequest>;
  try {
    body = (await req.json()) as Partial<ScreenerRequest>;
  } catch {
    return NextResponse.json({ error: 'bad json' }, { status: 400 });
  }

  const sortKey = typeof body.sort?.key === 'string' ? body.sort.key : DEFAULT_SORT.key;
  const sort = {
    key:
      FIELD.has(sortKey) || sortKey === 'symbol' || sortKey === 'name' ? sortKey : DEFAULT_SORT.key,
    dir: body.sort?.dir === 'asc' ? ('asc' as const) : ('desc' as const),
  };
  const columns = Array.isArray(body.columns)
    ? body.columns.filter((c): c is string => typeof c === 'string' && FIELD.has(c))
    : [];
  const limit = Math.min(MAX_LIMIT, Math.max(1, Math.floor(Number(body.limit) || DEFAULT_LIMIT)));

  const market = marketOf(body.market);

  try {
    const res = await runScreen(
      {
        market: market.id,
        filters: sanitizeFilters(body.filters, market.id),
        sort,
        columns,
        limit,
      },
      { screenPage, profiles, rvolsAtTime, pocSetups },
    );
    return NextResponse.json(res);
  } catch (e) {
    const message = e instanceof Error ? e.message : 'screener unavailable';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
