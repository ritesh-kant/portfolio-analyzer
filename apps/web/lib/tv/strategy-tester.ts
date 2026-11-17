/**
 * Strategy Tester maths (phase 3 of research/specs/ar-model-chart-strategy-plan.md): turns a strategy's trades
 * (entry, stop, exit in price points) into money after costs, and the numbers the tester shows. Pure, shared by
 * the chart's Strategy Tester panel and the command-line runner (`pnpm bt:ar`), so both print the same figures.
 *
 * Costs per round trip, whatever the exit was: slippage on both sides (ticks) + commission on both sides + for NSE
 * the levies on the trade's value (STT, exchange, SEBI, stamp duty, GST). US defaults approximate IBKR's all-in
 * futures fees; NSE rates are those in force since 1 April 2026 (`NSE_FUT_LEVIES`). All editable in the tester.
 */

/** Taxes and exchange fees charged as a fraction of the traded value (Indian derivatives). */
export interface Levies {
  /** Securities transaction tax, on the SELL side only. */
  sttSell: number;
  /** Exchange transaction charge, each side. */
  exchange: number;
  /** SEBI turnover fee, each side. */
  sebi: number;
  /** Stamp duty, on the BUY side only. */
  stampBuy: number;
  /** GST, on brokerage + exchange charge + SEBI fee (not on STT or stamp duty). */
  gst: number;
}

/**
 * NSE equity futures, rates from 1 April 2026: STT 0.05 % of the sell value (Finance Act 2026, up from 0.02 %),
 * NSE transaction charge ~0.00183 % a side, SEBI ₹10 a crore, stamp duty 0.002 % of the buy value, GST 18 %.
 */
export const NSE_FUT_LEVIES: Levies = { sttSell: 0.0005, exchange: 0.0000183, sebi: 0.000001, stampBuy: 0.00002, gst: 0.18 };

export interface Instrument {
  id: string;
  label: string;
  /** Money per 1.0 price point per contract (NSE: the lot size, from the contract). */
  pointValue: number;
  /** Smallest price step (NSE: from the contract). */
  tick: number;
  /** Commission (+ flat fees) per side, in the instrument's currency: per contract, or per order when `perOrder`. */
  commission: number;
  currency: string;
  /** Brokerage is a flat fee per order whatever the size (Indian discount brokers' ₹20). */
  perOrder?: boolean;
  levies?: Levies;
}

export const INSTRUMENTS: Instrument[] = [
  { id: 'NQ', label: 'NQ · E-mini Nasdaq-100', pointValue: 20, tick: 0.25, commission: 2.25, currency: 'USD' },
  { id: 'MNQ', label: 'MNQ · Micro Nasdaq-100', pointValue: 2, tick: 0.25, commission: 0.62, currency: 'USD' },
  { id: 'ES', label: 'ES · E-mini S&P 500', pointValue: 50, tick: 0.25, commission: 2.25, currency: 'USD' },
  { id: 'MES', label: 'MES · Micro S&P 500', pointValue: 5, tick: 0.25, commission: 0.62, currency: 'USD' },
  { id: 'GC', label: 'GC · Gold', pointValue: 100, tick: 0.1, commission: 2.4, currency: 'USD' },
  { id: 'MGC', label: 'MGC · Micro Gold', pointValue: 10, tick: 0.1, commission: 0.85, currency: 'USD' },
  {
    id: 'NSEFUT',
    label: 'NSE futures (lot and tick from the contract)',
    pointValue: 1,
    tick: 0.1,
    commission: 20,
    currency: 'INR',
    perOrder: true,
    levies: NSE_FUT_LEVIES,
  },
  { id: 'custom', label: 'Other (1 unit = 1 point)', pointValue: 1, tick: 0.01, commission: 0, currency: '' },
];

export interface CostCfg {
  instrument: string;
  /** Contracts (NSE: lots). */
  contracts: number;
  /** Per side (per contract, or per order for NSE); null = the instrument's default. */
  commission: number | null;
  /** Ticks lost to slippage on each side (entry and exit). */
  slipTicks: number;
  /** Units per contract (NSE lot size) when set by hand; null/absent = the contract's own. */
  lot?: number | null;
}

export const COST_DEFAULTS: CostCfg = { instrument: 'NQ', contracts: 1, commission: null, slipTicks: 1 };

/** What the chart knows of the traded contract (NSE: from the exchange's instrument list). */
export interface ContractInfo {
  lotSize?: number;
  tickSize?: number;
}

const byId = (id: string) => INSTRUMENTS.find((i) => i.id === id) ?? INSTRUMENTS[INSTRUMENTS.length - 1]!;

