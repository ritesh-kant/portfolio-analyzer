import type { Holding } from '@portfolio-analyzer/shared-types';

export interface BrokerAdapter {
  getHoldings(): Promise<Holding[]>;
}

export interface ZerodhaConfig {
  apiKey: string;
  accessToken: string;
  baseUrl?: string;
}
