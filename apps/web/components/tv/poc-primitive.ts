import type {
  AutoscaleInfo,
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts';
import { logicalX } from './coords';

/** Amber: a colour no other overlay uses (SVP blue/yellow/red, FVG green/red, delta magenta). Direction is in the labels. */
const ACCENT = '255, 167, 38';
const HATCH_GAP = 7;
const FOCUS_MS = 2600;
const RING_MS = 850;
const RING_REACH = 30;
const FONT = '600 11px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif';

export interface PocMark {
  /** Chart bar where the previous session (the profile's source) starts: the zone is drawn from here. */
  fromIndex: number;
  /** Chart bars of the move away from the zone and of the first touch back, when they happened. */
  departIndex: number | null;
  touchIndex: number | null;
  /** Chart bar of the furthest price after moving away, and that price. */
  extremeIndex: number | null;
  extreme: number | null;
  /** Sessions back the profile comes from (1 = yesterday). */
  sessionsAgo: number | null;
  /** Last chart bar: an untouched setup's column runs from the move away to here. */
  lastIndex: number;
  zoneLo: number;
  zoneHi: number;
  poc: number;
  entry: number | null;
  stop: number | null;
  /** null before price has left the zone. */
  long: boolean | null;
  /**
   * The setup being traded: hatched zone, entry, stop and markers. Without it the zone is a quiet level (another
   * untested POC on the indicator) with just its outline, POC line and a tag.
   */
  primary?: boolean;
  /** Short state for a quiet level's tag ("pulling back", "touched"…). */
  stage?: string;
  /** Last chart bar the zone runs to; null = to the right edge (still untested). */
  endIndex?: number | null;
}

/** A tracked setup's trade, in chart bars: drawn over the pick as a position box from the fill to the exit. */
export interface PocTradeMark {
  long: boolean;
  /** "stopped", "target", "closed" or "open". */
  outcome: string;
  fillIndex: number;
  fillPrice: number;
  /** The exit's bar; while still open, the last bar (with `exitPrice` the last close). */
  exitIndex: number;
  exitPrice: number;
  stop: number;
  target: number;
  r: number | null;
  /** Clock times for the labels, already formatted ("09:20"). */
  fillAt: string;
  exitAt: string | null;
}

const WIN = '38, 166, 154';
const LOSS = '239, 83, 80';

const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(3));

/**
 * The POC scanner's pick: a recent session's untested POC zone as a box running to the right edge, the POC as a dashed
 * midline, the entry edge and stop as labelled lines, a marker where price moved away and a ring at the first touch.
 * Drawn in one amber accent with hatching and dashes so it never reads as another indicator; a fresh pick pulses.
 */
