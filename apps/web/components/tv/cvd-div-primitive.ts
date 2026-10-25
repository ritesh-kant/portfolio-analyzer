import type {
  Coordinate,
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

const BULL = '#26a69a';
const BEAR = '#ef5350';
const NEAR = '#e879f9';
/** How long a clicked divergence pulses, and the spacing and reach of the expanding rings. */
const FOCUS_MS = 2600;
const RING_MS = 850;
const RING_REACH = 30;
const FONT = '600 11px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif';

export interface CvdDivMark {
  /** Chart bars of the earlier and later swing, and the value each is drawn at in THIS pane (price or CVD). */
  i1: number;
  i2: number;
  v1: number;
  v2: number;
  bullish: boolean;
  hidden: boolean;
  status: 'confirmed' | 'pending' | 'failed';
  /** The break that confirmed it: chart bar and the price level broken (drawn on the price pane only). */
  confirmIndex: number | null;
  level: number | null;
  /** A delta-divergence setup pointing the same way sits next to the second swing. */
  near: boolean;
  /** Picked in the side list: drawn with a halo. */
  focus?: boolean;
}

/**
 * The divergence a trader draws by hand: a line joining the two swings, in the pane's own scale (price on the
 * price pane, CVD on the CVD pane), with a tag on the second one. Solid and bright once price has broken the
 * swing between them, dashed while it only warns, faded when the move went on anyway.
 */
export class CvdDivPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private marks: CvdDivMark[] = [];
  private pulseAt: number | null = null;
  private pulseStart = 0;
  private raf = 0;
  /** The price pane draws the broken level too; the CVD pane has no price for it. */
  constructor(private readonly showLevel: boolean) {}
  private view: IPrimitivePaneView = {
    zOrder: () => 'top',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => this.paint(context, mediaSize.height));
      },
    }),
  };

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart as IChartApi;
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached() {
    cancelAnimationFrame(this.raf);
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews() {
    return [this.view];
  }

  setState(marks: CvdDivMark[]) {
    this.marks = marks;
    this.requestUpdate?.();
  }

  /** Pulse the divergence whose second swing is chart bar `i2` for a couple of seconds, so it is easy to spot. */
  pulse(i2: number) {
    cancelAnimationFrame(this.raf);
    this.pulseAt = i2;
    this.pulseStart = performance.now();
    this.tick();
  }

  private tick = () => {
    this.requestUpdate?.();
    if (performance.now() - this.pulseStart < FOCUS_MS) {
      this.raf = requestAnimationFrame(this.tick);
    } else {
      this.pulseAt = null;
      this.requestUpdate?.();
    }
  };

  /** A soft column over the whole pane across the two swings, and rings spreading from the second one. */
  private pulsePaint(ctx: CanvasRenderingContext2D, m: CvdDivMark, x1: number, x2: number, y2: number, height: number) {
    const t = performance.now() - this.pulseStart;
    const fade = Math.max(0, 1 - t / FOCUS_MS);
    const rgb = m.bullish ? '38, 166, 154' : '239, 83, 80';
    const half = Math.max(6, (this.chart?.timeScale().options().barSpacing ?? 8) * 0.75);
    ctx.save();
    ctx.fillStyle = `rgba(${rgb}, ${(0.16 * fade).toFixed(3)})`;
    ctx.fillRect(x1 - half, 0, x2 - x1 + half * 2, height);
    ctx.fillStyle = `rgba(${rgb}, ${(0.7 * fade).toFixed(3)})`;
    ctx.fillRect(x1 - half, 0, 1, height);
    ctx.fillRect(x2 + half - 1, 0, 1, height);
    for (let k = 0; k < 2; k += 1) {
      const phase = ((t + k * (RING_MS / 2)) % RING_MS) / RING_MS;
      ctx.beginPath();
      ctx.arc(x2, y2, 6 + phase * RING_REACH, 0, Math.PI * 2);
      ctx.lineWidth = 2;
      ctx.strokeStyle = `rgba(${rgb}, ${((1 - phase) * fade).toFixed(3)})`;
      ctx.stroke();
    }
    ctx.restore();
  }

  private label(m: CvdDivMark): string {
    const side = m.bullish ? 'Bull' : 'Bear';
    return `${m.bullish ? '▲' : '▼'} ${m.hidden ? 'Hidden ' : ''}${side} div${m.near ? ' + Δ' : ''}`;
  }

  private paint(ctx: CanvasRenderingContext2D, height: number) {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    const ts = chart.timeScale();
    for (const m of this.marks) {
      const x1 = ts.logicalToCoordinate(m.i1 as Logical);
      const x2 = ts.logicalToCoordinate(m.i2 as Logical);
      const y1 = series.priceToCoordinate(m.v1);
      const y2 = series.priceToCoordinate(m.v2);
      if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
      const color = m.bullish ? BULL : BEAR;
      if (this.pulseAt === m.i2) this.pulsePaint(ctx, m, x1, x2, y2, height);
      const alpha = m.status === 'failed' ? 0.35 : 1;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = color;
      if (m.focus) {
        ctx.save();
        ctx.globalAlpha = 0.28;
        ctx.lineWidth = 9;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.restore();
      }
      ctx.lineWidth = m.status === 'confirmed' ? 2 : 1.5;
      ctx.setLineDash(m.status === 'confirmed' ? [] : [5, 4]);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      ctx.setLineDash([]);
      for (const [x, y] of [
        [x1, y1],
        [x2, y2],
      ] as const) {
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fillStyle = m.status === 'confirmed' ? color : '#131722';
        ctx.fill();
        ctx.stroke();
      }
      // the swing that has to break, from the second swing to the candle that broke it
      if (this.showLevel && m.status === 'confirmed' && m.confirmIndex != null && m.level != null) {
        const xc = ts.logicalToCoordinate(m.confirmIndex as Logical);
        const yl = series.priceToCoordinate(m.level);
        if (xc != null && yl != null) {
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(x2, yl);
          ctx.lineTo(xc, yl);
          ctx.stroke();
          ctx.setLineDash([]);
          const dir = m.bullish ? -1 : 1;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.moveTo(xc, yl + dir * 9);
          ctx.lineTo(xc - 5, yl + dir * 1);
          ctx.lineTo(xc + 5, yl + dir * 1);
          ctx.closePath();
          ctx.fill();
        }
      }
      // tag at the second swing: above a swing high, below a swing low
      const text = this.label(m);
      ctx.font = FONT;
      const w = ctx.measureText(text).width + 10;
      const h = 16;
      const tx = x2 - w / 2;
      // clear every candle under the tag, not just the swing's own: a bull tag goes below the lowest low in its
      // span, a bear tag above the highest high, so it never covers a wick
      let yEdge: number = y2;
      const l0 = Math.floor(ts.coordinateToLogical((x2 - w / 2) as Coordinate) ?? m.i2);
      const l1 = Math.ceil(ts.coordinateToLogical((x2 + w / 2) as Coordinate) ?? m.i2);
      for (let l = l0; l <= l1; l += 1) {
        const bar = series.dataByIndex(l) as { low?: number; high?: number } | null;
        const v = m.bullish ? bar?.low : bar?.high;
        const y = v == null ? null : series.priceToCoordinate(v);
        if (y != null) yEdge = m.bullish ? Math.max(yEdge, y) : Math.min(yEdge, y);
      }
      const ty = m.bullish ? yEdge + 8 : yEdge - 8 - h;
      ctx.fillStyle = 'rgba(19, 23, 34, 0.92)';
      ctx.strokeStyle = m.near ? NEAR : color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(tx, ty, w, h, 4);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = m.near ? NEAR : color;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, x2, ty + h / 2 + 0.5);
      ctx.restore();
    }
  }
}
