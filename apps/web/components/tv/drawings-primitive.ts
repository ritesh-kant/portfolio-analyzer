import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitiveAxisView,
  Logical,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts';
import {
  type Drawing,
  FIB_LEVELS,
  type TimeAxis,
  formatDuration,
} from '../../lib/tv/drawings';

const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';

export interface DrawingsState {
  drawings: Drawing[];
  draft: Drawing | null;
  selectedId: string | null;
  hoverId: string | null;
  hidden: boolean;
  axis: TimeAxis;
  decimals: number;
}

interface Px {
  x: number;
  y: number;
}

/** Pixel geometry of one drawing, shared by painting and hit-testing. */
interface Geometry {
  pts: Px[];
  /** Pane size, for lines that run to an edge. */
  w: number;
  h: number;
}

export type Hit = { drawingId: string; handle: number | 'body' };

const dist2 = (a: Px, b: Px) => (a.x - b.x) ** 2 + (a.y - b.y) ** 2;

function distToSegment(p: Px, a: Px, b: Px): number {
  const l2 = dist2(a, b);
  if (l2 === 0) return Math.sqrt(dist2(p, a));
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / l2));
  return Math.sqrt(dist2(p, { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y) }));
}

/** Extend the line a→b to the pane's right (or left) edge. */
function rayEnd(a: Px, b: Px, w: number): Px {
  if (b.x === a.x) return { x: b.x, y: b.y > a.y ? 1e5 : -1e5 };
  const dir = b.x > a.x ? 1 : -1;
  const slope = (b.y - a.y) / (b.x - a.x);
  const x = dir > 0 ? w + 50 : -50;
  return { x, y: a.y + slope * (x - a.x) };
}

function fibLevelPrice(d: Drawing, v: number): number {
  const [a, b] = d.pts as unknown as [{ p: number }, { p: number }];
  return b.p + (a.p - b.p) * v;
}