export class PocPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private marks: PocMark[] = [];
  private trade: PocTradeMark | null = null;
  private pulseStart = 0;
  private pulsing = false;
  private raf = 0;
  /**
   * Fills on the `normal` layer: above the `bottom` overlays (session volume profile bars, FVG zones) yet under the
   * candles, so the candles stay readable. Lines and labels on `top`, so nothing hides them.
   */
  private layer = (z: 'normal' | 'top'): IPrimitivePaneView => ({
    zOrder: () => z,
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) =>
          this.paint(context, mediaSize.width, mediaSize.height, z === 'top'),
        );
      },
    }),
  });
  private views = [this.layer('normal'), this.layer('top')];

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
    return this.views;
  }

  /** Auto-scale keeps the whole zone, entry and stop in view, not only the candles. */
  autoscaleInfo(): AutoscaleInfo | null {
    // only the traded setup: a quiet level far away must not squash the candles
    const m = this.marks.find((k) => k.primary);
    if (!m) return null;
    const t = this.trade;
    const prices = [
      m.zoneLo,
      m.zoneHi,
      m.poc,
      m.entry,
      m.stop,
      t?.fillPrice,
      t?.exitPrice,
      t?.stop,
    ].filter((p): p is number => p != null);
    return {
      priceRange: { minValue: Math.min(...prices), maxValue: Math.max(...prices) },
      // room for the legend above and the labels below
      margins: { above: 40, below: 24 },
    };
  }

  /** One setup (the scanner's pick), drawn in full. */
  setState(mark: PocMark | null) {
    this.setMarks(mark ? [{ ...mark, primary: true }] : []);
  }

  /** Several levels; the ones flagged `primary` are drawn in full, the rest quietly. */
  setMarks(marks: PocMark[]) {
    this.marks = marks;
    this.requestUpdate?.();
  }

  /** The tracked trade on top of the pick; null removes it. */
  setTrade(trade: PocTradeMark | null) {
    this.trade = trade;
    this.requestUpdate?.();
  }

  pulse() {
    cancelAnimationFrame(this.raf);
    this.pulsing = true;
    this.pulseStart = performance.now();
    this.tick();
  }

  private tick = () => {
    this.requestUpdate?.();
    if (performance.now() - this.pulseStart < FOCUS_MS) {
      this.raf = requestAnimationFrame(this.tick);
    } else {
      this.pulsing = false;
      this.requestUpdate?.();
    }
  };

  /** A dark tag with an accent outline: readable on any background, never mistaken for a price-scale label. */
  private label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number) {
    ctx.font = FONT;
    const w = ctx.measureText(text).width + 10;
    ctx.fillStyle = 'rgba(19, 23, 34, 0.92)';
    ctx.fillRect(x - w, y - 9, w, 18);
    ctx.strokeStyle = `rgb(${ACCENT})`;
    ctx.lineWidth = 1;
    ctx.strokeRect(x - w + 0.5, y - 8.5, w - 1, 17);
    ctx.fillStyle = `rgb(${ACCENT})`;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x - 5, y + 0.5);
  }

  /** Diagonal hatching inside a rectangle: marks the zone without covering what is under it. */
  private hatch(
    ctx: CanvasRenderingContext2D,
    x: number,
    y: number,
    w: number,
    h: number,
    alpha: number,
  ) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    ctx.strokeStyle = `rgba(${ACCENT}, ${alpha.toFixed(3)})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let k = x - h; k < x + w; k += HATCH_GAP) {
      ctx.moveTo(k, y + h);
      ctx.lineTo(k + h, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  private paint(ctx: CanvasRenderingContext2D, width: number, height: number, over: boolean) {
    // quiet levels first, so the traded setup's labels sit on top
    for (const m of this.marks) if (!m.primary) this.paintLevel(ctx, m, width, over);
    for (const m of this.marks) if (m.primary) this.paintSetup(ctx, m, width, height, over);
    if (this.trade) this.paintTrade(ctx, this.trade, width, over);
  }

  /**
   * The trade: under the candles, the risk (fill → stop, red) and reward (fill → target, green) bands faintly, and
   * the realised part (fill → exit) stronger in the result's colour, all from the fill bar to the exit bar. On top,
   * the fill and exit as markers with their price and time.
   */
  private paintTrade(ctx: CanvasRenderingContext2D, t: PocTradeMark, width: number, over: boolean) {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    const half = (chart.timeScale().options().barSpacing ?? 8) / 2;
    const xa = logicalX(chart, t.fillIndex);
    const xb = logicalX(chart, t.exitIndex);
    const yFill = series.priceToCoordinate(t.fillPrice);
    const yExit = series.priceToCoordinate(t.exitPrice);
    const yStop = series.priceToCoordinate(t.stop);
    const yTarget = series.priceToCoordinate(t.target);
    if (xa == null || xb == null || yFill == null || yExit == null) return;
    const x0 = xa - half;
    const x1 = Math.max(x0 + 6, xb + half);
    if (x1 < 0 || x0 > width) return;
    const won = (t.r ?? 0) >= 0;
    const tone = won ? WIN : LOSS;
    const box = (a: number, b: number, rgb: string, alpha: number) => {
      ctx.fillStyle = `rgba(${rgb}, ${alpha})`;
      ctx.fillRect(x0, Math.min(a, b), x1 - x0, Math.abs(b - a));
    };

    ctx.save();
    if (!over) {
      if (yStop != null) box(yFill, yStop, LOSS, 0.1);
      if (yTarget != null) box(yFill, yTarget, WIN, 0.08);
      box(yFill, yExit, tone, 0.28);
      ctx.restore();
      return;
    }
    // outline of the realised part, and the entry level across the trade
    ctx.strokeStyle = `rgba(${tone}, 0.9)`;
    ctx.lineWidth = 1;
    ctx.strokeRect(x0 + 0.5, Math.min(yFill, yExit) + 0.5, x1 - x0 - 1, Math.abs(yExit - yFill));
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = 'rgba(209, 212, 220, 0.8)';
    ctx.beginPath();
    ctx.moveTo(x0, yFill);
    ctx.lineTo(x1, yFill);
    ctx.stroke();
    ctx.setLineDash([]);

    const tag = (text: string, x: number, y: number, rgb: string, above: boolean) => {
      ctx.font = FONT;
      const w = ctx.measureText(text).width + 10;
      const bx = Math.max(2, Math.min(x - w / 2, width - w - 2));
      const by = above ? y - 26 : y + 8;
      ctx.fillStyle = 'rgba(19, 23, 34, 0.92)';
      ctx.fillRect(bx, by, w, 18);
      ctx.strokeStyle = `rgb(${rgb})`;
      ctx.strokeRect(bx + 0.5, by + 0.5, w - 1, 17);
      ctx.fillStyle = `rgb(${rgb})`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, bx + 5, by + 9.5);
    };
    const dot = (x: number, y: number, rgb: string) => {
      ctx.beginPath();
      ctx.arc(x, y, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = `rgb(${rgb})`;
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = 'rgba(19, 23, 34, 1)';
      ctx.stroke();
    };
    // labels go on the side away from the move, so the box stays readable
    const up = t.exitPrice >= t.fillPrice;
    dot(xa, yFill, '209, 212, 220');
    tag(
      `${t.long ? '▲ Bought' : '▼ Sold'} ${fmt(t.fillPrice)} · ${t.fillAt}`,
      xa,
      yFill,
      '209, 212, 220',
      !up,
    );
    const name =
      t.outcome === 'stopped'
        ? 'Stopped'
        : t.outcome === 'target'
          ? 'Target'
          : t.outcome === 'closed'
            ? 'Closed'
            : 'Now';
    const r = t.r == null ? '' : ` ${t.r >= 0 ? '+' : '−'}${Math.abs(t.r).toFixed(1)}R`;
    dot(xb, yExit, tone);
    tag(`${name} ${fmt(t.exitPrice)}${t.exitAt ? ` · ${t.exitAt}` : ''}${r}`, xb, yExit, tone, up);
    ctx.restore();
  }

  /** Another untested POC: a light tint, a thin dashed outline, the POC line and a tag naming its age and state. */
  private paintLevel(ctx: CanvasRenderingContext2D, m: PocMark, width: number, over: boolean) {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    const half = (chart.timeScale().options().barSpacing ?? 8) / 2;
    const xa = logicalX(chart, m.fromIndex);
    const xb = m.endIndex == null ? width : logicalX(chart, m.endIndex);
    const yt = series.priceToCoordinate(m.zoneHi);
    const yb = series.priceToCoordinate(m.zoneLo);
    const yp = series.priceToCoordinate(m.poc);
    if (xa == null || xb == null || yt == null || yb == null || yp == null) return;
    const x0 = Math.max(-2, xa - half);
    const x1 = Math.min(width, m.endIndex == null ? width : xb + half);
    if (x0 > width || x1 <= x0) return;
    const ended = m.endIndex != null;
    ctx.save();
    if (!over) {
      ctx.fillStyle = `rgba(${ACCENT}, ${ended ? 0.04 : 0.08})`;
      ctx.fillRect(x0, yt, x1 - x0, yb - yt);
      ctx.restore();
      return;
    }
    ctx.strokeStyle = `rgba(${ACCENT}, ${ended ? 0.3 : 0.55})`;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.strokeRect(x0 + 0.5, yt + 0.5, x1 - x0, yb - yt - 1);
    ctx.setLineDash([1, 3]);
    ctx.beginPath();
    ctx.moveTo(x0, yp);
    ctx.lineTo(x1, yp);
    ctx.stroke();
    ctx.setLineDash([]);
    if (x1 - x0 > 90) {
      const age =
        m.sessionsAgo == null ? '' : m.sessionsAgo === 1 ? ' · 1d' : ` · ${m.sessionsAgo}d`;
      const text = `POC ${fmt(m.poc)}${age}${m.stage ? ` · ${m.stage}` : ''}`;
      ctx.font = FONT;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillStyle = `rgba(${ACCENT}, ${ended ? 0.5 : 0.85})`;
      ctx.fillText(text, Math.max(x0 + 6, width * 0.3), yp - 2);
    }
    ctx.restore();
  }

  private paintSetup(
    ctx: CanvasRenderingContext2D,
    m: PocMark,
    width: number,
    height: number,
    over: boolean,
  ) {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;
    const half = (chart.timeScale().options().barSpacing ?? 8) / 2;
    const xa = logicalX(chart, m.fromIndex);
    const yt = series.priceToCoordinate(m.zoneHi);
    const yb = series.priceToCoordinate(m.zoneLo);
    const yp = series.priceToCoordinate(m.poc);
    if (xa == null || yt == null || yb == null || yp == null) return;
    const x0 = Math.max(-2, xa - half);
    if (x0 > width) return;
    const glow = this.pulsing
      ? Math.max(0, 1 - (performance.now() - this.pulseStart) / FOCUS_MS)
      : 0;
    const h = yb - yt;
    const side = m.long == null ? '' : m.long ? '▲ Long ' : '▼ Short ';

    ctx.save();
    // the setup's time window: a faint column with dashed edges from the move away to the first touch (or to now),
    // brighter while the pick pulses
    if (m.departIndex != null) {
      const xs = logicalX(chart, m.departIndex);
      const xe = logicalX(chart, m.touchIndex ?? m.lastIndex);
      if (xs != null && xe != null) {
        const c0 = xs - half;
        const c1 = Math.max(c0 + 2, xe + half);
        if (!over) {
          ctx.fillStyle = `rgba(${ACCENT}, ${(0.07 + 0.15 * glow).toFixed(3)})`;
          ctx.fillRect(c0, 0, c1 - c0, height);
        } else {
          ctx.strokeStyle = `rgba(${ACCENT}, ${(0.5 + 0.4 * glow).toFixed(3)})`;
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(c0 + 0.5, 0);
          ctx.lineTo(c0 + 0.5, height);
          ctx.moveTo(c1 - 0.5, 0);
          ctx.lineTo(c1 - 0.5, height);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }
    // the zone: under the candles a solid amber tint plus hatching, so it reads even behind volume-profile bars
    if (!over) {
      ctx.fillStyle = `rgba(${ACCENT}, ${(0.05 + 0.12 * glow).toFixed(3)})`;
      ctx.fillRect(x0, yt, width - x0, h);
      this.hatch(ctx, x0, yt, width - x0, h, 0.14 + 0.3 * glow);
      ctx.restore();
      return;
    }
    // over everything: the outline, lines, labels and markers; kept soft so what is behind stays readable
    ctx.strokeStyle = `rgba(${ACCENT}, ${(0.4 + 0.5 * glow).toFixed(3)})`;
    ctx.lineWidth = glow > 0 ? 2.5 : 1;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(x0 + 0.5, yt + 0.5, width - x0 + 2, h - 1);
    // POC: dotted
    ctx.setLineDash([1, 3]);
    ctx.strokeStyle = `rgba(${ACCENT}, ${(0.55 + 0.4 * glow).toFixed(3)})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x0, yp);
    ctx.lineTo(width, yp);
    ctx.stroke();
    ctx.setLineDash([]);
    if (width - x0 > 90) {
      ctx.font = FONT;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillStyle = `rgba(${ACCENT}, 0.8)`;
      const age =
        m.sessionsAgo == null
          ? ''
          : m.sessionsAgo === 1
            ? ' · yesterday'
            : ` · ${m.sessionsAgo} sessions ago`;
      // when the zone starts off-screen the label would sit under the legend (top-left): start it further in
      ctx.fillText(`Untested POC ${fmt(m.poc)}${age}`, Math.max(x0 + 6, width * 0.3), yp - 3);
    }

    // entry solid, stop dashed: one colour, told apart by style and label
    const lines: [number | null, string, number[]][] = [
      [m.entry, `${side}entry`, []],
      [m.stop, 'Stop', [6, 4]],
    ];
    for (const [price, name, dash] of lines) {
      if (price == null) continue;
      const y = series.priceToCoordinate(price);
      if (y == null) continue;
      ctx.strokeStyle = `rgba(${ACCENT}, 0.65)`;
      ctx.lineWidth = 1;
      ctx.setLineDash(dash);
      ctx.beginPath();
      ctx.moveTo(x0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      ctx.setLineDash([]);
      this.label(ctx, `${name} ${fmt(price)}`, width - 4, y);
    }

    // where price moved away: named at the foot of the pane (the legend owns the top)
    if (m.departIndex != null) {
      const x = logicalX(chart, m.departIndex);
      if (x != null && x >= 0 && x <= width) {
        ctx.font = FONT;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillStyle = `rgb(${ACCENT})`;
        // kept inside the pane: on a narrow chart the setup sits against the right edge
        const tw = ctx.measureText('Moved away').width;
        ctx.fillText('Moved away', Math.max(4, Math.min(x - half + 4, width - tw - 4)), height - 6);
      }
    }

    // the furthest point of the move away: where the pullback starts from
    if (m.extremeIndex != null && m.extreme != null && m.long != null) {
      const x = logicalX(chart, m.extremeIndex);
      const y = series.priceToCoordinate(m.extreme);
      if (x != null && y != null && x >= -20 && x <= width + 20) {
        const dir = m.long ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(x, y + dir * 4);
        ctx.lineTo(x - 6, y + dir * 13);
        ctx.lineTo(x + 6, y + dir * 13);
        ctx.closePath();
        ctx.fillStyle = `rgb(${ACCENT})`;
        ctx.fill();
        ctx.font = FONT;
        ctx.textAlign = 'center';
        ctx.textBaseline = m.long ? 'bottom' : 'top';
        const text = 'Furthest';
        const tw = ctx.measureText(text).width;
        const tx = Math.max(tw / 2 + 4, Math.min(x, width - tw / 2 - 4));
        ctx.fillText(text, tx, y + dir * 16);
      }
    }

    // the first touch: a diamond on the entry edge, with spreading rings while the pick pulses
    if (m.touchIndex != null && m.entry != null) {
      const x = logicalX(chart, m.touchIndex);
      const y = series.priceToCoordinate(m.entry);
      if (x != null && y != null && x >= -20 && x <= width + 20) {
        if (glow > 0) {
          const t = performance.now() - this.pulseStart;
          for (let k = 0; k < 2; k += 1) {
            const phase = ((t + k * (RING_MS / 2)) % RING_MS) / RING_MS;
            ctx.beginPath();
            ctx.arc(x, y, 8 + phase * RING_REACH, 0, Math.PI * 2);
            ctx.lineWidth = 2;
            ctx.strokeStyle = `rgba(${ACCENT}, ${((1 - phase) * glow).toFixed(3)})`;
            ctx.stroke();
          }
        }
        ctx.beginPath();
        ctx.moveTo(x, y - 7);
        ctx.lineTo(x + 7, y);
        ctx.lineTo(x, y + 7);
        ctx.lineTo(x - 7, y);
        ctx.closePath();
        ctx.fillStyle = 'rgba(19, 23, 34, 0.92)';
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = `rgb(${ACCENT})`;
        ctx.stroke();
        ctx.font = FONT;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = `rgb(${ACCENT})`;
        const tw = ctx.measureText('First touch').width;
        const tx = Math.max(tw / 2 + 4, Math.min(x, width - tw / 2 - 4));
        ctx.fillText('First touch', tx, y + (m.long ? 11 : -25));
      }
    }
    ctx.restore();
  }
}
