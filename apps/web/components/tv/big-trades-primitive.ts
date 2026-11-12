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
    // largest first, so the small ones sit on top of them
    const drawn = [...this.bubbles].sort((a, b) => b.q - a.q).slice(0, MAX_DRAWN);
    for (const b of drawn) {
      const sec = b.t / 1000;
      if (sec < first || sec >= last) continue;
      const i = barIndexAt(this.barTimes, sec);
      const x = ts.logicalToCoordinate(i as Logical);
      const y = this.series.priceToCoordinate(b.p);
      if (x == null || y == null || x < -R_MAX || x > width + R_MAX) continue;
      const r = R_MIN + (R_MAX - R_MIN) * Math.sqrt(b.q / max);
      const rgb = b.side === 'buy' ? BUY : SELL;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${rgb}, 0.55)`;
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = `rgba(${rgb}, 0.95)`;
      ctx.stroke();
      if (r >= 9) {
        ctx.fillStyle = '#ffffff';
        ctx.font = `${Math.round(Math.min(12, r * 0.8))}px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(fmtQty(b.q), x, y + 0.5);
      }
    }
  }
}

function fmtQty(q: number): string {
  return q >= 1e6 ? `${(q / 1e6).toFixed(1)}M` : q >= 1e4 ? `${Math.round(q / 1e3)}K` : q >= 1e3 ? `${(q / 1e3).toFixed(1)}K` : `${Math.round(q)}`;
}
