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

// src/lib/store.ts
var store = { holdings: [] };
function getHoldings() {
  return store.holdings;
}

// src/handlers/summary.ts
async function handler() {
  const holdings = getHoldings();
  if (holdings.length === 0) {
    return json(200, {
      investedAmount: 141209,
      currentValue: 160480,
      pnl: 19271,
      pnlPct: 13.64,
      source: "demo"
    });
  }
  const investedAmount = holdings.reduce((sum, h) => sum + h.investedAmount, 0);
  const currentValue = holdings.reduce((sum, h) => sum + h.currentValue, 0);
  const pnl = currentValue - investedAmount;
  const pnlPct = investedAmount === 0 ? 0 : pnl / investedAmount * 100;
  return json(200, {
    investedAmount: Math.round(investedAmount),
    currentValue: Math.round(currentValue),
    pnl: Math.round(pnl),
    pnlPct: Math.round(pnlPct * 100) / 100,
    source: "live",
    holdingsCount: holdings.length
  });
}
export {
  handler
};
//# sourceMappingURL=summary.js.map
