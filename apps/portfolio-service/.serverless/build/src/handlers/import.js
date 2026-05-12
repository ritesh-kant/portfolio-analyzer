// ../../packages/broker-sdk/dist/groww-csv-adapter.js
var GrowwCsvAdapter = class {
  csv;
  constructor(csv) {
    this.csv = csv;
  }
  async getHoldings() {
    const rows = parseGrowwCsv(this.csv);
    return rows.map((row) => {
      const averagePrice = row.quantity > 0 ? row.investedAmount / row.quantity : 0;
      const currentPrice = row.quantity > 0 ? row.currentValue / row.quantity : 0;
      return {
        symbol: row.symbol,
        quantity: row.quantity,
        averagePrice,
        currentPrice,
        investedAmount: row.investedAmount,
        currentValue: row.currentValue,
        sector: row.sector,
        assetType: "mf",
        broker: "groww",
        asOf: (/* @__PURE__ */ new Date()).toISOString()
      };
    });
  }
};
function parseGrowwCsv(csv) {
  const lines = csv.split(/\r?\n/);
  const headerIdx = lines.findIndex((line) => line.trim().toLowerCase().startsWith("scheme name"));
  if (headerIdx === -1) {
    throw new Error('Unrecognised Groww CSV format \u2014 expected a header row starting with "Scheme Name". Please export your holdings from Groww \u2192 Portfolio \u2192 Download.');
  }
  const headers = (lines[headerIdx] ?? "").split(",").map((h) => h.trim().toLowerCase());
  const idx = {
    schemeName: headers.indexOf("scheme name"),
    category: headers.indexOf("category"),
    units: headers.indexOf("units"),
    investedValue: headers.indexOf("invested value"),
    currentValue: headers.indexOf("current value")
  };
  if (idx.schemeName < 0 || idx.units < 0 || idx.investedValue < 0 || idx.currentValue < 0) {
    throw new Error("Groww CSV is missing required columns: Scheme Name, Units, Invested Value, Current Value.");
  }
  const dataLines = lines.slice(headerIdx + 1).filter((line) => {
    const stripped = line.replace(/,/g, "").trim();
    return stripped.length > 0;
  });
  const byScheme = /* @__PURE__ */ new Map();
  for (const line of dataLines) {
    const cols = line.split(",").map((c) => c.trim());
    const schemeName = cols[idx.schemeName] ?? "";
    if (!schemeName)
      continue;
    const units = Number(cols[idx.units] ?? "0");
    const investedValue = Number(cols[idx.investedValue] ?? "0");
    const currentVal = Number(cols[idx.currentValue] ?? "0");
    const category = idx.category >= 0 ? cols[idx.category] ?? "Other" : "Other";
    if (!isFinite(units) || !isFinite(investedValue) || !isFinite(currentVal))
      continue;
    const existing = byScheme.get(schemeName);
    if (existing) {
      existing.quantity += units;
      existing.investedAmount += investedValue;
      existing.currentValue += currentVal;
    } else {
      byScheme.set(schemeName, {
        symbol: schemeName,
        quantity: units,
        investedAmount: investedValue,
        currentValue: currentVal,
        sector: category
      });
    }
  }
  return [...byScheme.values()];
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

// src/lib/store.ts
var store = { holdings: [] };
function setHoldings(holdings) {
  store.holdings = holdings;
}

// src/handlers/import.ts
async function handler(event) {
  if (!event.body) {
    return json(400, { error: "CSV payload expected in request body" });
  }
  const adapter = new GrowwCsvAdapter(event.body);
  const holdings = await adapter.getHoldings();
  setHoldings(holdings);
  return json(200, {
    source: "groww_csv",
    count: holdings.length,
    holdings
  });
}
export {
  handler
};
//# sourceMappingURL=import.js.map
