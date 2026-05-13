import {
  RSI,
  MACD,
  BollingerBands,
  EMA,
} from 'technicalindicators';
import type { TechnicalIndicator, TechnicalsResponse } from './types.js';

function avg(arr: number[]): number {
  if (arr.length === 0) return 0;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function last<T>(arr: T[]): T | undefined {
  return arr[arr.length - 1];
}

export function computeTechnicals(
  symbol: string,
  closes: number[],
  volumes: number[],
  highs: number[],
  lows: number[],
): Omit<TechnicalsResponse, 'error'> {
  const currentPrice = last(closes) ?? 0;

  // RSI
  const rsiValues = RSI.calculate({ period: 14, values: closes });
  const rsiVal = last(rsiValues) ?? null;
  const rsi: TechnicalIndicator = {
    value: rsiVal !== null ? parseFloat(rsiVal.toFixed(2)) : null,
    interpretation:
      rsiVal === null
        ? 'Insufficient data'
        : rsiVal > 70
          ? `RSI at ${rsiVal.toFixed(0)} — overbought zone`
          : rsiVal < 30
            ? `RSI at ${rsiVal.toFixed(0)} — oversold zone`
            : `RSI at ${rsiVal.toFixed(0)} — neutral zone`,
  };

  // MACD
  const macdValues = MACD.calculate({
    fastPeriod: 12,
    slowPeriod: 26,
    signalPeriod: 9,
    values: closes,
    SimpleMAOscillator: false,
    SimpleMASignal: false,
  });
  const macdLast = last(macdValues);
  const macdHistogram = macdLast?.histogram ?? null;
  const macd: TechnicalIndicator = {
    value: macdHistogram !== null ? parseFloat(macdHistogram.toFixed(4)) : null,
    interpretation:
      macdHistogram === null
        ? 'Insufficient data'
        : macdHistogram > 0
          ? `MACD histogram ${macdHistogram.toFixed(2)} — bullish momentum`
          : `MACD histogram ${macdHistogram.toFixed(2)} — bearish momentum`,
  };

  // Bollinger Bands
  const bbValues = BollingerBands.calculate({ period: 20, stdDev: 2, values: closes });
  const bbLast = last(bbValues);
  let bbPosition: number | null = null;
  let bbInterpretation = 'Insufficient data';
  if (bbLast && currentPrice) {
    const range = bbLast.upper - bbLast.lower;
    bbPosition = range > 0 ? parseFloat(((currentPrice - bbLast.lower) / range).toFixed(4)) : 0.5;
    if (currentPrice >= bbLast.upper) bbInterpretation = `Price at upper BB (${bbLast.upper.toFixed(2)}) — near resistance`;
    else if (currentPrice <= bbLast.lower) bbInterpretation = `Price at lower BB (${bbLast.lower.toFixed(2)}) — near support`;
    else bbInterpretation = `Price inside BB bands — ${(bbPosition * 100).toFixed(0)}% from lower band`;
  }
  const bollingerBands: TechnicalIndicator = { value: bbPosition, interpretation: bbInterpretation };

  // EMA 20
  const ema20Values = EMA.calculate({ period: 20, values: closes });
  const ema20Val = last(ema20Values) ?? null;
  const ema20: TechnicalIndicator = {
    value: ema20Val !== null ? parseFloat(ema20Val.toFixed(2)) : null,
    interpretation:
      ema20Val === null
        ? 'Insufficient data'
        : currentPrice > ema20Val
          ? `Price above EMA20 (${ema20Val.toFixed(2)}) — short-term bullish`
          : `Price below EMA20 (${ema20Val.toFixed(2)}) — short-term bearish`,
  };

  // EMA 50
  const ema50Values = EMA.calculate({ period: 50, values: closes });
  const ema50Val = last(ema50Values) ?? null;
  const ema50: TechnicalIndicator = {
    value: ema50Val !== null ? parseFloat(ema50Val.toFixed(2)) : null,
    interpretation:
      ema50Val === null
        ? 'Insufficient data'
        : currentPrice > ema50Val
          ? `Price above EMA50 (${ema50Val.toFixed(2)}) — medium-term bullish`
          : `Price below EMA50 (${ema50Val.toFixed(2)}) — medium-term bearish`,
  };

  // Volume ratio (current vs 20-day avg)
  const recentVolumes = volumes.slice(-21, -1);
  const avgVol = avg(recentVolumes);
  const currentVol = last(volumes) ?? 0;
  const volRatio = avgVol > 0 ? parseFloat((currentVol / avgVol).toFixed(2)) : null;
  const volumeRatio: TechnicalIndicator = {
    value: volRatio,
    interpretation:
      volRatio === null
        ? 'Insufficient data'
        : volRatio > 1.5
          ? `Volume ${volRatio}x average — high activity`
          : volRatio < 0.5
            ? `Volume ${volRatio}x average — low activity`
            : `Volume ${volRatio}x average — normal`,
  };

  return { symbol, currentPrice, rsi, macd, bollingerBands, ema20, ema50, volumeRatio };
}
