import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts';
import type { AvrDay, Pivot, RangeFib, SessionBox, StructureLabel } from '../../lib/tv/ar';
import type { ArSkip, ArTrade } from '../../lib/tv/ar-strategy';
import type { StackZone } from '../../lib/tv/stacked-imbalance';
import { logicalX } from './coords';

/** What the AR overlays draw; each part is empty when its indicator is off. */
export interface ArDrawState {
  sessions: SessionBox[];
  avr: AvrDay[];
  fib: RangeFib | null;
  /** Index of the last candle (zone lines run to it, then on to the right edge). */
  lastIndex: number;
  swings: { pivot: Pivot; label: StructureLabel | null }[];
  /** Strategy: trades (entry → exit with stop and target boxes) and setups it skipped. */
  trades: ArTrade[];
  skips: ArSkip[];
  /** Stacked-imbalance zones; traded-through ones are drawn only when `stackUsed`. */
  stacks: StackZone[];
  stackUsed: boolean;
  /** Trade to emphasise (hovered in the panel). */
  focusTrade: number | null;
  decimals: number;
}

const EMPTY: ArDrawState = {
  sessions: [],
  avr: [],
  fib: null,
  lastIndex: 0,
  swings: [],
  trades: [],
  skips: [],
  stacks: [],
  stackUsed: false,
  focusTrade: null,
  decimals: 2,
};

const TRADE_WIN = 'rgba(8, 153, 129, 0.16)';
const TRADE_RISK = 'rgba(242, 54, 69, 0.16)';

const SESSION_STYLE: Record<SessionBox['key'], { fill: string; edge: string; text: string }> = {
  asia: { fill: 'rgba(126, 87, 194, 0.16)', edge: 'rgba(126, 87, 194, 0.55)', text: '#b39ddb' },
  london: { fill: 'rgba(242, 54, 69, 0.12)', edge: 'rgba(242, 54, 69, 0.5)', text: '#ef9a9a' },
  ny: { fill: 'rgba(178, 181, 190, 0.10)', edge: 'rgba(178, 181, 190, 0.45)', text: '#b2b5be' },
  open: { fill: 'rgba(178, 181, 190, 0.10)', edge: 'rgba(178, 181, 190, 0.45)', text: '#b2b5be' },
};

const AVR_STYLE: Record<string, { fill: string; line: string }> = {
  l1: { fill: 'rgba(178, 181, 190, 0.16)', line: 'rgba(178, 181, 190, 0.6)' },
  avrMinus: { fill: '', line: 'rgba(178, 181, 190, 0.7)' },
  avr: { fill: 'rgba(255, 152, 0, 0.20)', line: 'rgba(255, 152, 0, 0.7)' },
  avrPlus: { fill: 'rgba(242, 54, 69, 0.20)', line: 'rgba(242, 54, 69, 0.7)' },
  max: { fill: 'rgba(183, 28, 28, 0.25)', line: 'rgba(183, 28, 28, 0.8)' },
};

const ZONE_FILL = 'rgba(255, 152, 0, 0.18)';
const ZONE_DIM = 'rgba(120, 123, 134, 0.12)';
const FIB_LINE = 'rgba(255, 213, 79, 0.75)';
const PREMIUM = 'rgba(242, 54, 69, 0.06)';
const DISCOUNT = 'rgba(8, 153, 129, 0.06)';
const STACK_STYLE = {
  buy: { fill: 'rgba(38, 166, 154, 0.22)', used: 'rgba(38, 166, 154, 0.08)', edge: 'rgba(38, 166, 154, 0.85)', text: '#4db6ac' },
  sell: { fill: 'rgba(239, 83, 80, 0.22)', used: 'rgba(239, 83, 80, 0.08)', edge: 'rgba(239, 83, 80, 0.85)', text: '#ef9a9a' },
};
const LABEL_FONT = '10px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, sans-serif';

/**
 * Draws the Auction Resolution layers on the price pane: session boxes, Asia-range (AVR) levels, the range
 * Fibonacci zone with value area / premium / discount, and HH/HL/LH/LL swing labels.
 */
