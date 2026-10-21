import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';
import { logicalX } from './coords';

const SHADE = 'rgba(41, 98, 255, 0.10)';
const BREAK = 'rgba(120, 123, 134, 0.55)';

/** Tints the hours outside the regular session and dashes the start of each trading day, behind the candles. */
export class SessionsPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private ranges: [number, number][] = [];
  private breaks: number[] = [];
  private view: IPrimitivePaneView = {
    zOrder: () => 'bottom',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) =>
          this.paint(context, mediaSize.width, mediaSize.height),
        );
      },
    }),
  };

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart as IChartApi;
    this.requestUpdate = param.requestUpdate;
  }

  detached() {
    this.chart = null;
    this.requestUpdate = null;
  }

  paneViews() {
    return [this.view];
  }

  /** `ranges` are inclusive candle-index pairs to tint; `breaks` are candle indices that start a session. */
  setState(ranges: [number, number][], breaks: number[]) {
    this.ranges = ranges;
    this.breaks = breaks;
    this.requestUpdate?.();
  }

  private paint(ctx: CanvasRenderingContext2D, w: number, h: number) {
    const chart = this.chart;
    if (!chart) return;
    const x = (logical: number) => logicalX(chart, logical);
    ctx.fillStyle = SHADE;
    for (const [a, b] of this.ranges) {
      const x0 = x(a - 0.5);
      const x1 = x(b + 0.5);
      if (x0 == null || x1 == null || x1 < 0 || x0 > w) continue;
      const left = Math.max(0, x0);
      ctx.fillRect(left, 0, Math.min(w, x1) - left, h);
    }
    ctx.strokeStyle = BREAK;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    for (const i of this.breaks) {
      const bx = x(i - 0.5);
      if (bx == null || bx < 0 || bx > w) continue;
      const px = Math.round(bx) + 0.5;
      ctx.moveTo(px, 0);
      ctx.lineTo(px, h);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }
}
