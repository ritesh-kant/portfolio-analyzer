import type { Bar } from './types';

export type Series = (number | null)[];

export function ema(values: number[], length: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (length < 1 || values.length < length) return out;
  const k = 2 / (length + 1);
  let sum = 0;
  for (let i = 0; i < length; i += 1) sum += values[i]!;
  let prev = sum / length;
  out[length - 1] = prev;
  for (let i = length; i < values.length; i += 1) {
    prev = values[i]! * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

export function sma(values: number[], length: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (length < 1) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i += 1) {
    sum += values[i]!;
    if (i >= length) sum -= values[i - length]!;
    if (i >= length - 1) out[i] = sum / length;
  }
  return out;
}

/** Wilder's RSI, as TradingView draws it. */
export function rsi(values: number[], length: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (values.length <= length) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= length; i += 1) {
    const change = values[i]! - values[i - 1]!;
    if (change >= 0) gain += change;
    else loss -= change;
  }
  gain /= length;
  loss /= length;
  const toRsi = () => (loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
  out[length] = toRsi();
  for (let i = length + 1; i < values.length; i += 1) {
    const change = values[i]! - values[i - 1]!;
    gain = (gain * (length - 1) + Math.max(change, 0)) / length;
    loss = (loss * (length - 1) + Math.max(-change, 0)) / length;
    out[i] = toRsi();
  }
  return out;
}

export interface MacdResult {
  macd: Series;
  signal: Series;
  hist: Series;
}

export function macd(values: number[], fast = 12, slow = 26, signalLen = 9): MacdResult {
  const fastE = ema(values, fast);
  const slowE = ema(values, slow);
  const line: Series = values.map((_, i) =>
    fastE[i] != null && slowE[i] != null ? fastE[i]! - slowE[i]! : null,
  );
  const firstIdx = line.findIndex((v) => v != null);
  const signal: Series = new Array(values.length).fill(null);
  if (firstIdx >= 0) {
    const tail = line.slice(firstIdx) as number[];
    const sig = ema(tail, signalLen);
    sig.forEach((v, i) => {
      signal[firstIdx + i] = v;
    });
  }
  const hist: Series = line.map((v, i) => (v != null && signal[i] != null ? v - signal[i]! : null));
  return { macd: line, signal, hist };
}

/** Session VWAP: resets each exchange day, typical-price weighted. */
export function vwap(bars: Bar[], dayOf: (time: number) => string): Series {
  const out: Series = [];
  let day = '';
  let pv = 0;
  let vol = 0;
  for (const bar of bars) {
    const d = dayOf(bar.time);
    if (d !== day) {
      day = d;
      pv = 0;
      vol = 0;
    }
    const typical = (bar.high + bar.low + bar.close) / 3;
    pv += typical * bar.volume;
    vol += bar.volume;
    out.push(vol > 0 ? pv / vol : null);
  }
  return out;
}

export interface BollingerResult {
  basis: Series;
  upper: Series;
  lower: Series;
}

export function bollinger(values: number[], length = 20, mult = 2): BollingerResult {
  const basis = sma(values, length);
  const upper: Series = new Array(values.length).fill(null);
  const lower: Series = new Array(values.length).fill(null);
  for (let i = length - 1; i < values.length; i += 1) {
    const mean = basis[i]!;
    let variance = 0;
    for (let j = i - length + 1; j <= i; j += 1) variance += (values[j]! - mean) ** 2;
    const sd = Math.sqrt(variance / length);
    upper[i] = mean + mult * sd;
    lower[i] = mean - mult * sd;
  }
  return { basis, upper, lower };
}

/** Relative volume: this candle's volume over the mean of the previous `length` candles. */
export function relativeVolume(volumes: number[], length = 20): Series {
  const out: Series = new Array(volumes.length).fill(null);
  let sum = 0;
  for (let i = 0; i < volumes.length; i += 1) {
    if (i >= length) {
      const avg = sum / length;
      out[i] = avg > 0 ? volumes[i]! / avg : null;
      sum -= volumes[i - length]!;
    }
    sum += volumes[i]!;
  }
  return out;
}

/** Average True Range, Wilder-smoothed (RMA) as TradingView's default ATR. */
export function atr(bars: Bar[], length = 14): Series {
  const out: Series = new Array(bars.length).fill(null);
  if (length < 1 || bars.length < length) return out;
  const tr = bars.map((b, i) =>
    i === 0 ? b.high - b.low : Math.max(b.high - b.low, Math.abs(b.high - bars[i - 1]!.close), Math.abs(b.low - bars[i - 1]!.close)),
  );
  let prev = 0;
  for (let i = 0; i < length; i += 1) prev += tr[i]!;
  prev /= length;
  out[length - 1] = prev;
  for (let i = length; i < bars.length; i += 1) {
    prev = (prev * (length - 1) + tr[i]!) / length;
    out[i] = prev;
  }
  return out;
}
