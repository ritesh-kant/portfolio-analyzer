import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  Time,
} from 'lightweight-charts';

const PURPLE = '#9c27b0';
const RADIUS = 9;
/** Distance of the marker centre from the bottom of the price pane. */
const BOTTOM = 16;

/** TradingView-style news badges: a purple lightning circle under the candle a filing belongs to. */
export class NewsPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private bars: number[] = [];
  private active: number | null = null;
  private view: IPrimitivePaneView = {
    zOrder: () => 'top',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) =>
          this.paint(context, mediaSize.height),
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

  /** Bar indices that carry news, and the one currently highlighted. */
  setState(bars: number[], active: number | null) {
    this.bars = bars;
    this.active = active;
    this.requestUpdate?.();
  }

  /** The news bar whose badge is under (x, y), if any. */
  findBar(x: number, y: number): number | null {
    const size = this.chart?.paneSize(0);
    if (!this.chart || !size) return null;
    const cy = size.height - BOTTOM;
    if (Math.abs(y - cy) > RADIUS + 3) return null;
    for (const idx of this.bars) {
      const cx = this.chart.timeScale().logicalToCoordinate(idx as Logical);
      if (cx != null && Math.abs(x - cx) <= RADIUS + 3) return idx;
    }
    return null;
  }

  /** Pixel position of a badge, for anchoring the popup. */
  position(idx: number): { x: number; y: number } | null {
    const size = this.chart?.paneSize(0);
    const cx = this.chart?.timeScale().logicalToCoordinate(idx as Logical);
    return size && cx != null ? { x: cx, y: size.height - BOTTOM } : null;
  }

  private paint(ctx: CanvasRenderingContext2D, h: number) {
    if (!this.chart) return;
    const cy = h - BOTTOM;
    for (const idx of this.bars) {
      const cx = this.chart.timeScale().logicalToCoordinate(idx as Logical);
      if (cx == null) continue;
      ctx.beginPath();
      ctx.arc(cx, cy, RADIUS + (idx === this.active ? 1.5 : 0), 0, Math.PI * 2);
      ctx.fillStyle = PURPLE;
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = idx === this.active ? '#ffffff' : '#131722';
      ctx.stroke();
      // lightning bolt
      ctx.beginPath();
      ctx.moveTo(cx + 1.5, cy - 5.5);
      ctx.lineTo(cx - 3.5, cy + 0.8);
      ctx.lineTo(cx - 0.3, cy + 0.8);
      ctx.lineTo(cx - 1.5, cy + 5.5);
      ctx.lineTo(cx + 3.5, cy - 0.8);
      ctx.lineTo(cx + 0.3, cy - 0.8);
      ctx.closePath();
      ctx.fillStyle = '#ffffff';
      ctx.fill();
    }
  }
}
