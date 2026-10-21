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

const COLOR = '#e879f9';
const RADIUS = 7;
/** Gap between the candle's wick tip and the badge edge. */
const GAP = 6;
/** How long a clicked divergence pulses before it settles back to a normal badge. */
const FOCUS_MS = 2600;
/** One expanding ring every this many ms while pulsing. */
const RING_MS = 850;
const RING_REACH = 30;
/** Under each candle the footprint prints bid/ask totals and a Delta/Total box; badges below it must clear them. */
const FOOTPRINT_LABELS_BOX = 78;
/** On narrow candles it prints two plain lines instead. */
const FOOTPRINT_LABELS_LINES = 48;
/** The footprint prints nothing under candles narrower than this (px per bar), and the box needs this much. */
const FOOTPRINT_LABEL_SPACING = 30;
const FOOTPRINT_BOX_SPACING = 68;

export interface DeltaMark {
  /** Chart bars of the 15-minute signal candle: its first and last. On a 5m chart a candle spans three bars. */
  startIndex: number;
  index: number;
  high: number;
  low: number;
  /** buyers = trapped at the high (badge above the candle); sellers = trapped at the low (below). */
  kind: 'buyers' | 'sellers';
  /** Solid badge once a later candle confirmed the setup; hollow while it waits or never followed through. */
  confirmed: boolean;
  /** Chart bar of the confirming candle, and the signal candle's extreme it broke. */
  confirmIndex: number | null;
  level: number | null;
}

