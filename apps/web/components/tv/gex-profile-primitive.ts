import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts';
import { fmtGex } from '../../lib/tv/gex';

export interface GexBar {
  /** Strike as drawn on this chart (already moved by the futures premium). */
  price: number;
  /** Net GEX at the strike, money per 1% move (+ damps moves, − amplifies them). */
  net: number;
}

const POS = 'rgba(8, 153, 129, 0.75)';
const NEG = 'rgba(242, 54, 69, 0.75)';
const POS_TEXT = '#26a69a';
const NEG_TEXT = '#ef5350';
const ZONE_POS = (a: number) => `rgba(8, 153, 129, ${a})`;
const ZONE_NEG = (a: number) => `rgba(242, 54, 69, ${a})`;
/** Extra price-axis width the column takes, right of the price labels. */
export const GEX_STRIP_WIDTH = 120;
/** Room kept for the value label after the longest bar. */
const LABEL_ROOM = 46;

/**
 * Net gamma exposure by strike (MenthorQ's "Net GEX" column), drawn in its own strip inside the price axis,
 * right of the price labels, so it never covers candles or indicators. The chart widens the right price
 * scale by `GEX_STRIP_WIDTH` while it is on. One bar per strike grows right from the strip's left edge,
 * green where dealer hedging damps moves and red where it amplifies them; length is relative to the largest
 * strike on screen, so zooming the price axis re-scales it.
 */
export class GexProfilePrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private bars: GexBar[] = [];
  private currency: 'INR' | 'USD' = 'INR';
  /** Gamma flip as drawn on this chart; null = no regime background. */
  private flip: number | null = null;
  /** Multiplier on the tint's opacity (1 = full strength). */
  private zoneStrength = 1;
  private zoneViews: IPrimitivePaneView[] = [
    {
      // Behind the candles, grid-level: a tint, never a cover.
      zOrder: () => 'bottom',
      renderer: (): IPrimitivePaneRenderer => ({
        draw: () => {},
        drawBackground: (target) => {
          target.useMediaCoordinateSpace(({ context, mediaSize }) =>
            this.paintZones(context, mediaSize.width, mediaSize.height),
          );
        },
      }),
    },
  ];
  private views: IPrimitivePaneView[] = [
    {
      // Below the axis labels: a level label at the same price stays readable.
      zOrder: () => 'normal',
      renderer: (): IPrimitivePaneRenderer => ({
        draw: (target) => {
          target.useMediaCoordinateSpace(({ context, mediaSize }) =>
            this.paint(context, mediaSize.width, mediaSize.height),
          );
        },
      }),
    },
  ];

  attached(param: SeriesAttachedParameter<Time>) {
    this.series = param.series as ISeriesApi<SeriesType, Time>;
    this.requestUpdate = param.requestUpdate;
  }

  detached() {
    this.series = null;
    this.requestUpdate = null;
  }

  priceAxisPaneViews() {
    return this.views;
  }

  paneViews() {
    return this.zoneViews;
  }

  /** Tint the pane green above the gamma flip and red below it; null clears it. */
  setFlip(flip: number | null, strength = 1) {
    this.flip = flip;
    this.zoneStrength = Math.max(0, strength);
    this.requestUpdate?.();
  }

  /** Strikes sorted by price; pass none to draw nothing. */
  setState(bars: GexBar[], currency: 'INR' | 'USD' = 'INR') {
    this.bars = bars;
    this.currency = currency;
    this.requestUpdate?.();
  }

  private paintZones(ctx: CanvasRenderingContext2D, w: number, h: number) {
    const series = this.series;
    if (!series || this.flip == null) return;
    const raw = series.priceToCoordinate(this.flip);
    if (raw == null) return;
    // Off-screen flip: the whole pane is one regime.
    const y = Math.max(0, Math.min(h, raw));
    const k = this.zoneStrength;
    const pos = (a: number) => ZONE_POS(Math.min(1, a * k));
    const neg = (a: number) => ZONE_NEG(Math.min(1, a * k));
    // Strongest next to the flip, fading away from it (MenthorQ / Tanuki look).
    if (y > 0) {
      const g = ctx.createLinearGradient(0, y, 0, 0);
      g.addColorStop(0, pos(0.22));
      g.addColorStop(Math.min(1, 200 / y), pos(0.1));
      g.addColorStop(1, pos(0.07));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, y);
    }
    if (y < h) {
      const g = ctx.createLinearGradient(0, y, 0, h);
      g.addColorStop(0, neg(0.22));
      g.addColorStop(Math.min(1, 200 / (h - y)), neg(0.1));
      g.addColorStop(1, neg(0.07));
      ctx.fillStyle = g;
      ctx.fillRect(0, y, w, h - y);
    }
    // A solid edge on the right side of the pane, like the reference: green above, red below.
    ctx.fillStyle = ZONE_POS(0.9);
    ctx.fillRect(w - 3, 0, 3, y);
    ctx.fillStyle = ZONE_NEG(0.9);
    ctx.fillRect(w - 3, y, 3, h - y);
  }

  private paint(ctx: CanvasRenderingContext2D, w: number, h: number) {
    const series = this.series;
    if (!series || !this.bars.length) return;
    const rows: { y: number; net: number }[] = [];
    for (const b of this.bars) {
      const y = series.priceToCoordinate(b.price);
      if (y != null && y >= -20 && y <= h + 20 && b.net !== 0) rows.push({ y, net: b.net });
    }
    if (!rows.length) return;
    const x0 = w - GEX_STRIP_WIDTH;
    // A hairline between the price labels and the column.
    ctx.fillStyle = '#2a2e39';
    ctx.fillRect(x0, 0, 1, h);

    const peak = Math.max(...rows.map((r) => Math.abs(r.net)));
    // Row height: the pixel gap between neighbouring strikes, so bars touch without overlapping.
    let gap = Infinity;
    for (let i = 1; i < rows.length; i += 1) gap = Math.min(gap, Math.abs(rows[i]!.y - rows[i - 1]!.y));
    const barH = Math.max(1, Math.min(Number.isFinite(gap) ? gap - 2 : 10, 12));
    const maxLen = GEX_STRIP_WIDTH - LABEL_ROOM - 8;
    const showText = barH >= 7;

    ctx.font = '10px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    for (const r of rows) {
      const len = Math.max(2, (maxLen * Math.abs(r.net)) / peak);
      ctx.fillStyle = r.net >= 0 ? POS : NEG;
      ctx.fillRect(x0 + 4, r.y - barH / 2, len, barH);
      if (showText) {
        ctx.fillStyle = r.net >= 0 ? POS_TEXT : NEG_TEXT;
        ctx.fillText(fmtGex(r.net, this.currency), x0 + 4 + len + 3, r.y);
      }
    }
    ctx.textBaseline = 'alphabetic';
  }
}
