import type { Unit } from './fields.ts';
import type { RangeFilter } from './types.ts';

const SUFFIX: Record<string, number> = { k: 1e3, m: 1e6, b: 1e9, t: 1e12 };

/** "15M" → 15_000_000, "1.5k" → 1500, "$2" → 2, "10%" → 10. Returns null for anything that is not a number. */
export function parseNumber(input: string): number | null {
  const s = input
    .trim()
    .replace(/[$,%\s]/g, '')
    .toLowerCase();
  if (!s) return null;
  const m = /^(-?\d*\.?\d+)([kmbt])?$/.exec(s);
  if (!m) return null;
  const n = Number(m[1]) * (m[2] ? SUFFIX[m[2]]! : 1);
  return Number.isFinite(n) ? n : null;
}

/** 12_880_000 → "12.88M", 3600 → "3.6K". Below 1000 the number is shown whole. */
export function compact(n: number, digits = 2): string {
  const a = Math.abs(n);
  const trim = (x: number) => String(Number(x.toFixed(digits)));
  if (a >= 1e12) return `${trim(n / 1e12)}T`;
  if (a >= 1e9) return `${trim(n / 1e9)}B`;
  if (a >= 1e6) return `${trim(n / 1e6)}M`;
  if (a >= 1e3) return `${trim(n / 1e3)}K`;
  return trim(n);
}

const fixed = (n: number, d: number) =>
  n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

/** Cell text for a numeric value. The currency word (USD) is added by the table, in a smaller face. */
export function formatValue(unit: Unit, v: number | null | undefined, signed = false): string {
  if (v == null || !Number.isFinite(v)) return '—';
  switch (unit) {
    case 'usd':
      return fixed(v, Math.abs(v) < 1 && v !== 0 ? 4 : 2);
    case 'pct':
      return `${signed && v > 0 ? '+' : ''}${fixed(v, 2)}%`;
    case 'x':
      return fixed(v, 2);
    case 'shares':
    case 'usdbig':
      return compact(v);
    case 'days':
      return String(Math.round(v));
    default:
      return fixed(v, 2);
  }
}

/** The pill text for an active range: `2 to 20 USD`, `> 10%`, `< 5M`. */
export function rangeLabel(unit: Unit, r: RangeFilter, currency = 'USD'): string {
  const show = (n: number) => {
    const t = unit === 'shares' || unit === 'usdbig' ? compact(n) : String(Number(n.toFixed(4)));
    return unit === 'pct' ? `${t}%` : t;
  };
  const tail = unit === 'usd' || unit === 'usdbig' ? ` ${currency}` : '';
  if (r.min != null && r.max != null) return `${show(r.min)} to ${show(r.max)}${tail}`;
  if (r.min != null) return `> ${show(r.min)}${tail}`;
  if (r.max != null) return `< ${show(r.max)}${tail}`;
  return 'Any';
}