/** A Δ badge above a candle whose upper wick met aggressive buying, below one whose lower wick met aggressive selling. */
export class DeltaPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private marks: DeltaMark[] = [];
  private footprintLabels = false;
  private focused: DeltaMark | null = null;
  private focusStart = 0;
  private raf = 0;
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

  /** `footprintLabels`: the footprint is showing its Δ/volume text under each candle. */
  setState(marks: DeltaMark[], footprintLabels = false) {
    this.marks = marks;
    this.footprintLabels = footprintLabels;
    this.requestUpdate?.();
  }

  /** Pulse one setup for a couple of seconds, even when the badges are switched off. */
  focus(mark: DeltaMark) {
    cancelAnimationFrame(this.raf);
    this.focused = mark;
    this.focusStart = performance.now();
    this.tick();
  }

  private tick = () => {
    this.requestUpdate?.();
    if (performance.now() - this.focusStart < FOCUS_MS) {
      this.raf = requestAnimationFrame(this.tick);
    } else {
      this.focused = null;
      this.requestUpdate?.();
    }
  };

  private badge(
    ctx: CanvasRenderingContext2D,
    cx: number,
    cy: number,
    radius: number,
    solid: boolean,
  ) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fillStyle = solid ? COLOR : 'rgba(19, 23, 34, 0.9)';
    ctx.fill();
    ctx.lineWidth = solid ? 1.5 : 2;
    ctx.strokeStyle = solid ? '#ffffff' : COLOR;
    ctx.stroke();
    ctx.fillStyle = solid ? '#131722' : COLOR;
    ctx.font = `bold ${Math.round(radius * 1.45)}px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Δ', cx, cy + 0.5);
  }

  private pos(m: DeltaMark): { cx: number; cy: number; x0: number; x1: number } | null {
    const xEnd = this.chart?.timeScale().logicalToCoordinate(m.index as Logical);
    const xStart = this.chart?.timeScale().logicalToCoordinate(m.startIndex as Logical);
    const py = this.series?.priceToCoordinate(m.kind === 'buyers' ? m.high : m.low);
    if (xEnd == null || py == null || !this.chart) return null;
    const x1 = xEnd;
    const x0 = xStart ?? xEnd;
    const cx = (x0 + x1) / 2; // the middle of the candle: on a 5m chart, the middle of its three bars
    const spacing = this.chart.timeScale().options().barSpacing;
    const clear = !this.footprintLabels || spacing < FOOTPRINT_LABEL_SPACING
      ? 0
      : spacing >= FOOTPRINT_BOX_SPACING
        ? FOOTPRINT_LABELS_BOX
        : FOOTPRINT_LABELS_LINES;
    return {
      cx,
      x0,
      x1,
      cy: m.kind === 'buyers' ? py - GAP - RADIUS : py + GAP + RADIUS + clear,
    };
  }

  /** The broken level, as a dashed line from the signal candle to the confirming one, ending in an arrow. */
  private confirmation(ctx: CanvasRenderingContext2D, m: DeltaMark, alpha: number) {
    if (!m.confirmed || m.confirmIndex == null || m.level == null || !this.chart || !this.series) return;
    const x0 = this.chart.timeScale().logicalToCoordinate(m.index as Logical);
    const x1 = this.chart.timeScale().logicalToCoordinate(m.confirmIndex as Logical);
    const y = this.series.priceToCoordinate(m.level);
    if (x0 == null || x1 == null || y == null) return;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = COLOR;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x1, y);
    ctx.stroke();
    ctx.setLineDash([]);
    // arrow at the confirming candle: down for a short (buyers trapped), up for a long
    const dir = m.kind === 'buyers' ? 1 : -1;
    ctx.fillStyle = COLOR;
    ctx.beginPath();
    ctx.moveTo(x1, y + dir * 9);
    ctx.lineTo(x1 - 5, y + dir * 1);
    ctx.lineTo(x1 + 5, y + dir * 1);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  /** When the candle spans several chart bars, a bracket at the badge's height shows exactly which ones. */
  private bracket(
    ctx: CanvasRenderingContext2D,
    p: { cy: number; x0: number; x1: number },
    m: DeltaMark,
    alpha: number,
  ) {
    if (!this.chart || p.x1 - p.x0 < 2) return;
    const hw = this.chart.timeScale().options().barSpacing * 0.45;
    const tick = m.kind === 'buyers' ? 5 : -5; // ends point toward the candle
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = COLOR;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(p.x0 - hw, p.cy + tick);
    ctx.lineTo(p.x0 - hw, p.cy);
    ctx.lineTo(p.x1 + hw, p.cy);
    ctx.lineTo(p.x1 + hw, p.cy + tick);
    ctx.stroke();
    ctx.restore();
  }

  private paint(ctx: CanvasRenderingContext2D, height: number) {
    if (!this.chart || !this.series) return;
    const f = this.focused;
    for (const m of this.marks) {
      if (f && m.index === f.index) continue; // drawn below, enlarged
      const p = this.pos(m);
      if (!p) continue;
      this.confirmation(ctx, m, 0.85);
      this.bracket(ctx, p, m, 0.7);
      this.badge(ctx, p.cx, p.cy, RADIUS, m.confirmed);
    }
    if (!f) return;
    const p = this.pos(f);
    if (!p) return;
    const t = performance.now() - this.focusStart;
    const fade = Math.max(0, 1 - t / FOCUS_MS);
    // a soft column through the whole pane at the candle, so it can be found on a busy chart
    const half = Math.max(6, this.chart.timeScale().options().barSpacing * 0.75);
    ctx.fillStyle = `rgba(232, 121, 249, ${(0.22 * fade).toFixed(3)})`;
    ctx.fillRect(p.x0 - half, 0, p.x1 - p.x0 + half * 2, height);
    ctx.fillStyle = `rgba(232, 121, 249, ${(0.7 * fade).toFixed(3)})`;
    ctx.fillRect(p.x0 - half, 0, 1, height);
    ctx.fillRect(p.x1 + half - 1, 0, 1, height);
    // rings spreading out from the badge
    for (let k = 0; k < 2; k += 1) {
      const phase = ((t + k * (RING_MS / 2)) % RING_MS) / RING_MS;
      ctx.beginPath();
      ctx.arc(p.cx, p.cy, RADIUS + 3 + phase * RING_REACH, 0, Math.PI * 2);
      ctx.lineWidth = 2;
      ctx.strokeStyle = `rgba(232, 121, 249, ${((1 - phase) * fade).toFixed(3)})`;
      ctx.stroke();
    }
    this.confirmation(ctx, f, 1);
    this.bracket(ctx, p, f, 1);
    this.badge(ctx, p.cx, p.cy, RADIUS * (1 + 0.45 * fade), f.confirmed);
  }
}
