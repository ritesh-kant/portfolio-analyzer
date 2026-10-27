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

const BULL = '38, 166, 154';
const BEAR = '239, 83, 80';
const FOCUS_MS = 2600;
/** One expanding ring every this many ms while pulsing, and how far it spreads. */
const RING_MS = 850;
const RING_REACH = 30;
const FONT = '600 11px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif';

export interface FvgMark {
  /** Chart bars of the gap's first candle and of the candle that completes it. */
  i1: number;
  i2: number;
  /** Chart bar that invalidated the zone, or null while it is alive (the zone then runs to the right edge). */
  endIndex: number | null;
  top: number;
  bottom: number;
  bullish: boolean;
  tested: boolean;
  /** Three-candle volume and the against-share of it (percent), null without volume. */
  volume: number;
  against: number | null;
  /** Picked in the side list: outlined until another is picked. */
  focus?: boolean;
}

const fmtVol = (v: number) =>
  v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : `${Math.round(v)}`;

/**
 * Fair value gap zones: a translucent box from the gap's first candle to the candle that invalidated it (or the
 * right edge while it is alive), a dashed midline, and the volume read-out at its right: total volume and the
 * share that went against the gap. Invalidated zones are faded. A zone picked in the side list pulses.
 */
export class FvgPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private marks: FvgMark[] = [];
  private pulseAt: number | null = null;
  private pulseStart = 0;
  private raf = 0;
  private view: IPrimitivePaneView = {
    zOrder: () => 'bottom',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => this.paint(context, mediaSize.width, mediaSize.height));
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

  setState(marks: FvgMark[]) {
    this.marks = marks;
    this.requestUpdate?.();
  }

  /** Pulse the zone completed by chart bar `i2` for a couple of seconds. */
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

  /** A soft column over the whole pane across the gap's three candles, and rings spreading from the zone. */
  private pulsePaint(ctx: CanvasRenderingContext2D, rgb: string, x0: number, x1: number, y: number, height: number) {
    const t = performance.now() - this.pulseStart;
    const fade = Math.max(0, 1 - t / FOCUS_MS);
    ctx.save();
    ctx.fillStyle = `rgba(${rgb}, ${(0.14 * fade).toFixed(3)})`;
    ctx.fillRect(x0, 0, x1 - x0, height);
    ctx.fillStyle = `rgba(${rgb}, ${(0.7 * fade).toFixed(3)})`;
    ctx.fillRect(x0, 0, 1, height);
    ctx.fillRect(x1 - 1, 0, 1, height);
    for (let k = 0; k < 2; k += 1) {
      const phase = ((t + k * (RING_MS / 2)) % RING_MS) / RING_MS;
      ctx.beginPath();
      ctx.arc((x0 + x1) / 2, y, 8 + phase * RING_REACH, 0, Math.PI * 2);
      ctx.lineWidth = 2;
      ctx.strokeStyle = `rgba(${rgb}, ${((1 - phase) * fade).toFixed(3)})`;
      ctx.stroke();
    }
    ctx.restore();
  }

  private paint(ctx: CanvasRenderingContext2D, width: number, height: number) {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    const ts = chart.timeScale();
    const half = (ts.options().barSpacing ?? 8) / 2;
    for (const m of this.marks) {
      const xa = ts.logicalToCoordinate(m.i1 as Logical);
      const xb = m.endIndex == null ? width : ts.logicalToCoordinate(m.endIndex as Logical);
      const yt = series.priceToCoordinate(m.top);
      const yb = series.priceToCoordinate(m.bottom);
      if (xa == null || xb == null || yt == null || yb == null) continue;
      const x0 = xa - half;
      const x1 = Math.max(x0 + 2, m.endIndex == null ? width : xb);
      if (x1 < 0 || x0 > width) continue;
      const rgb = m.bullish ? BULL : BEAR;
      const dead = m.endIndex != null;
      const fade = dead ? 0.45 : 1;
      const focus = this.pulseAt === m.i2;
      const t = performance.now() - this.pulseStart;
      const glow = focus ? Math.max(0, 1 - t / FOCUS_MS) : 0;
      const h = yb - yt;
      if (focus && glow > 0) {
        // the column spans the gap's own three candles
        const xc = ts.logicalToCoordinate(m.i2 as Logical);
        this.pulsePaint(ctx, rgb, x0, (xc ?? x0) + half, yt + h / 2, height);
      }
      ctx.save();
      ctx.fillStyle = `rgba(${rgb}, ${((m.tested ? 0.12 : 0.2) * fade + 0.25 * glow).toFixed(3)})`;
      ctx.fillRect(x0, yt, x1 - x0, h);
      ctx.strokeStyle = `rgba(${rgb}, ${(0.55 * fade + 0.45 * glow).toFixed(3)})`;
      ctx.lineWidth = focus && glow > 0 ? 2 : 1;
      ctx.strokeRect(x0 + 0.5, yt + 0.5, x1 - x0 - 1, h - 1);
      if (m.focus) {
        ctx.save();
        ctx.strokeStyle = `rgba(${rgb}, 0.35)`;
        ctx.lineWidth = 5;
        ctx.strokeRect(x0 - 1.5, yt - 1.5, x1 - x0 + 3, h + 3);
        ctx.restore();
        ctx.strokeStyle = `rgb(${rgb})`;
        ctx.lineWidth = 2;
        ctx.strokeRect(x0 + 1, yt + 1, x1 - x0 - 2, h - 2);
      }
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x0, yt + h / 2);
      ctx.lineTo(x1, yt + h / 2);
      ctx.stroke();
      ctx.setLineDash([]);
      // the volume read-out at the zone's right end, clipped to the zone so a dead one does not spill
      if (m.volume > 0 && x1 - x0 > 60 && Math.abs(h) > 12) {
        const text = `${fmtVol(m.volume)}${m.against != null ? ` · ${Math.round(m.against)}%` : ''}`;
        ctx.font = FONT;
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = `rgba(209, 212, 220, ${(0.9 * fade).toFixed(3)})`;
        ctx.fillText(text, Math.min(x1, width) - 6, yt + h / 2 + 0.5);
      }
      ctx.restore();
    }
  }
}
