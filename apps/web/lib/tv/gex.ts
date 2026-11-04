/**
 * Gamma exposure (GEX) levels from an option chain: the Tanuki / MenthorQ style map.
 * A port of apps/signal-engine/src/options_trader/gex.py; keep the two in step (tv.test.mjs pins both
 * to the same numbers).
 *
 * GEX estimates how much option writers ("dealers") must buy or sell the index to stay hedged when it
 * moves 1%. Per strike: gamma × OI (units) × spot² × 1%, in ₹ per 1% move. The sign is an assumption
 * about who is short what (`GexConvention`); dealer books are not public, so every level is a zone.
 *
 * Gamma is recomputed with Black-Scholes from each row's IV: Upstox rounds gamma to 4 decimals, and the
 * flip sweep needs gamma at hypothetical spots anyway (IV held fixed per strike).
 */

export type GexConvention = 'dealer_long_calls' | 'dealer_short_all';

export interface OptionRow {
  /** Expiry instant, unix seconds (15:30 IST for NSE). */
  expiry: number;
  strike: number;
  kind: 'CE' | 'PE';
  /** Open interest in units of the underlying, not lots. */
  oi: number;
  /** Implied volatility as a decimal (0.1257, not 12.57). */
  iv: number;
  /** Risk-free rate for this market (absent = `RISK_FREE`, the Indian rate). */
  r?: number;
  /** Today's traded volume, in the same units as `oi` (absent = unknown). */
  volume?: number;
}

/** Chain-wide totals behind the put/call ratios and net delta. */
export interface GexActivity {
  /** Open interest and today's volume, in units of the underlying. */
  callOi: number;
  putOi: number;
  callVol: number;
  putVol: number;
  /**
   * Net delta exposure (DEX), money: Σ delta × OI × spot over every option, as their buyers hold them
   * (calls +, puts −). Negative = the open options are net short the underlying; whoever wrote them holds
   * the opposite and hedges it. Independent of the dealer-position setting.
   */
  netDelta: number;
}

export interface StrikeGex {
  strike: number;
  /** Signed ₹ per 1% move. */
  call: number;
  put: number;
  net: number;
}

export interface GexLevels {
  spot: number;
  /** ₹ per 1% move summed over every row. */
  netGex: number;
  /** Gamma flip ("HVL"); null when the total never changes sign within ±8%. */
  flip: number | null;
  /** C1, C2, … strongest call-gamma strikes at/above spot. */
  callWalls: number[];
  /** P1, P2, … strongest put-gamma strikes at/below spot. */
  putWalls: number[];
  /** One-day range from the nearest expiry's ATM IV: [1D min, 1D max]. */
  range: [number, number] | null;
  /** Strike with the largest |net| GEX, either side of spot (MenthorQ's "Gamma Wall"). */
  gammaWall?: number | null;
  /** MenthorQ's GEX 1…10: the next-largest |net| strikes after Call Resistance and Put Support. */
  gexLevels?: number[];
  /** Put/call totals and net delta (absent in responses from before it existed). */
  activity?: GexActivity;
  strikes: StrikeGex[];
}

export const RISK_FREE = 0.065;
const YEAR_SECS = 365 * 24 * 3600;
/** 30 minutes: stops expiry-day ATM gamma going to infinity. */
const MIN_T_YEARS = 1800 / YEAR_SECS;
const TRADING_DAYS = 252;

const normPdf = (x: number) => Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);

/** Standard normal CDF (Abramowitz–Stegun 26.2.17, error < 7.5e-8). */
export function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const poly =
    t *
    (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  const tail = normPdf(x) * poly;
  return x >= 0 ? 1 - tail : tail;
}

