import { kite } from './kite';
import { upstox } from './upstox';
import { BrokerError, type BrokerDefinition, type BrokerId } from './types';

const BROKERS: Record<BrokerId, BrokerDefinition> = { upstox, kite };

export const allBrokers = (): BrokerDefinition[] => Object.values(BROKERS);

export function brokerById(id: string): BrokerDefinition {
  const b = (BROKERS as Record<string, BrokerDefinition | undefined>)[id];
  if (!b) throw new BrokerError(`unknown broker ${id}`, 404);
  return b;
}
