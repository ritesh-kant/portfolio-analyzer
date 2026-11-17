import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts';
import type { Bubble } from '../../lib/tv/bigtrades';

const BUY = '34, 171, 148';
const SELL = '239, 83, 80';
/** Radius (px) of the smallest and largest bubble; area ∝ quantity between them. */
const R_MIN = 6;
const R_MAX = 24;
/** MotiveWave draws at most 500 at once; so do we (the largest). */
const MAX_DRAWN = 500;

/** Index of the last bar that starts at or before `t` (unix s), or -1. */
export function barIndexAt(barTimes: number[], t: number): number {
  let lo = 0;
  let hi = barTimes.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (barTimes[mid]! <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

/** Circles for large trades: green when the buyer was the aggressor, red when the seller was. Size = quantity. */
export class BigTradesPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private bubbles: Bubble[] = [];
  private barTimes: number[] = [];
  private barSecs = 60;
  private view: IPrimitivePaneView = {
    zOrder: () => 'top',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) =>
          this.paint(context, mediaSize.width),
        );
      },
    }),
  };

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart as IChartApi;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews() {
    return [this.view];
  }

  /** `barTimes`: start of every chart candle (unix s), ascending; `barSecs`: the candle length. */
  setState(bubbles: Bubble[], barTimes: number[], barSecs: number) {
    this.bubbles = bubbles;
    this.barTimes = barTimes;
    this.barSecs = barSecs;
    this.requestUpdate?.();
  }

  private paint(ctx: CanvasRenderingContext2D, width: number) {
    if (!this.chart || !this.series || !this.bubbles.length || !this.barTimes.length) return;
    const ts = this.chart.timeScale();
    const last = this.barTimes[this.barTimes.length - 1]! + this.barSecs;
    const first = this.barTimes[0]!;
    let max = 0;
    for (const b of this.bubbles) max = Math.max(max, b.q);
    if (!(max > 0)) return;
    // largest first: they keep their spot, smaller ones are nudged sideways off them
    const drawn = [...this.bubbles].sort((a, b) => b.q - a.q).slice(0, MAX_DRAWN);
    const placed: Placed[] = [];
    for (const b of drawn) {
      const sec = b.t / 1000;
      if (sec < first || sec >= last) continue;
      const i = barIndexAt(this.barTimes, sec);
      const x0 = ts.logicalToCoordinate(i as Logical);
      const y = this.series.priceToCoordinate(b.p);
      if (x0 == null || y == null || x0 < -R_MAX || x0 > width + R_MAX) continue;
      const r = R_MIN + (R_MAX - R_MIN) * Math.sqrt(b.q / max);
      placed.push({ x: nudge(placed, x0, y, r), x0, y, r, q: b.q, rgb: b.side === 'buy' ? BUY : SELL });
    }
    // paint smallest last so nothing large covers a small one's label
    for (const p of placed) {
      if (p.x !== p.x0) {
        // tie a nudged bubble back to its candle
        ctx.beginPath();
        ctx.moveTo(p.x0, p.y);
        ctx.lineTo(p.x, p.y);
        ctx.lineWidth = 1;
        ctx.strokeStyle = `rgba(${p.rgb}, 0.9)`;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      // opaque: footprint cell numbers under the bubble must not bleed through its label
      ctx.fillStyle = '#131722';
      ctx.fill();
      ctx.fillStyle = `rgba(${p.rgb}, 0.8)`;
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = `rgb(${p.rgb})`;
      ctx.stroke();
      if (p.r >= 9) {
        ctx.font = `600 ${Math.round(Math.min(12, p.r * 0.8))}px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.lineJoin = 'round';
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.6)';
        ctx.strokeText(fmtQty(p.q), p.x, p.y + 0.5);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(fmtQty(p.q), p.x, p.y + 0.5);
      }
    }
  }
}

interface Placed {
  x: number;
  x0: number;
  y: number;
  r: number;
  q: number;
  rgb: string;
}

/**
 * x for a new bubble that overlaps none already placed: its candle's x if free, else the nearest
 * free spot to the right or left (a few tries; past that it stays put rather than wander off).
 */
export function nudge(placed: { x: number; y: number; r: number }[], x0: number, y: number, r: number): number {
  const free = (x: number) => placed.every((p) => Math.hypot(p.x - x, p.y - y) >= p.r + r + 1);
  if (free(x0)) return x0;
  const hits = placed.filter((p) => Math.hypot(p.x - x0, p.y - y) < p.r + r + 1);
  const cands: number[] = [];
  for (const p of hits) {
    const dy = p.y - y;
    const need = p.r + r + 1;
    const dx = Math.sqrt(Math.max(0, need * need - dy * dy)) + 0.5;
    cands.push(p.x + dx, p.x - dx);
  }
  cands.sort((a, b) => Math.abs(a - x0) - Math.abs(b - x0) || b - a);
  for (const x of cands.slice(0, 6)) if (free(x)) return x;
  return x0;
}

function fmtQty(q: number): string {
  return q >= 1e6 ? `${(q / 1e6).toFixed(1)}M` : q >= 1e4 ? `${Math.round(q / 1e3)}K` : q >= 1e3 ? `${(q / 1e3).toFixed(1)}K` : `${Math.round(q)}`;
}
