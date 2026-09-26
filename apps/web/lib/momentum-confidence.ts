import type { MomentumTrade } from './momentum-api';
import { direction } from './momentum-side';

export const MOMENTUM_CONFIDENCE_RUBRIC_VERSION = 'momentum-entry-v1';

export type ConfidenceFactorStatus = 'earned' | 'not_met' | 'not_recorded';
export type ConfidenceBand = 'strong' | 'moderate' | 'developing' | 'unavailable';

export interface ConfidenceFactor {
  id: string;
  label: string;
  maximum: number;
  earned: number;
  status: ConfidenceFactorStatus;
  detail: string;
}

export interface TradeConfidence {
  rubricVersion: typeof MOMENTUM_CONFIDENCE_RUBRIC_VERSION;
  score: number | null;
  band: ConfidenceBand;
  evidenceCoverage: number;
  factors: ConfidenceFactor[];
}

const hasOwn = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const finiteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);
const ratio = (numerator: number, denominator: number) =>
  denominator > 0 ? numerator / denominator : null;

function factor(
  id: string,
  label: string,
  maximum: number,
  earned: number | null,
  detail: string,
): ConfidenceFactor {
  if (earned === null) {
    return { id, label, maximum, earned: 0, status: 'not_recorded', detail };
  }
  return {
    id,
    label,
    maximum,
    earned: Math.min(maximum, Math.max(0, earned)),
    status: earned > 0 ? 'earned' : 'not_met',
    detail,
  };
}

function thresholdPoints(
  value: number,
  thresholds: readonly [number, number, number],
  points: readonly [number, number, number],
): number {
  if (value >= thresholds[2]) return points[2];
  if (value >= thresholds[1]) return points[1];
  if (value >= thresholds[0]) return points[0];
  return 0;
}

function confidenceBand(score: number | null): ConfidenceBand {
  if (score === null) return 'unavailable';
  if (score >= 75) return 'strong';
  if (score >= 50) return 'moderate';
  return 'developing';
}

/**
 * Scores only facts present at the trade decision. Realized performance and
 * post-entry context are intentionally excluded.
 */
