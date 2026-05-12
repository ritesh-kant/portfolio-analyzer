// ../../packages/analytics-core/dist/index.js
function calculateBenchmarkComparison(portfolioCurrentValue, investedAmount, benchmarkStartNav, benchmarkEndNav) {
  const portfolioReturnPct = investedAmount === 0 ? 0 : (portfolioCurrentValue - investedAmount) / investedAmount;
  const benchmarkReturnPct = benchmarkStartNav === 0 ? 0 : (benchmarkEndNav - benchmarkStartNav) / benchmarkStartNav;
  const benchmarkEquivalentValue = investedAmount * (1 + benchmarkReturnPct);
  return {
    portfolioReturnPct: portfolioReturnPct * 100,
    benchmarkReturnPct: benchmarkReturnPct * 100,
    alphaPct: (portfolioReturnPct - benchmarkReturnPct) * 100,
    portfolioCurrentValue,
    benchmarkEquivalentValue
  };
}

// src/lib/http.ts
var CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
  "access-control-allow-headers": "content-type,authorization"
};
function json(statusCode, body) {
  return {
    statusCode,
    headers: {
      "content-type": "application/json",
      ...CORS_HEADERS
    },
    body: JSON.stringify(body)
  };
}

// src/handlers/benchmark.ts
var NIFTY50_SCHEME_CODE = "120716";
var MFAPI_BASE = "https://api.mfapi.in";
function parseDdmmYyyyToDate(dateStr) {
  const [dd, mm, yyyy] = dateStr.split("-");
  return /* @__PURE__ */ new Date(`${yyyy}-${mm}-${dd}T00:00:00.000Z`);
}
async function fetchNifty50NavHistory() {
  const res = await fetch(`${MFAPI_BASE}/mf/${NIFTY50_SCHEME_CODE}`, {
    headers: { Accept: "application/json" }
  });
  if (!res.ok) {
    throw new Error(`MFAPI fetch failed: ${res.status}`);
  }
  const body = await res.json();
  return body.data;
}
function navOnOrBefore(history, targetDate) {
  for (const entry of history) {
    const d = parseDdmmYyyyToDate(entry.date);
    if (d <= targetDate) {
      return parseFloat(entry.nav);
    }
  }
  return null;
}
async function handler(event) {
  const qs = event.queryStringParameters ?? {};
  const portfolioCurrentValue = qs.currentValue ? parseFloat(qs.currentValue) : 160480;
  const investedAmount = qs.investedAmount ? parseFloat(qs.investedAmount) : 141209;
  const inceptionDateStr = qs.inceptionDate ?? "2024-01-01";
  try {
    const history = await fetchNifty50NavHistory();
    const latestEntry = history[0];
    const latestNav = latestEntry ? parseFloat(latestEntry.nav) : null;
    const inceptionDate = new Date(inceptionDateStr);
    const navAtInception = navOnOrBefore(history, inceptionDate);
    if (!latestNav || !navAtInception) {
      return json(500, { error: "Insufficient NAV data from MFAPI" });
    }
    const result = calculateBenchmarkComparison(
      portfolioCurrentValue,
      investedAmount,
      navAtInception,
      latestNav
    );
    return json(200, {
      ...result,
      benchmarkScheme: "UTI Nifty 50 Index Fund Direct Growth",
      benchmarkAsOf: latestEntry?.date,
      navAtInception: navAtInception.toFixed(4),
      navLatest: latestNav.toFixed(4)
    });
  } catch (error) {
    const result = calculateBenchmarkComparison(portfolioCurrentValue, investedAmount, 158.22, 176.31);
    return json(200, {
      ...result,
      benchmarkScheme: "UTI Nifty 50 Index Fund Direct Growth (fallback)",
      note: error instanceof Error ? error.message : "MFAPI unavailable"
    });
  }
}
export {
  handler
};
//# sourceMappingURL=benchmark.js.map