/** The instrument a chart symbol trades: `NQ=F` → NQ, `NIFTY1!.NFO` → NSE futures …, anything else → custom. */
export function instrumentFor(symbol: string): Instrument {
  if (/\.NFO$|1!$/i.test(symbol)) return byId('NSEFUT');
  const root = /^([A-Z]+)=F$/i.exec(symbol)?.[1]?.toUpperCase();
  return INSTRUMENTS.find((i) => i.id === root) ?? byId('custom');
}

export function instrumentOf(cfg: CostCfg): Instrument {
  return byId(cfg.instrument);
}

/** The instrument with the contract's own lot and tick filled in, and the settings' overrides applied. */
export function contractSpec(cfg: CostCfg, contract?: ContractInfo) {
  const ins = instrumentOf(cfg);
  const sized = ins.id === 'NSEFUT' || ins.id === 'custom';
  const pointValue = sized ? (cfg.lot ?? (ins.id === 'NSEFUT' ? contract?.lotSize : undefined) ?? ins.pointValue) : ins.pointValue;
  const tick = ins.id === 'NSEFUT' ? (contract?.tickSize ?? ins.tick) : ins.tick;
  return { ins, pointValue, tick, commission: cfg.commission ?? ins.commission };
}

export interface CostBreakdown {
  slippage: number;
  commission: number;
  stt: number;
  exchange: number;
  sebi: number;
  stamp: number;
  gst: number;
  total: number;
}

/** Money one round trip costs at these prices, for `cfg.contracts` contracts. */
export function roundTripCost(
  cfg: CostCfg,
  dir: 'long' | 'short',
  entry: number,
  exit: number,
  contract?: ContractInfo,
): CostBreakdown {
  const spec = contractSpec(cfg, contract);
  const q = spec.pointValue * cfg.contracts;
  const slippage = 2 * cfg.slipTicks * spec.tick * q;
  const commission = 2 * spec.commission * (spec.ins.perOrder ? 1 : cfg.contracts);
  const lv = spec.ins.levies;
  if (!lv) return { slippage, commission, stt: 0, exchange: 0, sebi: 0, stamp: 0, gst: 0, total: slippage + commission };
  const buy = (dir === 'long' ? entry : exit) * q;
  const sell = (dir === 'long' ? exit : entry) * q;
  const stt = lv.sttSell * sell;
  const exchange = lv.exchange * (buy + sell);
  const sebi = lv.sebi * (buy + sell);
  const stamp = lv.stampBuy * buy;
  const gst = lv.gst * (commission + exchange + sebi);
  return { slippage, commission, stt, exchange, sebi, stamp, gst, total: slippage + commission + stt + exchange + sebi + stamp + gst };
}

/** Round-trip cost in price points, at `price` (only Indian levies depend on it). */
export function costPoints(cfg: CostCfg, price = 0, contract?: ContractInfo): number {
  const q = contractSpec(cfg, contract).pointValue * cfg.contracts;
  return q > 0 ? roundTripCost(cfg, 'long', price, price, contract).total / q : 0;
}

/** What the tester needs of a trade: any strategy whose trades carry these fields can be tested. */
export interface TestTrade {
  id: number;
  dir: 'long' | 'short';
  entryIndex: number;
  entry: number;
  stop: number;
  target: number;
  exitIndex: number | null;
  exit: number | null;
  outcome: string;
  warnings?: string[];
}

export interface TradeResult<T extends TestTrade = TestTrade> {
  trade: T;
  grossPts: number;
  netPts: number;
  /** Money for `contracts` contracts. */
  gross: number;
  net: number;
  cost: number;
  /** Net points ÷ initial risk (entry to stop). */
  netR: number;
}

/** Closed trades only (an open trade has no result yet), with costs taken off. */
export function tradeResults<T extends TestTrade>(trades: T[], cfg: CostCfg, contract?: ContractInfo): TradeResult<T>[] {
  const k = contractSpec(cfg, contract).pointValue * cfg.contracts;
  const out: TradeResult<T>[] = [];
  for (const t of trades) {
    if (t.exit == null || t.exitIndex == null || t.outcome === 'open') continue;
    const grossPts = t.dir === 'long' ? t.exit - t.entry : t.entry - t.exit;
    const cost = roundTripCost(cfg, t.dir, t.entry, t.exit, contract).total;
    const netPts = k > 0 ? grossPts - cost / k : grossPts;
    const risk = Math.abs(t.entry - t.stop);
    out.push({
      trade: t,
      grossPts,
      netPts,
      gross: grossPts * k,
      net: grossPts * k - cost,
      cost,
      netR: risk > 0 ? netPts / risk : 0,
    });
  }
  return out;
}