export function calculateTradeConfidence(trade: MomentumTrade): TradeConfidence {
  const trend = trade.entry_evidence?.trend;
  const confirmation = trade.entry_evidence?.confirmation;
  const bestPatternStrength = trade.pattern_matches
    ?.map((match) => match.strength)
    .filter(finiteNumber)
    .reduce<number | undefined>(
      (best, strength) => (best === undefined || strength > best ? strength : best),
      undefined,
    );

  const trendAligned =
    trend && [trend.ema9, trend.ema20, trend.close, trend.vwap].every(finiteNumber);
  // Distances in the trade's favour: a short's stop is above, its target below.
  const dir = direction(trade);
  const riskPerShare =
    finiteNumber(trade.entry_price) && finiteNumber(trade.stop)
      ? dir * (trade.entry_price - trade.stop)
      : null;
  const riskReward =
    riskPerShare && riskPerShare > 0 && finiteNumber(trade.target)
      ? ratio(dir * (trade.target - trade.entry_price), riskPerShare)
      : null;

  const factors = [
    factor(
      'day-change',
      'Day-change momentum',
      10,
      finiteNumber(trade.day_chg_pct)
        ? thresholdPoints(trade.day_chg_pct, [1.5, 4, 8], [3, 7, 10])
        : null,
      finiteNumber(trade.day_chg_pct)
        ? `${trade.day_chg_pct.toFixed(2)}% at signal time`
        : 'Day change was not recorded for this trade',
    ),
    factor(
      'relative-volume',
      'Relative volume',
      15,
      finiteNumber(trade.rvol) ? thresholdPoints(trade.rvol, [1.5, 3, 5], [5, 10, 15]) : null,
      finiteNumber(trade.rvol)
        ? `${trade.rvol.toFixed(2)}× at signal time`
        : 'RVOL was not recorded for this trade',
    ),
    factor(
      'trend-alignment',
      '5-minute trend alignment',
      12,
      trendAligned ? (trend.ema9 > trend.ema20 && trend.close > trend.vwap ? 12 : 0) : null,
      trendAligned
        ? `EMA 9 ${trend.ema9 > trend.ema20 ? 'above' : 'not above'} EMA 20; close ${trend.close > trend.vwap ? 'above' : 'not above'} VWAP`
        : '5-minute EMA and VWAP evidence was not recorded',
    ),
    factor(
      'macd',
      '5-minute MACD momentum',
      8,
      finiteNumber(trade.macd_hist) ? (trade.macd_hist > 0 ? 8 : 0) : null,
      finiteNumber(trade.macd_hist)
        ? `MACD histogram ${trade.macd_hist > 0 ? 'positive' : 'not positive'}`
        : '5-minute MACD histogram was not recorded',
    ),
    factor(
      'confirmation-close',
      'Confirmation close quality',
      8,
      confirmation &&
        finiteNumber(confirmation.close_position) &&
        finiteNumber(confirmation.minimum_close_position)
        ? thresholdPoints(
            confirmation.close_position,
            [confirmation.minimum_close_position, 0.8, 0.9],
            [4, 6, 8],
          )
        : null,
      confirmation && finiteNumber(confirmation.close_position)
        ? `Closed in the ${(confirmation.close_position * 100).toFixed(0)}% of its range`
        : 'One-minute close-position evidence was not recorded',
    ),
    factor(
      'confirmation-volume',
      'Confirmation volume',
      12,
      confirmation &&
        finiteNumber(confirmation.volume_ratio) &&
        finiteNumber(confirmation.minimum_volume_ratio)
        ? (() => {
            const multiple = ratio(confirmation.volume_ratio, confirmation.minimum_volume_ratio);
            return multiple === null ? 0 : thresholdPoints(multiple, [1, 1.5, 2], [6, 9, 12]);
          })()
        : null,
      confirmation &&
        finiteNumber(confirmation.volume_ratio) &&
        finiteNumber(confirmation.minimum_volume_ratio)
        ? `${confirmation.volume_ratio.toFixed(2)}× versus ${confirmation.minimum_volume_ratio.toFixed(2)}× required`
        : 'One-minute confirmation volume was not recorded',
    ),
    factor(
      'pullback-ordinal',
      'Pullback timing',
      8,
      finiteNumber(trade.pullback_ord)
        ? trade.pullback_ord === 1
          ? 8
          : trade.pullback_ord === 2
            ? 6
            : trade.pullback_ord === 3
              ? 2
              : 0
        : null,
      finiteNumber(trade.pullback_ord)
        ? `Pullback ${trade.pullback_ord} of the session move`
        : 'Pullback ordinal was not recorded',
    ),
    factor(
      'pattern-strength',
      'Formation strength',
      6,
      bestPatternStrength === undefined
        ? trade.pattern_matches === undefined
          ? null
          : 0
        : thresholdPoints(bestPatternStrength, [0.75, 1, 1.5], [2, 4, 6]),
      bestPatternStrength === undefined
        ? trade.pattern_matches === undefined
          ? 'Pattern evidence was not recorded'
          : 'No strength-qualified formation was recorded'
        : `Strongest recorded formation was ${bestPatternStrength.toFixed(2)}× normal range`,
    ),
    factor(
      'risk-reward',
      'Planned reward-to-risk',
      6,
      riskReward === null ? null : thresholdPoints(riskReward, [1, 1.5, 2], [2, 4, 6]),
      riskReward === null
        ? 'A valid entry, stop, and target were not recorded'
        : `${riskReward.toFixed(2)}R planned reward-to-risk`,
    ),
    factor(
      'resistance-headroom',
      'Structural resistance headroom',
      10,
      hasOwn(trade, 'resist_head_pct')
        ? trade.resist_head_pct === null
          ? 10
          : finiteNumber(trade.resist_head_pct)
            ? thresholdPoints(trade.resist_head_pct, [1, 1.5, 2], [4, 7, 10])
            : null
        : null,
      !hasOwn(trade, 'resist_head_pct')
        ? 'Resistance-headroom evidence was not recorded'
        : trade.resist_head_pct === null
          ? 'No resistance was derived above the trigger'
          : finiteNumber(trade.resist_head_pct)
            ? `${trade.resist_head_pct.toFixed(2)}% to the nearest 5-minute resistance above the trigger`
            : 'Resistance-headroom evidence was not recorded',
    ),
    factor(
      'support-proximity',
      'Structural support proximity',
      5,
      hasOwn(trade, 'support_drop_pct')
        ? trade.support_drop_pct === null
          ? 0
          : finiteNumber(trade.support_drop_pct)
            ? trade.support_drop_pct <= 1
              ? 5
              : trade.support_drop_pct <= 2
                ? 3
                : 0
            : null
        : null,
      !hasOwn(trade, 'support_drop_pct')
        ? 'Support-location evidence was not recorded'
        : trade.support_drop_pct === null
          ? 'No support was derived below the trigger'
          : finiteNumber(trade.support_drop_pct)
            ? `${trade.support_drop_pct.toFixed(2)}% to the nearest 5-minute support below the trigger`
            : 'Support-location evidence was not recorded',
    ),
  ];

  const availableWeight = factors
    .filter((item) => item.status !== 'not_recorded')
    .reduce((sum, item) => sum + item.maximum, 0);
  const earned = factors.reduce((sum, item) => sum + item.earned, 0);
  const totalWeight = factors.reduce((sum, item) => sum + item.maximum, 0);
  const score = availableWeight === 0 ? null : Math.round((earned / availableWeight) * 100);

  return {
    rubricVersion: MOMENTUM_CONFIDENCE_RUBRIC_VERSION,
    score,
    band: confidenceBand(score),
    evidenceCoverage: Math.round((availableWeight / totalWeight) * 100),
    factors,
  };
}
