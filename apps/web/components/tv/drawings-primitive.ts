import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitiveAxisView,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts';
import { logicalX } from './coords';
import type { Bar } from '../../lib/tv/types';
import { DEFAULT_ROWS, type VolumeProfile, profileOver } from '../../lib/tv/volume-profile';
import { type Drawing, FREEHAND, type Pt, fibOf, vpOf, withAlpha, lineDash, type TimeAxis, formatDuration } from '../../lib/tv/drawings';
import {
  POSITION_PROFIT,
  POSITION_STOP,
  isPosition,
  positionEnd,
  positionSpan,
  positionStats,
} from '../../lib/tv/position';

const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';

export interface DrawingsState {
  drawings: Drawing[];
  draft: Drawing | null;
  selectedId: string | null;
  hoverId: string | null;
  hidden: boolean;
  axis: TimeAxis;
  decimals: number;
  /** The candles on screen, for volume profiles. */
  bars: Bar[];
  /** 1-minute candles under `bars` (candle length `secs`), for finer volume profiles; null when unavailable. */
  fine: Bar[] | null;
  secs: number;
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

const ARROW_MARK_W = 22;
const ARROW_MARK_H = 30;

/** Isosceles triangle inscribed in the box spanned by two anchors: apex top-middle, base along the bottom. */
function triangleVerts(a: Px, b: Px): [Px, Px, Px] {
  const top = Math.min(a.y, b.y);
  const bottom = Math.max(a.y, b.y);
  return [
    { x: (a.x + b.x) / 2, y: top },
    { x: Math.max(a.x, b.x), y: bottom },
    { x: Math.min(a.x, b.x), y: bottom },
  ];
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
  return fibOf(d).reverse ? a.p + (b.p - a.p) * v : b.p + (a.p - b.p) * v;
}

/**
 * One instance per pane: the price pane's (`paneKey` null) or an indicator pane's (its indicator id). Each is
 * attached to a series of its own pane, so prices are read against that pane's scale, and it only draws and
 * hit-tests the drawings that belong to it.
 */
/** A position box never gets narrower than this, whatever the zoom. */
const MIN_POSITION_PX = 24;

export class DrawingsPrimitive implements ISeriesPrimitive<Time> {
  constructor(private readonly paneKey: string | null = null) {}

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
    bars: [],
    fine: null,
    secs: 60,
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

  get hoverId() {
    return this.state.hoverId;
  }

  /** The drawings (and the draft) that live in this primitive's pane. */
  private mine(): Drawing[] {
    const own = (d: Drawing) => (d.pane ?? null) === this.paneKey;
    const list = this.state.drawings.filter(own);
    return this.state.draft && own(this.state.draft) ? [...list, this.state.draft] : list;
  }

