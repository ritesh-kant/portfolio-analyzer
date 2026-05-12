// ../../packages/broker-sdk/dist/zerodha-adapter.js
var ZerodhaAdapter = class {
  config;
  baseUrl;
  constructor(config) {
    this.config = config;
    this.baseUrl = config.baseUrl ?? "https://api.kite.trade";
  }
  async getHoldings() {
    const response = await fetch(`${this.baseUrl}/portfolio/holdings`, {
      headers: {
        "X-Kite-Version": "3",
        Authorization: `token ${this.config.apiKey}:${this.config.accessToken}`
      }
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch Zerodha holdings: ${response.status}`);
    }
    const payload = await response.json();
    return payload.data.map((item) => {
      const investedAmount = item.average_price * item.quantity;
      const currentValue = item.last_price * item.quantity;
      return {
        symbol: item.tradingsymbol,
        quantity: item.quantity,
        averagePrice: item.average_price,
        currentPrice: item.last_price,
        investedAmount,
        currentValue,
        assetType: "stock",
        broker: "zerodha",
        asOf: (/* @__PURE__ */ new Date()).toISOString()
      };
    });
  }
};

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
function setHoldings(holdings) {
  store.holdings = holdings;
}

// src/handlers/holdings.ts
async function handler() {
  const apiKey = process.env.ZERODHA_API_KEY;
  const accessToken = process.env.ZERODHA_ACCESS_TOKEN;
  if (!apiKey || !accessToken) {
    const cached = getHoldings();
    return json(200, {
      source: cached.length > 0 ? "imported" : "mock",
      holdings: cached,
      message: cached.length === 0 ? "No holdings found. Import a Groww CSV or set ZERODHA_API_KEY + ZERODHA_ACCESS_TOKEN." : `${cached.length} holdings loaded from last import.`
    });
  }
  const adapter = new ZerodhaAdapter({ apiKey, accessToken });
  const holdings = await adapter.getHoldings();
  setHoldings(holdings);
  return json(200, {
    source: "zerodha",
    holdings
  });
}
export {
  handler
};
//# sourceMappingURL=holdings.js.map
