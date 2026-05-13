import { calculateBenchmarkComparison } from '@portfolio-analyzer/analytics-core';

import { json } from '../lib/http.js';

const NIFTY50_SCHEME_CODE = '120716'; // UTI Nifty 50 Index Fund Direct Growth
const MFAPI_BASE = 'https://api.mfapi.in';

interface MfApiEntry {
  date: string; // "DD-MM-YYYY"
  nav: string;
}

interface MfApiResponse {
  data: MfApiEntry[];
}

function parseDdmmYyyyToDate(dateStr: string): Date {
  const [dd, mm, yyyy] = dateStr.split('-');
  return new Date(`${yyyy}-${mm}-${dd}T00:00:00.000Z`);
}

async function fetchNifty50NavHistory(): Promise<MfApiEntry[]> {
  const res = await fetch(`${MFAPI_BASE}/mf/${NIFTY50_SCHEME_CODE}`, {
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) {
    throw new Error(`MFAPI fetch failed: ${res.status}`);
  }
  const body = (await res.json()) as MfApiResponse;
  return body.data;
}

function navOnOrBefore(history: MfApiEntry[], targetDate: Date): number | null {
  for (const entry of history) {
    const d = parseDdmmYyyyToDate(entry.date);
    if (d <= targetDate) {
      return parseFloat(entry.nav);
    }
  }
  return null;
}

export async function handler(event: {
  queryStringParameters?: Record<string, string | undefined> | null;
}) {
  const qs = event.queryStringParameters ?? {};
  const portfolioCurrentValue = qs.currentValue ? parseFloat(qs.currentValue) : 160480;
  const investedAmount = qs.investedAmount ? parseFloat(qs.investedAmount) : 141209;
  const inceptionDateStr = qs.inceptionDate ?? '2024-01-01';

  try {
    const history = await fetchNifty50NavHistory();

    // history is newest-first from MFAPI
    const latestEntry = history[0];
    const latestNav = latestEntry ? parseFloat(latestEntry.nav) : null;

    const inceptionDate = new Date(inceptionDateStr);
    const navAtInception = navOnOrBefore(history, inceptionDate);

    if (!latestNav || !navAtInception) {
      return json(500, { error: 'Insufficient NAV data from MFAPI' });
    }

    const result = calculateBenchmarkComparison(
      portfolioCurrentValue,
      investedAmount,
      navAtInception,
      latestNav,
    );

    return json(200, {
      ...result,
      benchmarkScheme: 'UTI Nifty 50 Index Fund Direct Growth',
      benchmarkAsOf: latestEntry?.date,
      navAtInception: navAtInception.toFixed(4),
      navLatest: latestNav.toFixed(4),
    });
  } catch (error) {
    // Fallback to hardcoded stub if MFAPI is unreachable
    const result = calculateBenchmarkComparison(
      portfolioCurrentValue,
      investedAmount,
      158.22,
      176.31,
    );
    return json(200, {
      ...result,
      benchmarkScheme: 'UTI Nifty 50 Index Fund Direct Growth (fallback)',
      note: error instanceof Error ? error.message : 'MFAPI unavailable',
    });
  }
}
