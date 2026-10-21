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
import { DEFAULT_ROWS, type VolumeProfile, buildProfile } from '../../lib/tv/volume-profile';
import { type Drawing, FIB_LEVELS, type Pt, type TimeAxis, formatDuration } from '../../lib/tv/drawings';
import {
  POSITION_PROFIT,
  POSITION_STOP,
  isPosition,
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
    bars: [],
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
      const levels: { price: number; color: string }[] = [];
      if (d.kind === 'hline' || d.kind === 'hray') levels.push({ price: d.pts[0]!.p, color: d.color });
      else if (d.id === this.state.selectedId && (d.kind === 'trend' || d.kind === 'ray'))
        for (const pt of d.pts) levels.push({ price: pt.p, color: d.color });
      else if (d.id === this.state.selectedId && isPosition(d.kind)) {
        levels.push({ price: d.pts[2]!.p, color: POSITION_PROFIT });
        levels.push({ price: d.pts[0]!.p, color: '#787b86' });
        levels.push({ price: d.pts[3]!.p, color: POSITION_STOP });
      }
      for (const { price, color } of levels) {
        const y = this.series.priceToCoordinate(price);
        if (y == null) continue;
        this.axisViews.push({
          coordinate: () => y,
          text: () => price.toFixed(this.state.decimals),
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

  private profiles = new Map<string, { key: string; profile: VolumeProfile | null }>();

  /** Volume profile over the candles between a profile drawing's two anchors (cached until the data or range changes). */
  private profileOf(d: Drawing): VolumeProfile | null {
    const bars = this.state.bars;
    if (!bars.length || d.pts.length < 2) return null;
    const a = Math.round(this.state.axis.toLogical(d.pts[0]!.t));
    const b = Math.round(this.state.axis.toLogical(d.pts[1]!.t));
    const rows = d.rows ?? DEFAULT_ROWS;
    const last = bars[bars.length - 1]!;
    const key = `${a}|${b}|${rows}|${bars.length}|${last.volume}|${last.high}|${last.low}`;
    const hit = this.profiles.get(d.id);
    if (hit?.key === key) return hit.profile;
    const profile = buildProfile(bars, a, b, rows);
    this.profiles.set(d.id, { key, profile });
    return profile;
  }

  private geometry(d: Drawing, w: number, h: number): Geometry | null {
    if (isPosition(d.kind)) return this.positionGeometry(d, w, h);
    const pts: Px[] = [];
    for (const pt of d.pts) {
      const px = this.px(pt.t, pt.p);
      if (!px) return null;
      pts.push(px);
    }
    if (d.kind === 'vprofile') {
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
    const right = this.px(b.t, b.p);
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
      if (d.kind === 'vprofile' && !d.locked) {
        // Either boundary line can be grabbed anywhere along its length and dragged sideways.
        const dx = [Math.abs(p.x - g.pts[0]!.x), Math.abs(p.x - g.pts[1]!.x)];
        const nearest = dx[0]! <= dx[1]! ? 0 : 1;
        if (dx[nearest]! <= tol) return { drawingId: d.id, handle: nearest };
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
        const x0 = Math.min(a.x, b!.x);
        const x1 = Math.max(a.x, b!.x);
        if (distToSegment(p, a, b!) <= tol) return true;
        if (p.x < x0 - tol || p.x > x1 + tol) return false;
        return FIB_LEVELS.some((l) => {
          const y = this.series?.priceToCoordinate(fibLevelPrice(d, l.v));
          return y != null && Math.abs(p.y - y) <= tol;
        });
      }
      case 'vprofile': {
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
        this.paintFib(ctx, d, a, b!);
        break;
      case 'measure':
        this.paintMeasure(ctx, d, a, b!);
        break;
      case 'vprofile':
        this.paintProfile(ctx, d, a, b!, g.h, selected || hovered);
        break;
      case 'long':
      case 'short':
        this.paintPosition(ctx, d, g, selected || hovered);
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

  /** TradingView's long / short position: profit and stop zones either side of the entry, three labels. */
  private paintPosition(ctx: CanvasRenderingContext2D, d: Drawing, g: Geometry, emphasis: boolean) {
    const [a, b, t, s] = g.pts as [Px, Px, Px, Px];
    const decimals = this.state.decimals;
    const st = positionStats(d, this.state.bars, 10 ** -decimals);
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

    // The span of candles the profile covers.
    ctx.fillStyle = 'rgba(41, 98, 255, 0.06)';
    ctx.fillRect(x0, yTop, x1 - x0, yBottom - yTop);
    ctx.strokeStyle = emphasis ? 'rgba(41, 98, 255, 0.95)' : 'rgba(120, 123, 134, 0.7)';
    ctx.lineWidth = emphasis ? 2 : 1;
    ctx.setLineDash(emphasis ? [] : [4, 4]);
    ctx.beginPath();
    for (const x of [x0, x1]) {
      ctx.moveTo(Math.round(x) + 0.5, 0);
      ctx.lineTo(Math.round(x) + 0.5, paneH);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // Histogram grows rightward from the left anchor; up volume then down volume, value area stronger.
    const maxLen = (x1 - x0) * 0.7;
    profile.rows.forEach((row, i) => {
      const top = y(row.price + profile.step);
      const bottom = y(row.price);
      if (top == null || bottom == null) return;
      const total = row.up + row.down;
      if (total <= 0 || profile.maxRow <= 0) return;
      const h = Math.max(1, Math.abs(bottom - top) - 1);
      const len = (maxLen * total) / profile.maxRow;
      const upLen = (len * row.up) / total;
      const inValue = i >= profile.vaLo && i <= profile.vaHi;
      ctx.fillStyle = inValue ? 'rgba(41, 98, 255, 0.55)' : 'rgba(41, 98, 255, 0.28)';
      ctx.fillRect(x0, Math.min(top, bottom), upLen, h);
      ctx.fillStyle = inValue ? 'rgba(251, 192, 45, 0.6)' : 'rgba(251, 192, 45, 0.3)';
      ctx.fillRect(x0 + upLen, Math.min(top, bottom), len - upLen, h);
    });

    // Point of control and value-area edges, labelled at the right end.
    const decimals = this.state.decimals;
    ctx.font = `10px ${FONT}`;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'bottom';
    const level = (price: number, label: string, color: string, dashed: boolean) => {
      const ly = y(price);
      if (ly == null) return;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = dashed ? 1 : 1.5;
      ctx.setLineDash(dashed ? [4, 3] : []);
      ctx.beginPath();
      ctx.moveTo(x0, ly);
      ctx.lineTo(x1, ly);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillText(`${label} ${price.toFixed(decimals)}`, x1 - 4, ly - 2);
    };
    level(profile.vah, 'VAH', 'rgba(41, 98, 255, 0.95)', true);
    level(profile.val, 'VAL', 'rgba(41, 98, 255, 0.95)', true);
    level(profile.pocPrice, 'POC', '#f23645', false);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
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
      ctx.fillText(
        `${level.v} (${fibLevelPrice(d, level.v).toFixed(this.state.decimals)})`,
        x0 - 6,
        y,
      );
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