export const median = (xs: number[]): number => {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
};

export interface TesterStats {
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  gross: number;
  cost: number;
  net: number;
  avgNet: number;
  medianNet: number;
  avgR: number;
  medianR: number;
  /** Gross profit of winners ÷ gross loss of losers, after costs (Infinity with no losers, 0 with no trades). */
  profitFactor: number;
  avgWin: number;
  avgLoss: number;
  best: number;
  worst: number;
  /** Largest fall of the running net total from a previous high (a positive number). */
  maxDrawdown: number;
  /** Net total without the 3 best trades: how much rests on a few. */
  netWithoutTop3: number;
  /** Running net total after each trade, in exit order. */
  equity: number[];
  byOutcome: Record<string, { n: number; net: number }>;
  byDir: Record<'long' | 'short', { n: number; net: number; wins: number }>;
}

export function testerStats(results: TradeResult[]): TesterStats {
  const rs = [...results].sort((a, b) => (a.trade.exitIndex ?? 0) - (b.trade.exitIndex ?? 0));
  const nets = rs.map((r) => r.net);
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  const winners = nets.filter((x) => x > 0);
  const losers = nets.filter((x) => x <= 0);
  const lossSum = -sum(losers);
  const equity: number[] = [];
  let run = 0;
  let peak = 0;
  let maxDrawdown = 0;
  for (const x of nets) {
    run += x;
    equity.push(run);
    peak = Math.max(peak, run);
    maxDrawdown = Math.max(maxDrawdown, peak - run);
  }
  const byOutcome: TesterStats['byOutcome'] = {};
  const byDir: TesterStats['byDir'] = { long: { n: 0, net: 0, wins: 0 }, short: { n: 0, net: 0, wins: 0 } };
  for (const r of rs) {
    const o = (byOutcome[r.trade.outcome] ??= { n: 0, net: 0 });
    o.n += 1;
    o.net += r.net;
    const d = byDir[r.trade.dir];
    d.n += 1;
    d.net += r.net;
    if (r.net > 0) d.wins += 1;
  }
  const top3 = sum([...nets].sort((a, b) => b - a).slice(0, 3));
  const n = rs.length;
  return {
    trades: n,
    wins: winners.length,
    losses: losers.length,
    winRate: n ? winners.length / n : 0,
    gross: sum(rs.map((r) => r.gross)),
    cost: sum(rs.map((r) => r.cost)),
    net: sum(nets),
    avgNet: n ? sum(nets) / n : 0,
    medianNet: median(nets),
    avgR: n ? sum(rs.map((r) => r.netR)) / n : 0,
    medianR: median(rs.map((r) => r.netR)),
    profitFactor: !n ? 0 : lossSum > 0 ? sum(winners) / lossSum : Infinity,
    avgWin: winners.length ? sum(winners) / winners.length : 0,
    avgLoss: losers.length ? sum(losers) / losers.length : 0,
    best: n ? Math.max(...nets) : 0,
    worst: n ? Math.min(...nets) : 0,
    maxDrawdown,
    netWithoutTop3: sum(nets) - top3,
    equity,
    byOutcome,
    byDir,
  };
}

/** Trades of run A and run B matched by direction and fill candle: what changing a setting added and removed. */
export function tradeOverlap(a: TestTrade[], b: TestTrade[]) {
  const key = (t: TestTrade) => `${t.dir}:${t.entryIndex}`;
  const inA = new Set(a.map(key));
  const inB = new Set(b.map(key));
  return {
    both: a.filter((t) => inB.has(key(t))).length,
    onlyA: a.filter((t) => !inB.has(key(t))),
    onlyB: b.filter((t) => !inA.has(key(t))),
  };
}

/** Skipped setups grouped by step and reason, with the numbers taken out (times like 09:30 and names like 1h kept), most common first. */
export function groupSkips(skips: { step: string; reason: string }[]): { step: string; reason: string; n: number }[] {
  const m = new Map<string, { step: string; reason: string; n: number }>();
  for (const k of skips) {
    const reason = k.reason.replace(/(?<![\d:.])-?\d+(\.\d+)?(?![\d:A-Za-z])/g, '…');
    const key = `${k.step}|${reason}`;
    const e = m.get(key) ?? { step: k.step, reason, n: 0 };
    e.n += 1;
    m.set(key, e);
  }
  return [...m.values()].sort((a, b) => b.n - a.n);
}