export class ArPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private state: ArDrawState = EMPTY;

  private back: IPrimitivePaneView = {
    zOrder: () => 'bottom',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) =>
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          this.paintBack(context, mediaSize.width);
          this.paintStacks(context, mediaSize.width);
          this.paintTradesBack(context, mediaSize.width);
        }),
    }),
  };

  private front: IPrimitivePaneView = {
    zOrder: () => 'normal',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) =>
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          this.paintFront(context, mediaSize.width);
          this.paintTradesFront(context, mediaSize.width);
        }),
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
    return [this.back, this.front];
  }

  setState(state: ArDrawState | null) {
    this.state = state ?? EMPTY;
    this.requestUpdate?.();
  }

  private x(logical: number): number | null {
    return this.chart ? logicalX(this.chart, logical) : null;
  }

  private y(price: number): number | null {
    return this.series?.priceToCoordinate(price) ?? null;
  }

  /** Left/right pixel edges of candles a..b, clipped to the pane; null when entirely off screen. */
  private span(a: number, b: number, w: number, extendRight = false): [number, number] | null {
    const x0 = this.x(a - 0.5);
    const x1 = extendRight ? w : this.x(b + 0.5);
    if (x0 == null || x1 == null || x1 < 0 || x0 > w) return null;
    return [Math.max(0, x0), Math.min(w, x1)];
  }

  private band(ctx: CanvasRenderingContext2D, x: [number, number], lo: number, hi: number, fill: string) {
    const a = this.y(hi);
    const b = this.y(lo);
    if (a == null || b == null) return;
    ctx.fillStyle = fill;
    ctx.fillRect(x[0], Math.min(a, b), x[1] - x[0], Math.max(1, Math.abs(b - a)));
  }

  private hline(ctx: CanvasRenderingContext2D, x: [number, number], price: number, color: string, dash: number[] = []) {
    const y = this.y(price);
    if (y == null) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(x[0], Math.round(y) + 0.5);
    ctx.lineTo(x[1], Math.round(y) + 0.5);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  private label(ctx: CanvasRenderingContext2D, text: string, x: number, price: number, color: string, above = true) {
    const y = this.y(price);
    if (y == null) return;
    ctx.font = LABEL_FONT;
    ctx.fillStyle = color;
    ctx.textBaseline = above ? 'bottom' : 'top';
    ctx.fillText(text, x + 3, above ? y - 1 : y + 1);
  }

  private paintBack(ctx: CanvasRenderingContext2D, w: number) {
    const s = this.state;
    for (const box of s.sessions) {
      const x = this.span(box.from, box.to, w);
      if (!x) continue;
      const st = SESSION_STYLE[box.key];
      this.band(ctx, x, box.low, box.high, st.fill);
    }
    for (const day of s.avr) {
      const x = this.span(day.from, day.to, w);
      if (!x) continue;
      for (const l of day.levels) {
        const fill = AVR_STYLE[l.key]?.fill;
        if (fill && l.hi > l.lo) this.band(ctx, x, l.lo, l.hi, fill);
      }
    }
    const f = s.fib;
    if (f) {
      const x = this.span(f.from.index, s.lastIndex, w, true);
      if (x) {
        if (f.vah != null && f.val != null) {
          // premium above value, discount below, over the leg and after it
          const top = Math.max(f.from.price, f.to.price, f.extension);
          const bottom = Math.min(f.from.price, f.to.price, f.extension);
          this.band(ctx, x, f.vah, top, PREMIUM);
          this.band(ctx, x, bottom, f.val, DISCOUNT);
        }
        const live = f.status !== 'invalidated' && f.status !== 'extended';
        const zx = this.span(f.to.index, s.lastIndex, w, true);
        if (zx) this.band(ctx, zx, f.zoneBottom, f.zoneTop, live ? ZONE_FILL : ZONE_DIM);
      }
    }
  }

  /** Stacked-imbalance zones: a box from the stack's candle to where price traded through it (or the right edge). */
  private paintStacks(ctx: CanvasRenderingContext2D, w: number) {
    ctx.font = LABEL_FONT;
    for (const z of this.state.stacks) {
      const used = z.usedAt != null;
      if (used && !this.state.stackUsed) continue;
      const x = this.span(z.index, z.usedAt ?? this.state.lastIndex, w, !used);
      if (!x) continue;
      const st = STACK_STYLE[z.side];
      this.band(ctx, x, z.lo, z.hi, used ? st.used : st.fill);
      if (used) continue;
      // the edge price comes back to: the top of a buy zone, the bottom of a sell zone
      this.hline(ctx, x, z.side === 'buy' ? z.hi : z.lo, st.edge);
      const lx = this.x(z.index);
      if (lx != null && lx > -40 && lx < w) {
        this.label(ctx, `${z.side === 'buy' ? '▲' : '▼'} ${z.rows} stacked`, lx, z.side === 'buy' ? z.lo : z.hi, st.text, z.side !== 'buy');
      }
    }
  }

  private paintTradesBack(ctx: CanvasRenderingContext2D, w: number) {
    for (const t of this.state.trades) {
      const x = this.span(t.entryIndex, t.exitIndex ?? this.state.lastIndex, w, t.exitIndex == null);
      if (!x) continue;
      const focus = this.state.focusTrade === t.id;
      this.band(ctx, x, Math.min(t.entry, t.target), Math.max(t.entry, t.target), focus ? 'rgba(8, 153, 129, 0.3)' : TRADE_WIN);
      this.band(ctx, x, Math.min(t.entry, t.stop), Math.max(t.entry, t.stop), focus ? 'rgba(242, 54, 69, 0.3)' : TRADE_RISK);
    }
  }

  private paintTradesFront(ctx: CanvasRenderingContext2D, w: number) {
    const fmtR = (r: number | null) => (r == null ? '' : `${r >= 0 ? '+' : ''}${r.toFixed(1)}R`);
    ctx.font = LABEL_FONT;
    for (const t of this.state.trades) {
      const x = this.span(t.entryIndex, t.exitIndex ?? this.state.lastIndex, w, t.exitIndex == null);
      if (x) {
        this.hline(ctx, x, t.entry, 'rgba(209, 212, 220, 0.8)');
        this.hline(ctx, x, t.stop, 'rgba(242, 54, 69, 0.8)');
        this.hline(ctx, x, t.target, 'rgba(8, 153, 129, 0.8)');
      }
      // entry: a triangle under (long) / over (short) the entry price
      const ex = this.x(t.entryIndex);
      const ey = this.y(t.entry);
      const long = t.dir === 'long';
      if (ex != null && ey != null && ex > -10 && ex < w + 10) {
        ctx.fillStyle = long ? '#26a69a' : '#ef5350';
        ctx.beginPath();
        const s = 6;
        const tip = long ? ey + 3 : ey - 3;
        const base = long ? tip + s : tip - s;
        ctx.moveTo(ex, tip);
        ctx.lineTo(ex - s, base);
        ctx.lineTo(ex + s, base);
        ctx.closePath();
        ctx.fill();
        ctx.textAlign = 'center';
        ctx.textBaseline = long ? 'top' : 'bottom';
        ctx.fillText(`AR ${t.dir}`, ex, long ? base + 2 : base - 2);
      }
      // exit: a dot coloured by the result, with R
      if (t.exitIndex != null && t.exit != null) {
        const xx = this.x(t.exitIndex);
        const xy = this.y(t.exit);
        if (xx != null && xy != null && xx > -10 && xx < w + 40) {
          const won = (t.points ?? 0) > 0;
          ctx.fillStyle = won ? '#26a69a' : '#ef5350';
          ctx.beginPath();
          ctx.arc(xx, xy, 3.5, 0, Math.PI * 2);
          ctx.fill();
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText(`${t.outcome} ${fmtR(t.r)}`, xx + 6, xy);
        }
      }
    }
    // skipped setups: a small grey ✕ (hover the candle to read why in the panel)
    ctx.fillStyle = 'rgba(149, 152, 161, 0.85)';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const k of this.state.skips) {
      const kx = this.x(k.index);
      const ky = this.y(k.price);
      if (kx == null || ky == null || kx < -10 || kx > w + 10) continue;
      ctx.fillText('✕', kx, k.dir === 'long' ? ky + 12 : ky - 12);
    }
    ctx.textAlign = 'start';
  }

  private paintFront(ctx: CanvasRenderingContext2D, w: number) {
    const s = this.state;
    const fmt = (p: number) => p.toFixed(s.decimals);
    for (const box of s.sessions) {
      const x = this.span(box.from, box.to, w);
      if (!x) continue;
      const st = SESSION_STYLE[box.key];
      this.hline(ctx, x, box.high, st.edge);
      this.hline(ctx, x, box.low, st.edge);
      this.label(ctx, box.label, x[0], box.high, st.text);
    }
    for (const day of s.avr) {
      const x = this.span(day.from, day.to, w);
      if (!x) continue;
      this.hline(ctx, x, day.asia.high, 'rgba(255, 235, 59, 0.8)');
      this.hline(ctx, x, day.asia.low, 'rgba(255, 235, 59, 0.8)');
      this.hline(ctx, x, day.eq, 'rgba(209, 212, 220, 0.5)', [4, 3]);
      this.hline(ctx, x, day.q1, 'rgba(242, 54, 69, 0.35)', [2, 3]);
      this.hline(ctx, x, day.q3, 'rgba(242, 54, 69, 0.35)', [2, 3]);
      this.label(ctx, 'Asia H', x[0], day.asia.high, '#fff176');
      this.label(ctx, 'Asia L', x[0], day.asia.low, '#fff176', false);
      for (const l of day.levels) {
        const st = AVR_STYLE[l.key]!;
        const mid = (l.lo + l.hi) / 2;
        if (l.hi > l.lo) {
          this.hline(ctx, x, l.hi, st.line);
          this.hline(ctx, x, l.lo, st.line);
        } else this.hline(ctx, x, mid, st.line, [5, 4]);
        this.label(ctx, l.label, x[0], l.side === 'up' ? l.hi : l.lo, st.line, l.side === 'up');
      }
    }
    const f = s.fib;
    if (f) {
      const x = this.span(f.to.index, s.lastIndex, w, true);
      const legX = this.span(f.from.index, s.lastIndex, w, true);
      if (x && legX) {
        const live = f.status !== 'invalidated' && f.status !== 'extended';
        const dim = (c: string) => (live ? c : 'rgba(120, 123, 134, 0.6)');
        for (const { level, price } of f.levels) {
          const zone = level >= 0.705 && level <= 0.886;
          const color = level === 1.1 ? dim('rgba(242, 54, 69, 0.8)') : dim(zone ? '#ffb74d' : FIB_LINE);
          this.hline(ctx, x, price, color, zone || level === 1.1 ? [] : [4, 3]);
          this.label(ctx, `${level} (${fmt(price)})`, Math.max(x[0], x[1] - 120), price, color, f.dir === 'down');
        }
        // the leg itself, and its end as the target ("DOL": draw on liquidity)
        this.hline(ctx, legX, f.to.price, dim('rgba(41, 98, 255, 0.9)'));
        this.hline(ctx, legX, f.from.price, 'rgba(41, 98, 255, 0.45)', [2, 3]);
        this.label(ctx, `DOL · target ${fmt(f.target)}`, Math.max(legX[0], x[1] - 150), f.to.price, dim('#82b1ff'), f.dir === 'up');
        if (f.vah != null && f.val != null && f.poc != null) {
          this.hline(ctx, legX, f.vah, 'rgba(242, 54, 69, 0.45)', [6, 4]);
          this.hline(ctx, legX, f.val, 'rgba(8, 153, 129, 0.55)', [6, 4]);
          this.hline(ctx, legX, f.poc, 'rgba(209, 212, 220, 0.4)', [1, 3]);
          this.label(ctx, 'VAH · premium above', legX[0], f.vah, 'rgba(242, 54, 69, 0.8)');
          this.label(ctx, 'VAL · discount below', legX[0], f.val, 'rgba(8, 153, 129, 0.9)', false);
        }
        const zoneEdge = f.dir === 'up' ? f.zoneTop : f.zoneBottom;
        this.label(
          ctx,
          `${f.dir === 'up' ? 'Discount' : 'Premium'} zone · ${f.status}`,
          x[0],
          zoneEdge,
          live ? '#ffb74d' : '#787b86',
          f.dir === 'down',
        );
      }
    }
    // swing labels
    ctx.font = LABEL_FONT;
    ctx.textAlign = 'center';
    for (const { pivot, label } of s.swings) {
      const px = this.x(pivot.index);
      const py = this.y(pivot.price);
      if (px == null || py == null || px < -20 || px > w + 20) continue;
      const high = pivot.kind === 'H';
      const text = label ?? (high ? 'H' : 'L');
      const up = label === 'HH' || label === 'HL';
      ctx.fillStyle = label ? (up ? '#26a69a' : '#ef5350') : '#9598a1';
      ctx.textBaseline = high ? 'bottom' : 'top';
      ctx.fillText(text, px, high ? py - 4 : py + 4);
    }
    ctx.textAlign = 'start';
  }
}