/** Black-Scholes price (no dividends). */
export function bsPrice(
  spot: number,
  strike: number,
  t: number,
  iv: number,
  kind: OptionRow['kind'],
  r = RISK_FREE,
): number {
  if (t <= 0 || iv <= 0) return Math.max(0, kind === 'CE' ? spot - strike : strike - spot);
  const volT = iv * Math.sqrt(t);
  const d1 = (Math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / volT;
  const d2 = d1 - volT;
  const disc = Math.exp(-r * t);
  return kind === 'CE'
    ? spot * normCdf(d1) - strike * disc * normCdf(d2)
    : strike * disc * normCdf(-d2) - spot * normCdf(-d1);
}

/**
 * Volatility that makes Black-Scholes return `price` (bisection over 1%–500%); null when no volatility can
 * produce it (below intrinsic value, or above the no-arbitrage bound).
 */
export function impliedVol(
  price: number,
  spot: number,
  strike: number,
  t: number,
  kind: OptionRow['kind'],
  r = RISK_FREE,
): number | null {
  if (!(price > 0) || t <= 0) return null;
  let lo = 0.01;
  let hi = 5;
  if (
    price < bsPrice(spot, strike, t, lo, kind, r) ||
    price > bsPrice(spot, strike, t, hi, kind, r)
  ) {
    return null;
  }
  for (let i = 0; i < 60; i += 1) {
    const mid = (lo + hi) / 2;
    if (bsPrice(spot, strike, t, mid, kind, r) < price) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

export const yearFraction = (now: number, expiry: number) =>
  Math.max((expiry - now) / YEAR_SECS, MIN_T_YEARS);

/** Black-Scholes gamma (change in delta per 1-point move); the same for calls and puts. */
export function bsGamma(
  spot: number,
  strike: number,
  t: number,
  iv: number,
  r = RISK_FREE,
): number {
  if (spot <= 0 || strike <= 0 || t <= 0 || iv <= 0) return 0;
  const volT = iv * Math.sqrt(t);
  const d1 = (Math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / volT;
  return normPdf(d1) / (spot * volT);
}

/** Black-Scholes delta: 0…1 for calls, −1…0 for puts (no dividends). */
export function bsDelta(
  spot: number,
  strike: number,
  t: number,
  iv: number,
  kind: OptionRow['kind'],
  r = RISK_FREE,
): number {
  if (spot <= 0 || strike <= 0 || t <= 0 || iv <= 0) return 0;
  const volT = iv * Math.sqrt(t);
  const d1 = (Math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / volT;
  return kind === 'CE' ? normCdf(d1) : normCdf(d1) - 1;
}

/** Put/call open interest and volume over live rows, and net delta over the rows with an IV. */
export function chainActivity(rows: OptionRow[], spot: number, now: number): GexActivity {
  const a: GexActivity = { callOi: 0, putOi: 0, callVol: 0, putVol: 0, netDelta: 0 };
  for (const r of rows) {
    if (r.expiry <= now) continue;
    const vol = r.volume ?? 0;
    if (r.kind === 'CE') {
      a.callOi += r.oi;
      a.callVol += vol;
    } else {
      a.putOi += r.oi;
      a.putVol += vol;
    }
    if (r.oi > 0 && r.iv > 0) {
      const d = bsDelta(
        spot,
        r.strike,
        yearFraction(now, r.expiry),
        r.iv,
        r.kind,
        r.r ?? RISK_FREE,
      );
      a.netDelta += d * r.oi * spot;
    }
  }
  return a;
}

const sign = (kind: OptionRow['kind'], convention: GexConvention) =>
  convention === 'dealer_short_all' ? -1 : kind === 'CE' ? 1 : -1;

export function rowGex(
  row: OptionRow,
  spot: number,
  now: number,
  convention: GexConvention,
): number {
  const g = bsGamma(spot, row.strike, yearFraction(now, row.expiry), row.iv, row.r ?? RISK_FREE);
  return sign(row.kind, convention) * g * row.oi * spot * spot * 0.01;
}

export function netGex(
  rows: OptionRow[],
  spot: number,
  now: number,
  convention: GexConvention,
): number {
  let sum = 0;
  for (const r of rows) sum += rowGex(r, spot, now, convention);
  return sum;
}

export function strikeGex(
  rows: OptionRow[],
  spot: number,
  now: number,
  convention: GexConvention,
): StrikeGex[] {
  const by = new Map<number, StrikeGex>();
  for (const r of rows) {
    let s = by.get(r.strike);
    if (!s) by.set(r.strike, (s = { strike: r.strike, call: 0, put: 0, net: 0 }));
    const g = rowGex(r, spot, now, convention);
    if (r.kind === 'CE') s.call += g;
    else s.put += g;
    s.net = s.call + s.put;
  }
  return [...by.values()].sort((a, b) => a.strike - b.strike);
}

/** Spot where total GEX changes sign, nearest the current spot (sweep ±`span`, interpolate the bracket). */
export function gammaFlip(
  rows: OptionRow[],
  spot: number,
  now: number,
  convention: GexConvention,
  span = 0.08,
  steps = 161,
): number | null {
  const lo = spot * (1 - span);
  const hi = spot * (1 + span);
  const grid = Array.from({ length: steps }, (_, i) => lo + ((hi - lo) * i) / (steps - 1));
  const totals = grid.map((s) => netGex(rows, s, now, convention));
  const crossings: number[] = [];
  for (let i = 0; i < steps - 1; i += 1) {
    const s0 = grid[i]!;
    const s1 = grid[i + 1]!;
    const g0 = totals[i]!;
    const g1 = totals[i + 1]!;
    if (g0 === 0) crossings.push(s0);
    else if (g0 * g1 < 0) crossings.push(s0 + ((s1 - s0) * -g0) / (g1 - g0));
  }
  if (!crossings.length) return null;
  return crossings.reduce((best, c) => (Math.abs(c - spot) < Math.abs(best - spot) ? c : best));
}

/**
 * Mean call/put IV at the strike nearest spot, on the nearest expiry at least a day away (else the
 * nearest). A same-day (0DTE) option prices the overnight gap and the open into a few hours, so its
 * annualised IV runs about double and would inflate a one-day range.
 */
export function atmIv(rows: OptionRow[], spot: number, now = 0): number | null {
  if (!rows.length) return null;
  const expiries = rows.map((r) => r.expiry);
  const dayOut = expiries.filter((e) => e - now >= 86_400);
  const nearest = Math.min(...(dayOut.length ? dayOut : expiries));
  const front = rows.filter((r) => r.expiry === nearest && r.iv > 0);
  if (!front.length) return null;
  const k = front.reduce((best, r) =>
    Math.abs(r.strike - spot) < Math.abs(best.strike - spot) ? r : best,
  ).strike;
  const ivs = front.filter((r) => r.strike === k).map((r) => r.iv);
  return ivs.reduce((a, b) => a + b, 0) / ivs.length;
}

export function expectedMove(spot: number, iv: number | null, days = 1): [number, number] | null {
  if (!iv || iv <= 0) return null;
  const move = spot * iv * Math.sqrt(days / TRADING_DAYS);
  return [spot - move, spot + move];
}

export function computeLevels(
  rows: OptionRow[],
  spot: number,
  now: number,
  convention: GexConvention = 'dealer_long_calls',
  nWalls = 3,
  nGex = 10,
): GexLevels {
  const live = rows.filter((r) => r.oi > 0 && r.iv > 0 && r.expiry > now);
  const strikes = strikeGex(live, spot, now, convention);
  // Walls on their own side of price (Tanuki style): resistance above, support below.
  const callWalls = strikes
    .filter((s) => s.strike >= spot && s.call !== 0)
    .sort((a, b) => Math.abs(b.call) - Math.abs(a.call))
    .slice(0, nWalls)
    .map((s) => s.strike);
  const putWalls = strikes
    .filter((s) => s.strike <= spot && s.put !== 0)
    .sort((a, b) => Math.abs(b.put) - Math.abs(a.put))
    .slice(0, nWalls)
    .map((s) => s.strike);
  const byNet = strikes
    .filter((s) => s.net !== 0)
    .sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
  const crPs = new Set([callWalls[0], putWalls[0]]);
  return {
    spot,
    netGex: strikes.reduce((a, s) => a + s.net, 0),
    gammaWall: byNet[0]?.strike ?? null,
    gexLevels: byNet
      .filter((s) => !crPs.has(s.strike))
      .slice(0, nGex)
      .map((s) => s.strike),
    flip: convention === 'dealer_long_calls' ? gammaFlip(live, spot, now, convention) : null,
    callWalls,
    putWalls,
    range: expectedMove(spot, atmIv(live, spot, now)),
    activity: chainActivity(rows, spot, now),
    strikes,
  };
}

/** What `/api/chart/gex` returns. */
export interface GexResponse {
  supported: boolean;
  reason?: string;
  /** Upstox (NSE) or Yahoo (US, ~15 min delayed). */
  source?: 'upstox' | 'yahoo';
  /** GEX money unit: ₹ for NSE, $ for US. */
  currency?: 'INR' | 'USD';
  /** Set when the levels come from another instrument's options (ES=F ← ^SPX, GC=F ← GLD). */
  proxy?: string;
  /** US: where each contract's IV came from (bid/ask mid, last trade, Yahoo's own figure). */
  ivSources?: { mid: number; last: number; yahoo: number };
  symbol?: string;
  /** Snapshot time, unix seconds. */
  asOf?: number;
  spot?: number;
  /** Futures charts only: the future's last price, and futures ÷ spot to move strike levels onto it. */
  futurePrice?: number;
  scale?: number;
  convention?: GexConvention;
  /** Expiries included (YYYY-MM-DD), nearest first. */
  expiries?: string[];
  /** Every expiry within the window. */
  all?: GexLevels;
  /** The nearest expiry only (MenthorQ's "0DTE" set). */
  nearest?: GexLevels;
}

// ── chart levels: what each line is, its MenthorQ name, and its default look ──

export type GexLineStyle = 'solid' | 'dashed' | 'dotted';

export interface GexLineDef {
  key: string;
  /** Settings name (MenthorQ's). */
  name: string;
  /** Label on the chart. */
  title: string;
  color: string;
  style: GexLineStyle;
  on: boolean;
  /** The price of this level in a response (null = not available). */
  price: (all: GexLevels, nearest: GexLevels | undefined) => number | null | undefined;
}

const CR = '#f4511e';
const PS = '#43a047';
const HVL = '#448aff';
const RANGE = '#ff9800';
const GEX_N = '#26c6da';

/** Every level the indicator can draw, in MenthorQ's order. Defaults match its "Gamma Levels" study. */
export const GEX_LINES: GexLineDef[] = [
  {
    key: 'cr',
    name: 'Call Resistance',
    title: 'CR',
    color: CR,
    style: 'solid',
    on: true,
    price: (a) => a.callWalls[0],
  },
  {
    key: 'ps',
    name: 'Put Support',
    title: 'PS',
    color: PS,
    style: 'solid',
    on: true,
    price: (a) => a.putWalls[0],
  },
  {
    key: 'c2',
    name: 'Call Wall 2',
    title: 'C2',
    color: CR,
    style: 'solid',
    on: true,
    price: (a) => a.callWalls[1],
  },
  {
    key: 'c3',
    name: 'Call Wall 3',
    title: 'C3',
    color: CR,
    style: 'solid',
    on: true,
    price: (a) => a.callWalls[2],
  },
  {
    key: 'p2',
    name: 'Put Wall 2',
    title: 'P2',
    color: PS,
    style: 'solid',
    on: true,
    price: (a) => a.putWalls[1],
  },
  {
    key: 'p3',
    name: 'Put Wall 3',
    title: 'P3',
    color: PS,
    style: 'solid',
    on: true,
    price: (a) => a.putWalls[2],
  },
  {
    key: 'hvl',
    name: 'HVL',
    title: 'HVL',
    color: HVL,
    style: 'solid',
    on: true,
    price: (a) => a.flip,
  },
  {
    key: 'min1d',
    name: '1D Min',
    title: '1D Min',
    color: RANGE,
    style: 'dotted',
    on: true,
    price: (a) => a.range?.[0],
  },
  {
    key: 'max1d',
    name: '1D Max',
    title: '1D Max',
    color: RANGE,
    style: 'dotted',
    on: true,
    price: (a) => a.range?.[1],
  },
  {
    key: 'cr0',
    name: 'Call Resistance 0DTE',
    title: 'CR 0DTE',
    color: CR,
    style: 'dashed',
    on: true,
    price: (_, n) => n?.callWalls[0],
  },
  {
    key: 'ps0',
    name: 'Put Support 0DTE',
    title: 'PS 0DTE',
    color: PS,
    style: 'dashed',
    on: true,
    price: (_, n) => n?.putWalls[0],
  },
  {
    key: 'hvl0',
    name: 'HVL 0DTE',
    title: 'HVL 0DTE',
    color: HVL,
    style: 'dashed',
    on: true,
    price: (_, n) => n?.flip,
  },
  {
    key: 'gw0',
    name: 'Gamma Wall 0DTE',
    title: 'GW 0DTE',
    color: CR,
    style: 'dashed',
    on: true,
    price: (_, n) => n?.gammaWall,
  },
  ...Array.from(
    { length: 10 },
    (_, i): GexLineDef => ({
      key: `gex${i + 1}`,
      name: `GEX ${i + 1}`,
      title: `G${i + 1}`,
      color: GEX_N,
      style: 'solid',
      on: i < 3,
      price: (a) => a.gexLevels?.[i],
    }),
  ),
];

/** Per-level overrides kept on the indicator (absent = the default above). */
export type GexStyle = Record<string, { on?: boolean; color?: string }>;

export const gexLineOn = (def: GexLineDef, style: GexStyle | undefined) =>
  style?.[def.key]?.on ?? def.on;
export const gexLineColor = (def: GexLineDef, style: GexStyle | undefined) =>
  style?.[def.key]?.color ?? def.color;

/** GEX money per 1% move, short. ₹: "+268 cr" / "−1.2k cr" / "+45 L"; $: "+$1.2B" / "−$350M" / "+$40K". */
export function fmtGex(v: number, currency: 'INR' | 'USD' = 'INR'): string {
  const sign = v >= 0 ? '+' : '−';
  const a = Math.abs(v);
  if (currency === 'USD') {
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(1)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(0)}M`;
    return `${sign}$${(a / 1e3).toFixed(0)}K`;
  }
  const cr = a / 1e7;
  if (cr >= 1000) return `${sign}${(cr / 1000).toFixed(1)}k cr`;
  if (cr >= 1) return `${sign}${cr.toFixed(0)} cr`;
  return `${sign}${(a / 1e5).toFixed(0)} L`;
}

// ── US chains (Yahoo) ──────────────────────────────────────────────────────

/** US equity and index options: 100 shares (or index units) per contract. */
export const US_MULTIPLIER = 100;
/** Assumed US risk-free rate for the Black-Scholes inputs. */
export const US_RATE = 0.04;

/** One contract as Yahoo's v7 options endpoint returns it (fields we use). */
export interface YahooContract {
  strike: number;
  openInterest?: number;
  impliedVolatility?: number;
  bid?: number;
  ask?: number;
  lastPrice?: number;
  volume?: number;
}

/** UTC offset of New York (hours, e.g. −4 in summer, −5 in winter) on a given instant. */
export function nyOffsetHours(unix: number): number {
  const name = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    timeZoneName: 'shortOffset',
  })
    .formatToParts(new Date(unix * 1000))
    .find((p) => p.type === 'timeZoneName')?.value;
  const m = /GMT([+-]\d+)/.exec(name ?? '');
  return m ? Number(m[1]) : -5;
}

/** Yahoo's `expirationDate` (00:00 UTC of the day) → 16:00 New York that day, unix seconds. */
export function usExpiryInstant(expirationDate: number): number {
  const noonUtc = expirationDate + 12 * 3600;
  return expirationDate + (16 - nyOffsetHours(noonUtc)) * 3600;
}

/**
 * The price the options are actually trading off, from put–call parity: S = C − P + K·e^(−rT), using the
 * bid/ask mids of the `n` strikes nearest `quoteSpot` that have both a call and a put quote (median of
 * them). Yahoo's quote can lag the options (before the US open it is still yesterday's close while the
 * options already price pre-market), and solving IVs against a stale spot inflates every one of them.
 * Null when too few strikes have two-sided quotes.
 */
export function paritySpot(
  calls: YahooContract[],
  puts: YahooContract[],
  expiry: number,
  quoteSpot: number,
  now: number,
  n = 5,
): number | null {
  const mid = (c: YahooContract) =>
    (c.bid ?? 0) > 0 && (c.ask ?? 0) >= (c.bid ?? 0) ? ((c.bid ?? 0) + (c.ask ?? 0)) / 2 : null;
  const putMid = new Map<number, number>();
  for (const p of puts) {
    const m = mid(p);
    if (m != null) putMid.set(p.strike, m);
  }
  const disc = Math.exp(-US_RATE * yearFraction(now, expiry));
  const implied: { k: number; s: number }[] = [];
  for (const c of calls) {
    const cm = mid(c);
    const pm = putMid.get(c.strike);
    if (cm != null && pm != null) implied.push({ k: c.strike, s: cm - pm + c.strike * disc });
  }
  if (implied.length < 3) return null;
  const near = implied
    .sort((a, b) => Math.abs(a.k - quoteSpot) - Math.abs(b.k - quoteSpot))
    .slice(0, n)
    .map((x) => x.s)
    .sort((a, b) => a - b);
  return near[Math.floor(near.length / 2)]!;
}

/**
 * Yahoo contracts → rows. Open interest is in contracts, so × 100 to units. IV is solved from the bid/ask
 * mid (Yahoo's own IV is unreliable on thin strikes), else from the last trade, else Yahoo's figure.
 */
export function usRows(
  calls: YahooContract[],
  puts: YahooContract[],
  expiry: number,
  spot: number,
  now: number,
  tally: { mid: number; last: number; yahoo: number },
): OptionRow[] {
  const t = yearFraction(now, expiry);
  const rows: OptionRow[] = [];
  for (const [list, kind] of [
    [calls, 'CE'],
    [puts, 'PE'],
  ] as const) {
    for (const c of list) {
      const oi = (c.openInterest ?? 0) * US_MULTIPLIER;
      const volume = (c.volume ?? 0) * US_MULTIPLIER;
      if (!(c.strike > 0) || (!(oi > 0) && !(volume > 0))) continue;
      // No open interest yet (a strike first traded today): counts toward volume only.
      if (!(oi > 0)) {
        rows.push({ expiry, strike: c.strike, kind, oi: 0, iv: 0, r: US_RATE, volume });
        continue;
      }
      const bid = c.bid ?? 0;
      const ask = c.ask ?? 0;
      let iv: number | null = null;
      if (bid > 0 && ask >= bid) {
        iv = impliedVol((bid + ask) / 2, spot, c.strike, t, kind, US_RATE);
        if (iv != null) tally.mid += 1;
      }
      if (iv == null && (c.lastPrice ?? 0) > 0) {
        iv = impliedVol(c.lastPrice!, spot, c.strike, t, kind, US_RATE);
        if (iv != null) tally.last += 1;
      }
      if (iv == null && (c.impliedVolatility ?? 0) > 0.01 && c.impliedVolatility! < 5) {
        iv = c.impliedVolatility!;
        tally.yahoo += 1;
      }
      // No usable IV: still counts toward the put/call totals (iv 0 keeps it out of GEX and delta).
      rows.push({ expiry, strike: c.strike, kind, oi, iv: iv ?? 0, r: US_RATE, volume });
    }
  }
  return rows;
}