export class DrawingsPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private state: DrawingsState = {
    drawings: [],
    draft: null,
    selectedId: null,
    hoverId: null,
    hidden: false,
    axis: { count: 0, toLogical: () => 0, toTime: () => 0 },
    decimals: 2,
  };
  private axisViews: ISeriesPrimitiveAxisView[] = [];
  private view: IPrimitivePaneView = {
    zOrder: () => 'top',
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
    this.series = param.series as ISeriesApi<SeriesType, Time>;
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

  priceAxisViews() {
    return this.axisViews;
  }

  updateAllViews() {
    this.axisViews = [];
    if (this.state.hidden || !this.series) return;
    const all = this.state.draft ? [...this.state.drawings, this.state.draft] : this.state.drawings;
    for (const d of all) {
      const prices: number[] =
        d.kind === 'hline' || d.kind === 'hray'
          ? [d.pts[0]!.p]
          : d.id === this.state.selectedId && (d.kind === 'trend' || d.kind === 'ray')
            ? d.pts.map((pt) => pt.p)
            : [];
      for (const price of prices) {
        const y = this.series.priceToCoordinate(price);
        if (y == null) continue;
        this.axisViews.push({
          coordinate: () => y,
          text: () => price.toFixed(this.state.decimals),
          textColor: () => '#ffffff',
          backColor: () => d.color,
        });
      }
    }
  }

  setState(next: Partial<DrawingsState>) {
    this.state = { ...this.state, ...next };
    this.requestUpdate?.();
  }

  // ── geometry ────────────────────────────────────────────────────────────

  private px(t: number, p: number): Px | null {
    if (!this.chart || !this.series) return null;
    const x = this.chart.timeScale().logicalToCoordinate(this.state.axis.toLogical(t) as Logical);
    const y = this.series.priceToCoordinate(p);
    return x == null || y == null ? null : { x, y };
  }

  private geometry(d: Drawing, w: number, h: number): Geometry | null {
    const pts: Px[] = [];
    for (const pt of d.pts) {
      const px = this.px(pt.t, pt.p);
      if (!px) return null;
      pts.push(px);
    }
    return { pts, w, h };
  }

  /** Paneside size, read from the chart. */
  private paneSize() {
    const size = this.chart?.paneSize(0);
    return { w: size?.width ?? 0, h: size?.height ?? 0 };
  }

  findHit(x: number, y: number, tol = 7): Hit | null {
    if (this.state.hidden) return null;
    const { w, h } = this.paneSize();
    const p = { x, y };
    // Topmost (latest) drawing first; the selected one wins ties via handles.
    const order = [...this.state.drawings].reverse();
    const sel = this.state.drawings.find((d) => d.id === this.state.selectedId);
    if (sel) order.unshift(sel);
    for (const d of order) {
      const g = this.geometry(d, w, h);
      if (!g) continue;
      if (d.id === this.state.selectedId && !d.locked) {
        const handles = this.handlePoints(d, g);
        for (let i = 0; i < handles.length; i += 1) {
          if (handles[i] && dist2(p, handles[i]!) <= 10 * 10) return { drawingId: d.id, handle: i };
        }
      }
      if (this.bodyHit(d, g, p, tol)) return { drawingId: d.id, handle: 'body' };
    }
    return null;
  }

  private handlePoints(d: Drawing, g: Geometry): (Px | null)[] {
    if (d.kind === 'hline') return [null];
    if (d.kind === 'vline') return [null];
    return g.pts;
  }

  private bodyHit(d: Drawing, g: Geometry, p: Px, tol: number): boolean {
    const [a, b] = g.pts as [Px, Px | undefined];
    switch (d.kind) {
      case 'trend':
        return distToSegment(p, a, b!) <= tol;
      case 'ray':
        return distToSegment(p, a, rayEnd(a, b!, g.w)) <= tol;
      case 'hline':
        return Math.abs(p.y - a.y) <= tol;
      case 'hray':
        return Math.abs(p.y - a.y) <= tol && p.x >= a.x - tol;
      case 'vline':
        return Math.abs(p.x - a.x) <= tol;
      case 'rect':
      case 'measure': {
        const x0 = Math.min(a.x, b!.x);
        const x1 = Math.max(a.x, b!.x);
        const y0 = Math.min(a.y, b!.y);
        const y1 = Math.max(a.y, b!.y);
        if (d.kind === 'measure') return p.x >= x0 - tol && p.x <= x1 + tol && p.y >= y0 - tol && p.y <= y1 + tol;
        const inside = p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1;
        const nearEdge =
          p.x >= x0 - tol && p.x <= x1 + tol && p.y >= y0 - tol && p.y <= y1 + tol && !(
            p.x > x0 + tol && p.x < x1 - tol && p.y > y0 + tol && p.y < y1 - tol
          );
        return inside || nearEdge;
      }
      case 'fib': {
        const x0 = Math.min(a.x, b!.x);
        const x1 = Math.max(a.x, b!.x);
        if (distToSegment(p, a, b!) <= tol) return true;
        if (p.x < x0 - tol || p.x > x1 + tol) return false;
        return FIB_LEVELS.some((l) => {
          const y = this.series?.priceToCoordinate(fibLevelPrice(d, l.v));
          return y != null && Math.abs(p.y - y) <= tol;
        });
      }
      case 'text':
        return Math.abs(p.x - a.x) < 60 && p.y > a.y - 16 && p.y < a.y + 6;
    }
  }

  // ── painting ───────────────────────────────────────────────────────────

  private paint(ctx: CanvasRenderingContext2D, w: number, h: number) {
    if (this.state.hidden || !this.series || !this.chart) return;
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, w, h);
    ctx.clip();
    const all = this.state.draft ? [...this.state.drawings, this.state.draft] : this.state.drawings;
    for (const d of all) {
      const g = this.geometry(d, w, h);
      if (!g) continue;
      const selected = d.id === this.state.selectedId;
      const hovered = d.id === this.state.hoverId;
      this.paintOne(ctx, d, g, selected, hovered);
      if (selected && !d.locked) this.paintHandles(ctx, d, g);
    }
    ctx.restore();
  }

  private paintHandles(ctx: CanvasRenderingContext2D, d: Drawing, g: Geometry) {
    const handles = this.handlePoints(d, g);
    for (const hp of handles) {
      if (!hp) continue;
      ctx.beginPath();
      ctx.arc(hp.x, hp.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#131722';
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#2962ff';
      ctx.stroke();
    }
  }

  private paintOne(ctx: CanvasRenderingContext2D, d: Drawing, g: Geometry, selected: boolean, hovered: boolean) {
    const [a, b] = g.pts as [Px, Px | undefined];
    ctx.lineWidth = d.width + (hovered && !selected ? 0.5 : 0);
    ctx.strokeStyle = d.color;
    ctx.fillStyle = d.color;
    ctx.setLineDash([]);
    ctx.lineCap = 'round';
    const line = (p: Px, q: Px) => {
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(q.x, q.y);
      ctx.stroke();
    };
    switch (d.kind) {
      case 'trend':
        line(a, b!);
        break;
      case 'ray':
        line(a, rayEnd(a, b!, g.w));
        break;
      case 'hline':
        line({ x: 0, y: a.y }, { x: g.w, y: a.y });
        break;
      case 'hray':
        line(a, { x: g.w, y: a.y });
        break;
      case 'vline':
        line({ x: a.x, y: 0 }, { x: a.x, y: g.h });
        break;
      case 'rect': {
        const x = Math.min(a.x, b!.x);
        const y = Math.min(a.y, b!.y);
        const rw = Math.abs(b!.x - a.x);
        const rh = Math.abs(b!.y - a.y);
        ctx.globalAlpha = 0.15;
        ctx.fillRect(x, y, rw, rh);
        ctx.globalAlpha = 1;
        ctx.strokeRect(x, y, rw, rh);
        break;
      }
      case 'fib':
        this.paintFib(ctx, d, a, b!);
        break;
      case 'measure':
        this.paintMeasure(ctx, d, a, b!);
        break;
      case 'text': {
        ctx.font = `600 13px ${FONT}`;
        ctx.textBaseline = 'alphabetic';
        const text = d.text ?? 'Text';
        const tw = ctx.measureText(text).width;
        if (selected || hovered) {
          ctx.strokeStyle = 'rgba(41,98,255,0.8)';
          ctx.lineWidth = 1;
          ctx.strokeRect(a.x - 4, a.y - 15, tw + 8, 20);
        }
        ctx.fillStyle = d.color;
        ctx.fillText(text, a.x, a.y);
        break;
      }
    }
  }

  private paintFib(ctx: CanvasRenderingContext2D, d: Drawing, a: Px, b: Px) {
    const x0 = Math.min(a.x, b.x);
    const x1 = Math.max(a.x, b.x);
    const ys = FIB_LEVELS.map((l) => this.series?.priceToCoordinate(fibLevelPrice(d, l.v)) ?? null);
    ctx.font = `11px ${FONT}`;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'right';
    for (let i = 0; i < FIB_LEVELS.length; i += 1) {
      const y = ys[i];
      if (y == null) continue;
      const level = FIB_LEVELS[i]!;
      const next = ys[i + 1];
      if (next != null && i + 1 < FIB_LEVELS.length && level.v < 1) {
        ctx.globalAlpha = 0.09;
        ctx.fillStyle = FIB_LEVELS[i + 1]!.color;
        ctx.fillRect(x0, Math.min(y, next), x1 - x0, Math.abs(next - y));
        ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = level.color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x0, y);
      ctx.lineTo(x1, y);
      ctx.stroke();
      ctx.fillStyle = level.color;
      ctx.fillText(`${level.v} (${fibLevelPrice(d, level.v).toFixed(this.state.decimals)})`, x0 - 6, y);
    }
    ctx.textAlign = 'left';
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = '#787b86';
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  private paintMeasure(ctx: CanvasRenderingContext2D, d: Drawing, a: Px, b: Px) {
    const [p0, p1] = d.pts as [{ t: number; p: number }, { t: number; p: number }];
    const up = p1.p >= p0.p;
    const color = up ? '#2962ff' : '#f23645';
    const x = Math.min(a.x, b.x);
    const y = Math.min(a.y, b.y);
    const rw = Math.abs(b.x - a.x);
    const rh = Math.abs(b.y - a.y);
    ctx.globalAlpha = 0.18;
    ctx.fillStyle = color;
    ctx.fillRect(x, y, rw, rh);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    // Vertical arrow along the price move, horizontal arrow along time.
    const midX = x + rw / 2;
    const midY = y + rh / 2;
    ctx.beginPath();
    ctx.moveTo(midX, a.y);
    ctx.lineTo(midX, b.y);
    ctx.moveTo(a.x, midY);
    ctx.lineTo(b.x, midY);
    ctx.stroke();
    const head = (tx: number, ty: number, dx: number, dy: number) => {
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(tx - 6 * dx + 4 * dy, ty - 6 * dy - 4 * dx);
      ctx.moveTo(tx, ty);
      ctx.lineTo(tx - 6 * dx - 4 * dy, ty - 6 * dy + 4 * dx);
      ctx.stroke();
    };
    head(midX, b.y, 0, b.y >= a.y ? 1 : -1);
    head(b.x, midY, b.x >= a.x ? 1 : -1, 0);

    const dp = p1.p - p0.p;
    const pct = p0.p ? (dp / p0.p) * 100 : 0;
    const bars = Math.round(this.state.axis.toLogical(p1.t) - this.state.axis.toLogical(p0.t));
    const lines = [
      `${dp >= 0 ? '+' : ''}${dp.toFixed(this.state.decimals)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`,
      `${Math.abs(bars)} bars, ${formatDuration(p1.t - p0.t)}`,
    ];
    ctx.font = `12px ${FONT}`;
    const bw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 16;
    const bh = 40;
    const bx = midX - bw / 2;
    const by = up ? y - bh - 8 : y + rh + 8;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(bx, by, bw, bh, 4);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    ctx.fillText(lines[0]!, midX, by + 13);
    ctx.fillText(lines[1]!, midX, by + 28);
    ctx.textAlign = 'left';
  }
}