  /** A value on this pane's scale: the price pane keeps its fixed decimals, an indicator pane uses its own format. */
  private fmt(v: number): string {
    return this.paneKey == null || !this.series
      ? v.toFixed(this.state.decimals)
      : this.series.priceFormatter().format(v);
  }

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
    for (const d of this.mine()) {
      const levels: { price: number; color: string }[] = [];
      if (d.kind === 'hline' || d.kind === 'hray') levels.push({ price: d.pts[0]!.p, color: d.color });
      else if (d.id === this.state.selectedId && (d.kind === 'trend' || d.kind === 'ray'))
        for (const pt of d.pts) levels.push({ price: pt.p, color: d.color });
      else if (d.id === this.state.selectedId && isPosition(d.kind)) {
        levels.push({ price: d.pts[2]!.p, color: POSITION_PROFIT });
        levels.push({ price: d.pts[0]!.p, color: '#787b86' });
        levels.push({ price: d.pts[3]!.p, color: POSITION_STOP });
      }
      if (d.kind === 'avprofile') {
        // VAH / VAL / POC tags on the price axis, as TradingView prints them.
        const profile = this.profileOf(d);
        const vp = vpOf(d);
        if (profile) {
          if (vp.showVa) {
            levels.push({ price: profile.vah, color: vp.vaLineColor });
            levels.push({ price: profile.val, color: vp.vaLineColor });
          }
          if (vp.showPoc) levels.push({ price: profile.pocPrice, color: vp.pocColor });
        }
      }
      for (const { price, color } of levels) {
        const y = this.series.priceToCoordinate(price);
        if (y == null) continue;
        this.axisViews.push({
          coordinate: () => y,
          text: () => this.fmt(price),
          textColor: () => '#ffffff',
          backColor: () => color,
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
    const x = logicalX(this.chart, this.state.axis.toLogical(t));
    const y = this.series.priceToCoordinate(p);
    return x == null || y == null ? null : { x, y };
  }

  private profiles = new Map<string, VolumeProfile | null>();

  /** Volume profile over the candles between a profile drawing's two anchors (memoised until the data or range changes). */
  private profileOf(d: Drawing): VolumeProfile | null {
    const { bars, fine, secs } = this.state;
    if (!bars.length || d.pts.length < (d.kind === 'avprofile' ? 1 : 2)) return null;
    const clamp = (i: number) => Math.max(0, Math.min(bars.length - 1, i));
    // An anchored profile's second edge is always the latest candle, so it grows as bars arrive.
    const i0 = Math.round(this.state.axis.toLogical(d.pts[0]!.t));
    const i1 = d.kind === 'avprofile' ? bars.length - 1 : Math.round(this.state.axis.toLogical(d.pts[1]!.t));
    const a = clamp(Math.min(i0, i1));
    const b = clamp(Math.max(i0, i1));
    return profileOver(bars, fine, secs, a, b, d.rows ?? DEFAULT_ROWS, vpOf(d).valueArea / 100, this.profiles);
  }

  private geometry(d: Drawing, w: number, h: number): Geometry | null {
    if (isPosition(d.kind)) return this.positionGeometry(d, w, h);
    const pts: Px[] = [];
    for (const pt of d.pts) {
      const px = this.px(pt.t, pt.p);
      if (!px) return null;
      pts.push(px);
    }
    if (d.kind === 'avprofile') {
      // Synthetic right edge on the latest candle (never left of the anchor).
      const last = this.state.bars[this.state.bars.length - 1];
      const end = last ? this.px(last.time, d.pts[0]!.p) : null;
      if (!end) return null;
      pts.push({ x: Math.max(end.x, pts[0]!.x), y: end.y });
    }
    if (d.kind === 'vprofile' || d.kind === 'avprofile') {
      // Anchors sit on the top edge of the profile, whatever price the cursor was at.
      const profile = this.profileOf(d);
      const top = profile ? this.series?.priceToCoordinate(profile.hi) : null;
      if (top != null) for (const p of pts) p.y = top;
    }
    return { pts, w, h };
  }

  /** Handles in order: entry-left, entry-right, target (top-middle), stop (bottom-middle). */
  private positionGeometry(d: Drawing, w: number, h: number): Geometry | null {
    const [a, b, tp, sl] = d.pts as [Pt, Pt, Pt, Pt];
    const left = this.px(a.t, a.p);
    if (!left || !this.chart) return null;
    // The width is a candle count, so it survives the overnight gaps that crush a duration.
    const rx = logicalX(this.chart, this.state.axis.toLogical(a.t) + positionSpan(d, this.state.axis));
    const right = rx == null ? null : { x: Math.max(rx, left.x + MIN_POSITION_PX), y: left.y };
    const yT = this.series?.priceToCoordinate(tp.p);
    const yS = this.series?.priceToCoordinate(sl.p);
    if (!left || !right || yT == null || yS == null) return null;
    const xm = (left.x + right.x) / 2;
    return {
      pts: [left, right, { x: xm, y: yT }, { x: xm, y: yS }],
      w,
      h,
    };
  }

  /** Paneside size, read from the chart. */
  private paneSize() {
    const size = this.chart?.paneSize(this.series?.getPane().paneIndex() ?? 0);
    return { w: size?.width ?? 0, h: size?.height ?? 0 };
  }

  findHit(x: number, y: number, tol = 7): Hit | null {
    if (this.state.hidden) return null;
    const { w, h } = this.paneSize();
    const p = { x, y };
    // Topmost (latest) drawing first; the selected one wins ties via handles.
    const own = this.mine();
    const order = [...own].reverse();
    const sel = own.find((d) => d.id === this.state.selectedId);
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
      if ((d.kind === 'vprofile' || d.kind === 'avprofile') && !d.locked) {
        // Either boundary line can be grabbed anywhere along its length and dragged sideways
        // (an anchored profile only has its left one: the right edge is the latest candle).
        const dx = [Math.abs(p.x - g.pts[0]!.x), Math.abs(p.x - g.pts[1]!.x)];
        const nearest = d.kind === 'avprofile' || dx[0]! <= dx[1]! ? 0 : 1;
        if (dx[nearest]! <= tol) return { drawingId: d.id, handle: nearest };
      }
      if (this.bodyHit(d, g, p, tol)) return { drawingId: d.id, handle: 'body' };
    }
    return null;
  }

  private handlePoints(d: Drawing, g: Geometry): (Px | null)[] {
    if (d.kind === 'hline') return [null];
    if (d.kind === 'vline') return [null];
    if (d.kind === 'avprofile') return [g.pts[0]!];
    // A freehand stroke has hundreds of points: it is moved as a whole, never reshaped.
    if (FREEHAND.has(d.kind)) return [];
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
        if (d.kind === 'measure')
          return p.x >= x0 - tol && p.x <= x1 + tol && p.y >= y0 - tol && p.y <= y1 + tol;
        const inside = p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1;
        const nearEdge =
          p.x >= x0 - tol &&
          p.x <= x1 + tol &&
          p.y >= y0 - tol &&
          p.y <= y1 + tol &&
          !(p.x > x0 + tol && p.x < x1 - tol && p.y > y0 + tol && p.y < y1 - tol);
        return inside || nearEdge;
      }
      case 'fib': {
        const fib = fibOf(d);
        const x0 = fib.extendLeft ? -1e5 : Math.min(a.x, b!.x);
        const x1 = fib.extendRight ? 1e5 : Math.max(a.x, b!.x);
        if (fib.trend.on && distToSegment(p, a, b!) <= tol) return true;
        if (p.x < x0 - tol || p.x > x1 + tol) return false;
        return fib.levels.filter((l) => l.on).some((l) => {
          const y = this.series?.priceToCoordinate(fibLevelPrice(d, l.v));
          return y != null && Math.abs(p.y - y) <= tol;
        });
      }
      case 'vprofile':
      case 'avprofile': {
        const profile = this.profileOf(d);
        const bottom = profile ? this.series?.priceToCoordinate(profile.lo) : null;
        if (!profile || bottom == null) return false;
        const x0 = Math.min(a.x, b!.x);
        const x1 = Math.max(a.x, b!.x);
        return p.x >= x0 - tol && p.x <= x1 + tol && p.y >= a.y - tol && p.y <= bottom + tol;
      }
      case 'long':
      case 'short': {
        const [, r, t, st] = g.pts as [Px, Px, Px, Px];
        const x0 = Math.min(a.x, r.x);
        const x1 = Math.max(a.x, r.x);
        const y0 = Math.min(t.y, st.y);
        const y1 = Math.max(t.y, st.y);
        return p.x >= x0 - tol && p.x <= x1 + tol && p.y >= y0 - tol && p.y <= y1 + tol;
      }
      case 'brush':
      case 'highlighter': {
        const reach = d.kind === 'highlighter' ? Math.max(tol, d.width / 2) : tol;
        for (let i = 1; i < g.pts.length; i += 1)
          if (distToSegment(p, g.pts[i - 1]!, g.pts[i]!) <= reach) return true;
        return false;
      }
      case 'arrow':
      case 'arrowmarker':
        return distToSegment(p, a, b!) <= tol;
      case 'arrowup':
      case 'arrowdown': {
        const down = d.kind === 'arrowup' ? 1 : -1;
        return Math.abs(p.x - a.x) <= ARROW_MARK_W / 2 + tol && down * (p.y - a.y) >= -tol && down * (p.y - a.y) <= ARROW_MARK_H + tol;
      }
      case 'circle': {
        const r = Math.sqrt(dist2(a, b!));
        return Math.sqrt(dist2(p, a)) <= r + tol;
      }
      case 'ellipse': {
        const rx = Math.abs(b!.x - a.x) / 2 + tol;
        const ry = Math.abs(b!.y - a.y) / 2 + tol;
        const cx = (a.x + b!.x) / 2;
        const cy = (a.y + b!.y) / 2;
        return ((p.x - cx) / rx) ** 2 + ((p.y - cy) / ry) ** 2 <= 1;
      }
      case 'triangle': {
        const [v0, v1, v2] = triangleVerts(a, b!);
        const side = (u: Px, v: Px) => (p.x - v.x) * (u.y - v.y) - (u.x - v.x) * (p.y - v.y);
        const s0 = side(v0, v1);
        const s1 = side(v1, v2);
        const s2 = side(v2, v0);
        const inside = !((s0 < 0 || s1 < 0 || s2 < 0) && (s0 > 0 || s1 > 0 || s2 > 0));
        return inside || distToSegment(p, v0, v1) <= tol || distToSegment(p, v1, v2) <= tol || distToSegment(p, v2, v0) <= tol;
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
    for (const d of this.mine()) {
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

  private paintOne(
    ctx: CanvasRenderingContext2D,
    d: Drawing,
    g: Geometry,
    selected: boolean,
    hovered: boolean,
  ) {
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
        this.paintFib(ctx, d, a, b!, g.w);
        break;
      case 'measure':
        this.paintMeasure(ctx, d, a, b!);
        break;
      case 'vprofile':
      case 'avprofile':
        this.paintProfile(ctx, d, a, b!, g.w, g.h, selected || hovered);
        break;
      case 'long':
      case 'short':
        this.paintPosition(ctx, d, g, selected || hovered);
        break;
      case 'brush':
      case 'highlighter': {
        ctx.lineJoin = 'round';
        if (d.kind === 'highlighter') {
          ctx.globalAlpha = 0.35;
          ctx.lineCap = 'butt';
        }
        ctx.beginPath();
        g.pts.forEach((q, i) => (i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y)));
        // A stroke that has not moved yet is a dot.
        if (g.pts.length < 2 || g.pts.every((q) => q.x === a.x && q.y === a.y)) ctx.lineTo(a.x + 0.01, a.y);
        ctx.stroke();
        ctx.globalAlpha = 1;
        break;
      }
      case 'arrow': {
        line(a, b!);
        const ang = Math.atan2(b!.y - a.y, b!.x - a.x);
        const len = 8 + d.width * 2;
        ctx.beginPath();
        ctx.moveTo(b!.x, b!.y);
        ctx.lineTo(b!.x - len * Math.cos(ang - 0.45), b!.y - len * Math.sin(ang - 0.45));
        ctx.moveTo(b!.x, b!.y);
        ctx.lineTo(b!.x - len * Math.cos(ang + 0.45), b!.y - len * Math.sin(ang + 0.45));
        ctx.stroke();
        break;
      }
      case 'arrowmarker': {
        // A filled arrow that widens from a point at the tail to a head at the tip.
        const dx = b!.x - a.x;
        const dy = b!.y - a.y;
        const len = Math.hypot(dx, dy);
        if (len < 1) break;
        const ux = dx / len;
        const uy = dy / len;
        const nx = -uy;
        const ny = ux;
        const headLen = Math.min(len * 0.45, 22);
        const headW = Math.min(len * 0.3, 10);
        const bodyW = headW * 0.45;
        const bx = b!.x - ux * headLen;
        const by = b!.y - uy * headLen;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(bx + nx * bodyW, by + ny * bodyW);
        ctx.lineTo(bx + nx * headW, by + ny * headW);
        ctx.lineTo(b!.x, b!.y);
        ctx.lineTo(bx - nx * headW, by - ny * headW);
        ctx.lineTo(bx - nx * bodyW, by - ny * bodyW);
        ctx.closePath();
        ctx.fill();
        break;
      }
      case 'arrowup':
      case 'arrowdown': {
        // The tip sits on the clicked price; the body trails away from it.
        const s = d.kind === 'arrowup' ? 1 : -1;
        const hw = ARROW_MARK_W / 2;
        const neck = ARROW_MARK_H * 0.5;
        const sw = hw * 0.45;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(a.x + hw, a.y + s * neck);
        ctx.lineTo(a.x + sw, a.y + s * neck);
        ctx.lineTo(a.x + sw, a.y + s * ARROW_MARK_H);
        ctx.lineTo(a.x - sw, a.y + s * ARROW_MARK_H);
        ctx.lineTo(a.x - sw, a.y + s * neck);
        ctx.lineTo(a.x - hw, a.y + s * neck);
        ctx.closePath();
        ctx.fill();
        break;
      }
      case 'circle':
      case 'ellipse':
      case 'triangle': {
        ctx.beginPath();
        if (d.kind === 'circle') ctx.arc(a.x, a.y, Math.sqrt(dist2(a, b!)), 0, Math.PI * 2);
        else if (d.kind === 'ellipse')
          ctx.ellipse((a.x + b!.x) / 2, (a.y + b!.y) / 2, Math.abs(b!.x - a.x) / 2, Math.abs(b!.y - a.y) / 2, 0, 0, Math.PI * 2);
        else {
          const [v0, v1, v2] = triangleVerts(a, b!);
          ctx.moveTo(v0.x, v0.y);
          ctx.lineTo(v1.x, v1.y);
          ctx.lineTo(v2.x, v2.y);
          ctx.closePath();
        }
        ctx.globalAlpha = 0.15;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.stroke();
        break;
      }
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

  /** TradingView's long / short position: profit and stop zones either side of the entry, three labels. */
  private paintPosition(ctx: CanvasRenderingContext2D, d: Drawing, g: Geometry, emphasis: boolean) {
    const [a, b, t, s] = g.pts as [Px, Px, Px, Px];
    const decimals = this.state.decimals;
    const st = positionStats(d, this.state.bars, 10 ** -decimals, positionEnd(d, this.state.axis));
    const x0 = Math.min(a.x, b.x);
    const x1 = Math.max(a.x, b.x);
    const yE = a.y;

    ctx.globalAlpha = 0.22;
    ctx.fillStyle = POSITION_PROFIT;
    ctx.fillRect(x0, Math.min(yE, t.y), x1 - x0, Math.abs(t.y - yE));
    ctx.fillStyle = POSITION_STOP;
    ctx.fillRect(x0, Math.min(yE, s.y), x1 - x0, Math.abs(s.y - yE));
    ctx.globalAlpha = 1;

    ctx.lineWidth = emphasis ? 2 : 1;
    ctx.setLineDash([]);
    const hline = (y: number, color: string) => {
      ctx.strokeStyle = color;
      ctx.beginPath();
      ctx.moveTo(x0, y);
      ctx.lineTo(x1, y);
      ctx.stroke();
    };
    hline(t.y, POSITION_PROFIT);
    hline(s.y, POSITION_STOP);
    hline(yE, '#9598a1');

    // Labels stay readable when the box is partly scrolled off either edge.
    const centre = (bw: number) => {
      const lo = Math.max(x0, 0) + bw / 2;
      const hi = Math.min(x1, g.w) - bw / 2;
      return lo <= hi ? Math.min(hi, Math.max(lo, (x0 + x1) / 2)) : (x0 + x1) / 2;
    };
    const pill = (lines: string[], fill: string, y: number, border?: string) => {
      ctx.font = `12px ${FONT}`;
      const bw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 16;
      const bh = lines.length * 15 + 8;
      const cx = centre(bw);
      ctx.fillStyle = fill;
      ctx.beginPath();
      ctx.roundRect(cx - bw / 2, y, bw, bh, 4);
      ctx.fill();
      if (border) {
        ctx.strokeStyle = border;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      lines.forEach((l, i) => ctx.fillText(l, cx, y + 4 + 7.5 + i * 15));
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
      return bh;
    };
    const money = (v: number) => String(Number(v.toFixed(2)));
    const level = (name: string, dist: number, pct: number, ticks: number) =>
      `${name}: ${dist.toFixed(decimals)} (${pct.toFixed(3)}%) ${ticks}, Amount: ${st.qty}`;

    const tLines = [level('Target', st.reward, st.targetPct, st.targetTicks)];
    const sLines = [level('Stop', st.risk, st.stopPct, st.stopTicks)];
    // Outside the zones: above a long's target and below its stop, mirrored for a short.
    pill(tLines, POSITION_PROFIT, st.isLong ? t.y - 26 : t.y + 2);
    pill(sLines, POSITION_STOP, st.isLong ? s.y + 2 : s.y - 26);
    // Result pill inside the profit zone, hugging the entry line.
    const verb = st.status === 'open' ? 'Open' : 'Closed';
    const centreLines = [
      `${verb} PnL: ${money(st.pnl)}, Qty: ${st.qty}`,
      `Risk/reward ratio: ${st.rr.toFixed(2)}`,
    ];
    const ph = centreLines.length * 15 + 8;
    pill(
      centreLines,
      st.pnl >= 0 ? POSITION_PROFIT : POSITION_STOP,
      st.isLong ? yE - ph - 6 : yE + 6,
      '#ffffff',
    );
  }

  private paintProfile(
    ctx: CanvasRenderingContext2D,
    d: Drawing,
    a: Px,
    b: Px,
    paneW: number,
    paneH: number,
    emphasis: boolean,
  ) {
    const series = this.series;
    const profile = this.profileOf(d);
    if (!series || !profile) return;
    const x0 = Math.min(a.x, b.x);
    const x1 = Math.max(a.x, b.x);
    const y = (price: number) => series.priceToCoordinate(price);
    const yTop = y(profile.hi);
    const yBottom = y(profile.lo);
    if (yTop == null || yBottom == null) return;

    const vp = vpOf(d);
    // The span of candles the profile covers: a faint box, with edges only while hovered or selected.
    if (vp.showBackground) {
      ctx.fillStyle = withAlpha(vp.backgroundColor, vp.backgroundOpacity);
      ctx.fillRect(x0, yTop, x1 - x0, yBottom - yTop);
    }
    if (emphasis) {
      ctx.strokeStyle = 'rgba(41, 98, 255, 0.95)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (const x of [x0, x1]) {
        ctx.moveTo(Math.round(x) + 0.5, 0);
        ctx.lineTo(Math.round(x) + 0.5, paneH);
      }
      ctx.stroke();
    }

    // Histogram grows from the chosen edge; up volume then down volume (or one colour for total), value area brighter.
    // Anchored: 'right' pins the histogram to the pane's right edge, sized off the pane; otherwise it hangs off the box.
    const pinned = d.kind === 'avprofile' && vp.placement === 'right';
    const maxLen = (((pinned ? paneW : x1 - x0)) * vp.width) / 100;
    const fromRight = vp.placement === 'right';
    profile.rows.forEach((row, i) => {
      const top = y(row.price + profile.step);
      const bottom = y(row.price);
      if (top == null || bottom == null) return;
      const total = row.up + row.down;
      if (total <= 0 || profile.maxRow <= 0) return;
      const h = Math.max(1, Math.abs(bottom - top) - 1);
      const yy = Math.min(top, bottom);
      const len = (maxLen * total) / profile.maxRow;
      const upLen = vp.volume === 'total' ? len : (len * row.up) / total;
      const inValue = i >= profile.vaLo && i <= profile.vaHi;
      const alpha = inValue ? vp.opacity : vp.opacity * 0.45;
      const edge = pinned ? paneW : fromRight ? x1 : x0;
      const sign = fromRight ? -1 : 1;
      const seg = (from: number, length: number) => {
        const sx = edge + sign * from;
        ctx.fillRect(fromRight ? sx - length : sx, yy, length, h);
      };
      ctx.fillStyle = withAlpha(inValue ? vp.vaUpColor : vp.upColor, alpha);
      seg(0, upLen);
      if (vp.volume !== 'total') {
        ctx.fillStyle = withAlpha(inValue ? vp.vaDownColor : vp.downColor, alpha);
        seg(upLen, len - upLen);
      }
    });

    // Point of control and value-area edges: lines across the box, labelled only if asked.
    const decimals = this.state.decimals;
    ctx.font = `10px ${FONT}`;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'bottom';
    const level = (price: number, label: string, color: string, lineWidth: number) => {
      const ly = y(price);
      if (ly == null) return;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.beginPath();
      // Anchored: the levels run the whole pane, from the anchor on (TradingView), not just across the box.
      const lineTo = d.kind === 'avprofile' ? paneW : x1;
      ctx.moveTo(x0, ly);
      ctx.lineTo(lineTo, ly);
      ctx.stroke();
      if (vp.showLabels) ctx.fillText(`${label} ${price.toFixed(decimals)}`, x1 - 4, ly - 2);
    };
    if (vp.showVa) {
      level(profile.vah, 'VAH', vp.vaLineColor, 1);
      level(profile.val, 'VAL', vp.vaLineColor, 1);
    }
    if (vp.showPoc) level(profile.pocPrice, 'POC', vp.pocColor, vp.pocWidth);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  private paintFib(ctx: CanvasRenderingContext2D, d: Drawing, a: Px, b: Px, w: number) {
    const fib = fibOf(d);
    const x0 = fib.extendLeft ? 0 : Math.min(a.x, b.x);
    const x1 = fib.extendRight ? w : Math.max(a.x, b.x);
    const levels: { l: (typeof fib.levels)[number]; y: number }[] = [];
    for (const l of fib.levels) {
      if (!l.on) continue;
      const y = this.series?.priceToCoordinate(fibLevelPrice(d, l.v));
      if (y != null) levels.push({ l, y: y as number });
    }
    levels.sort((p, q) => p.l.v - q.l.v);
    ctx.font = `11px ${FONT}`;
    ctx.textBaseline = 'middle';
    if (fib.background.on) {
      for (let i = 0; i + 1 < levels.length; i += 1) {
        const cur = levels[i]!;
        const next = levels[i + 1]!;
        ctx.globalAlpha = fib.background.opacity;
        ctx.fillStyle = next.l.color;
        ctx.fillRect(x0, Math.min(cur.y, next.y), x1 - x0, Math.abs(next.y - cur.y));
      }
      ctx.globalAlpha = 1;
    }
    for (const { l, y } of levels) {
      ctx.strokeStyle = l.color;
      ctx.lineWidth = fib.lines.width;
      ctx.setLineDash(lineDash(fib.lines.style, fib.lines.width));
      ctx.beginPath();
      ctx.moveTo(x0, y);
      ctx.lineTo(x1, y);
      ctx.stroke();
      ctx.setLineDash([]);
      if (!fib.labels.show) continue;
      const parts: string[] = [];
      parts.push(fib.labels.percent ? `${+(l.v * 100).toFixed(1)}%` : `${l.v}`);
      if (fib.labels.price) parts.push(`(${this.fmt(fibLevelPrice(d, l.v))})`);
      ctx.fillStyle = l.color;
      const left = fib.labels.side === 'left';
      ctx.textAlign = left ? 'right' : 'left';
      ctx.fillText(parts.join(' '), left ? x0 - 6 : x1 + 6, y);
    }
    ctx.textAlign = 'left';
    if (fib.trend.on) {
      ctx.setLineDash(lineDash(fib.trend.style, fib.trend.width));
      ctx.strokeStyle = fib.trend.color;
      ctx.lineWidth = fib.trend.width;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      ctx.setLineDash([]);
    }
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
      `${dp >= 0 ? '+' : ''}${this.fmt(dp)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`,
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
