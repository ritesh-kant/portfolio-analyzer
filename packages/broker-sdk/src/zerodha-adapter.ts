import type { Holding } from '@portfolio-analyzer/shared-types';

import type { BrokerAdapter, ZerodhaConfig } from './types.js';

interface ZerodhaHoldingApi {
  tradingsymbol: string;
  average_price: number;
  last_price: number;
  quantity: number;
}

interface ZerodhaHoldingsResponse {
  status: string;
  data: ZerodhaHoldingApi[];
}

export class ZerodhaAdapter implements BrokerAdapter {
  private readonly baseUrl: string;

  constructor(private readonly config: ZerodhaConfig) {
    this.baseUrl = config.baseUrl ?? 'https://api.kite.trade';
  }

  async getHoldings(): Promise<Holding[]> {
    const response = await fetch(`${this.baseUrl}/portfolio/holdings`, {
      headers: {
        'X-Kite-Version': '3',
        Authorization: `token ${this.config.apiKey}:${this.config.accessToken}`,
      },
    });

    if (!response.ok) {
      throw new Error(`Failed to fetch Zerodha holdings: ${response.status}`);
    }

    const payload = (await response.json()) as ZerodhaHoldingsResponse;

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
        assetType: 'stock',
        broker: 'zerodha',
        asOf: new Date().toISOString(),
      } as Holding;
    });
  }
}
