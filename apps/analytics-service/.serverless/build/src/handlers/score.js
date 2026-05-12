// ../../packages/analytics-core/dist/index.js
function calculateHealthScore(holdings, alphaPct) {
  const totalValue = holdings.reduce((sum, h) => sum + h.currentValue, 0);
  const maxSingleWeight = holdings.reduce((max, h) => {
    const weight = totalValue === 0 ? 0 : h.currentValue / totalValue;
    return Math.max(max, weight);
  }, 0);
  const diversification = Math.max(0, 35 - maxSingleWeight * 50);
  const benchmarkPerformance = Math.max(0, Math.min(35, 17.5 + alphaPct));
  const riskConcentration = Math.max(0, 30 - maxSingleWeight * 40);
  const score = Math.round(diversification + benchmarkPerformance + riskConcentration);
  return {
    score,
    breakdown: {
      diversification: round2(diversification),
      benchmarkPerformance: round2(benchmarkPerformance),
      riskConcentration: round2(riskConcentration)
    }
  };
}
function round2(value) {
  return Math.round(value * 100) / 100;
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

// src/handlers/score.ts
async function handler(event) {
  const qs = event.queryStringParameters ?? {};
  const alphaPct = qs.alphaPct ? parseFloat(qs.alphaPct) : 2.2;
  let holdings;
  try {
    holdings = qs.holdings ? JSON.parse(decodeURIComponent(qs.holdings)) : defaultHoldings();
  } catch {
    holdings = defaultHoldings();
  }
  const result = calculateHealthScore(holdings, alphaPct);
  return json(200, result);
}
function defaultHoldings() {
  return [
    {
      symbol: "HDFCBANK",
      quantity: 18,
      averagePrice: 1410,
      currentPrice: 1665,
      investedAmount: 25380,
      currentValue: 29970,
      assetType: "stock",
      broker: "zerodha",
      sector: "Financials",
      asOf: (/* @__PURE__ */ new Date()).toISOString()
    },
    {
      symbol: "INFY",
      quantity: 22,
      averagePrice: 1312,
      currentPrice: 1470,
      investedAmount: 28864,
      currentValue: 32340,
      assetType: "stock",
      broker: "zerodha",
      sector: "Technology",
      asOf: (/* @__PURE__ */ new Date()).toISOString()
    },
    {
      symbol: "UTI_NIFTY50",
      quantity: 410,
      averagePrice: 158,
      currentPrice: 176,
      investedAmount: 64780,
      currentValue: 72160,
      assetType: "mf",
      broker: "groww",
      sector: "Index",
      asOf: (/* @__PURE__ */ new Date()).toISOString()
    },
    {
      symbol: "RELIANCE",
      quantity: 9,
      averagePrice: 2465,
      currentPrice: 2890,
      investedAmount: 22185,
      currentValue: 26010,
      assetType: "stock",
      broker: "groww",
      sector: "Energy",
      asOf: (/* @__PURE__ */ new Date()).toISOString()
    }
  ];
}
export {
  handler
};
//# sourceMappingURL=score.js.map
