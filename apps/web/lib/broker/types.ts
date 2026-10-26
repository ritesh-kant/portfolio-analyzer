/** One broker-neutral order model, the way TradingView's Broker API hides each broker behind the same calls. */
export type BrokerId = 'upstox' | 'kite';
export type Side = 'buy' | 'sell';
/** `stop` is a stop-loss-market order: nothing rests on the book until the trigger trades. */
export type OrderKind = 'market' | 'limit' | 'stop';
/** `intraday` squares off the same day (Upstox I / Kite MIS); `delivery` is held (D / CNC). */
export type Product = 'intraday' | 'delivery';

export interface PlaceOrder {
  /** Caller-generated id; a repeat inside a couple of minutes is refused, so a double click cannot send two orders. */
  clientId: string;
  /** NSE trading symbol without the .NS suffix. */
  symbol: string;
  side: Side;
  kind: OrderKind;
  qty: number;
  /** Limit price (limit orders). */
  price?: number;
  /** Trigger price (stop orders). */
  trigger?: number;
  product: Product;
  /** Last chart price, used only to size a market order against the notional cap. */
  refPrice?: number;
}

export type OrderStatus = 'open' | 'complete' | 'cancelled' | 'rejected';

export interface BrokerOrder {
  id: string;
  symbol: string;
  side: Side;
  kind: OrderKind;
  product: Product;
  qty: number;
  filledQty: number;
  price: number;
  avgPrice: number;
  status: OrderStatus;
  /** The broker's own status string, kept because it explains rejections. */
  rawStatus: string;
  message?: string;
  /** Unix seconds. */
  time: number;
}

export interface BrokerPosition {
  symbol: string;
  product: Product;
  /** Signed: > 0 long, < 0 short. */
  qty: number;
  avg: number;
  ltp: number;
  pnl: number;
}

/** What the chart needs from a broker; an implementation is bound to one day's access token. */
export interface TradingBroker {
  profile(): Promise<{ user: string }>;
  orders(): Promise<BrokerOrder[]>;
  positions(): Promise<BrokerPosition[]>;
  place(order: PlaceOrder): Promise<{ orderId: string }>;
  cancel(orderId: string): Promise<void>;
}

export interface BrokerDefinition {
  id: BrokerId;
  name: string;
  /** API key and secret are present in the environment. */
  configured(): boolean;
  loginUrl(state: string): string;
  /** Trade the redirect's query for an access token. */
  exchange(query: URLSearchParams): Promise<string>;
  connect(token: string): TradingBroker;
}

export class BrokerError extends Error {
  constructor(
    message: string,
    readonly status = 502,
  ) {
    super(message);
  }
}
