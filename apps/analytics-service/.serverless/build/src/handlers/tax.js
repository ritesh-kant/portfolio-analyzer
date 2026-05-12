// ../../packages/tax-core/dist/index.js
var LTCG_EXEMPTION_LIMIT = 125e3;
var LTCG_DAYS_THRESHOLD = 365;
function calculateTaxSummary(transactions) {
  const sorted = [...transactions].sort((a, b) => new Date(a.executedAt).getTime() - new Date(b.executedAt).getTime());
  const lotsBySymbol = /* @__PURE__ */ new Map();
  let realizedLtcg = 0;
  let realizedStcg = 0;
  for (const tx of sorted) {
    const txDate = new Date(tx.executedAt);
    const lots = lotsBySymbol.get(tx.symbol) ?? [];
    if (tx.side === "buy") {
      lots.push({ quantity: tx.quantity, price: tx.price, date: txDate });
      lotsBySymbol.set(tx.symbol, lots);
      continue;
    }
    let remainingSell = tx.quantity;
    while (remainingSell > 0 && lots.length > 0) {
      const lot = lots[0];
      if (!lot) {
        break;
      }
      const matchedQty = Math.min(remainingSell, lot.quantity);
      const gain = (tx.price - lot.price) * matchedQty;
      const holdingDays = daysBetween(lot.date, txDate);
      if (holdingDays > LTCG_DAYS_THRESHOLD) {
        realizedLtcg += gain;
      } else {
        realizedStcg += gain;
      }
      lot.quantity -= matchedQty;
      remainingSell -= matchedQty;
      if (lot.quantity <= 0) {
        lots.shift();
      }
    }
    lotsBySymbol.set(tx.symbol, lots);
  }
  const ltcgExemptionUsed = Math.max(0, realizedLtcg);
  const ltcgExemptionRemaining = Math.max(0, LTCG_EXEMPTION_LIMIT - ltcgExemptionUsed);
  return {
    realized: {
      ltcg: realizedLtcg,
      stcg: realizedStcg
    },
    unrealized: {
      profits: 0,
      losses: 0
    },
    ltcgExemptionUsed,
    ltcgExemptionLimit: LTCG_EXEMPTION_LIMIT,
    ltcgExemptionRemaining,
    harvestable: []
  };
}
function daysBetween(start, end) {
  return Math.floor((end.getTime() - start.getTime()) / (1e3 * 60 * 60 * 24));
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

// src/handlers/tax.ts
var sampleTransactions = [
  {
    symbol: "INFY",
    quantity: 10,
    price: 1200,
    side: "buy",
    executedAt: "2024-01-11T10:00:00.000Z",
    broker: "zerodha"
  },
  {
    symbol: "INFY",
    quantity: 6,
    price: 1460,
    side: "sell",
    executedAt: "2025-06-19T10:00:00.000Z",
    broker: "zerodha"
  }
];
async function handler() {
  const result = calculateTaxSummary(sampleTransactions);
  return json(200, result);
}
export {
  handler
};
//# sourceMappingURL=tax.js.map
