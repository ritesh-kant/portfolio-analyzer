import type { IChartApi, Logical } from 'lightweight-charts';

/**
 * x pixel of a (possibly fractional) logical candle index.
 *
 * `timeScale().logicalToCoordinate` only understands whole indices — any
 * fraction returns 0 — so interpolate between the two neighbouring candles.
 * Whole indices off-screen still give a valid (negative / beyond-width) x.
 */
export function logicalX(chart: IChartApi, logical: number): number | null {
  const ts = chart.timeScale();
  const i = Math.floor(logical);
  const a = ts.logicalToCoordinate(i as Logical);
  if (a == null) return null;
  const f = logical - i;
  if (f === 0) return a;
  const b = ts.logicalToCoordinate((i + 1) as Logical);
  return b == null ? a : a + f * (b - a);
}
