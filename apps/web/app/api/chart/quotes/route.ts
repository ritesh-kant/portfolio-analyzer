import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const SYMBOL = /^[A-Za-z0-9.^=\-_&]{1,24}$/;

export interface Quote {
  price: number;
  prevClose: number;
  change: number;
  changePct: number;
}

interface Spark {
  symbol?: string;
  close?: Array<number | null>;
  chartPreviousClose?: number | null;
}

/** Last price and day change for up to 40 symbols in one Yahoo call: `?symbols=A,B,C`. */
export async function GET(req: Request) {
  const symbols = (new URL(req.url).searchParams.get('symbols') ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 40);
  if (!symbols.length || !symbols.every((s) => SYMBOL.test(s))) {
    return NextResponse.json({ error: 'bad symbols' }, { status: 400 });
  }
  for (const host of ['query1.finance.yahoo.com', 'query2.finance.yahoo.com']) {
    try {
      const res = await fetch(
        `https://${host}/v8/finance/spark?symbols=${encodeURIComponent(symbols.join(','))}&range=1d&interval=1d`,
        { headers: { 'User-Agent': 'Mozilla/5.0' }, cache: 'no-store' },
      );
      if (!res.ok) continue;
      const body = (await res.json()) as Record<string, Spark>;
      const quotes: Record<string, Quote> = {};
      for (const [symbol, row] of Object.entries(body)) {
        const closes = (row.close ?? []).filter((c): c is number => c != null);
        const price = closes[closes.length - 1];
        const prev = row.chartPreviousClose;
        if (price == null || prev == null || prev === 0) continue;
        quotes[row.symbol ?? symbol] = {
          price,
          prevClose: prev,
          change: price - prev,
          changePct: ((price - prev) / prev) * 100,
        };
      }
      return NextResponse.json({ quotes });
    } catch {
      // try the next host
    }
  }
  return NextResponse.json({ error: 'quotes unavailable' }, { status: 502 });
}
