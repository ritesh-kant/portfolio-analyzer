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
import type { Bar } from '../../lib/tv/types';
import { type VpSettings, VP_DEFAULTS, withAlpha } from '../../lib/tv/drawings';
import { type VolumeProfile, buildProfile } from '../../lib/tv/volume-profile';
import { logicalX } from './coords';

export interface SvpOptions {
  /** Price rows per session. */
  rows: number;
  /** Look and inputs, shared with the fixed range volume profile. */
  vp: VpSettings;
  /** Price decimals, for the level labels. */
  decimals: number;
  /** Candle length in seconds. */
  secs: number;
  /** 1-minute candles for a finer profile, where they cover the session. */
  fine: Bar[] | null;
}

const NONE: SvpOptions = {
  rows: 24,
  vp: { ...VP_DEFAULTS, showBackground: false },
  decimals: 2,
  secs: 60,
  fine: null,
};

/** First index in `bars` whose time is >= t. */
function lowerBound(bars: Bar[], t: number): number {
  let lo = 0;
  let hi = bars.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid]!.time < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Session volume profile: for every trading session on screen, a volume-by-price
 * histogram anchored at the session's first candle, with its point of control and
 * value area. The current session's profile develops as candles arrive.
 *
 * Volume is spread over each candle's range, so the shape is only as good as the
 * candles: sessions covered by 1-minute data use it (a much finer shape); older ones
 * fall back to the chart's own candles.
 */
export class SessionProfilePrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private bars: Bar[] = [];
  private sessions: [number, number][] = [];
  private opts: SvpOptions = NONE;
  private cache = new Map<string, VolumeProfile | null>();
  private view: IPrimitivePaneView = {
    zOrder: () => 'bottom',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) =>
          this.paint(context, mediaSize.width),
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

  /** `sessions` are inclusive candle-index ranges; pass none to draw nothing. */
  setState(bars: Bar[], sessions: [number, number][], opts: SvpOptions = NONE) {
    this.bars = bars;
    this.sessions = sessions;
    this.opts = opts;
    this.requestUpdate?.();
  }

  private profileOf(a: number, b: number, rows: number, valueArea: number): VolumeProfile | null {
    const { fine, secs } = this.opts;
    const start = this.bars[a]!.time;
    const lastStart = this.bars[b]!.time;
    // 1-minute candles, when they span the whole session so far.
    const useFine =
      !!fine &&
      fine.length > 0 &&
      fine[0]!.time <= start &&
      fine[fine.length - 1]!.time >= lastStart;
    const source = useFine ? fine! : this.bars;
    const i0 = useFine ? lowerBound(fine!, start) : a;
    const i1 = useFine ? lowerBound(fine!, lastStart + secs) - 1 : b;
    const last = source[i1];
    const key = `${a}|${b}|${rows}|${valueArea}|${useFine}|${i1}|${last?.volume}|${last?.high}|${last?.low}`;
    if (this.cache.has(key)) return this.cache.get(key)!;
    if (this.cache.size > 600) this.cache.clear();
    const profile = i1 >= i0 ? buildProfile(source, i0, i1, rows, valueArea) : null;
    this.cache.set(key, profile);
    return profile;
  }

  private paint(ctx: CanvasRenderingContext2D, w: number) {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series || !this.sessions.length) return;
    const visible = chart.timeScale().getVisibleLogicalRange();
    if (!visible) return;
    const y = (price: number) => series.priceToCoordinate(price);
    const { vp, decimals } = this.opts;
    const rows = this.opts.rows > 0 ? this.opts.rows : 24;
    const fromRight = vp.placement === 'right';

    for (const [a, b] of this.sessions) {
      if (b < visible.from - 1 || a > visible.to + 1) continue;
      const x0 = logicalX(chart, a - 0.5);
      const x1 = logicalX(chart, b + 0.5);
      if (x0 == null || x1 == null || x1 < 0 || x0 > w) continue;

      const profile = this.profileOf(a, b, rows, vp.valueArea / 100);
      if (!profile || profile.maxRow <= 0) continue;

      if (vp.showBackground) {
        const yTop = y(profile.hi);
        const yBottom = y(profile.lo);
        if (yTop != null && yBottom != null) {
          ctx.fillStyle = withAlpha(vp.backgroundColor, vp.backgroundOpacity);
          ctx.fillRect(x0, yTop, x1 - x0, yBottom - yTop);
        }
      }

      // Histogram grows from the session's edge: up volume then down volume (or one colour for total), value area brighter.
      const maxLen = (x1 - x0) * (vp.width / 100);
      const edge = fromRight ? x1 : x0;
      const sign = fromRight ? -1 : 1;
      profile.rows.forEach((row, i) => {
        const top = y(row.price + profile.step);
        const bottom = y(row.price);
        const total = row.up + row.down;
        if (top == null || bottom == null || total <= 0) return;
        const h = Math.max(1, Math.abs(bottom - top) - 1);
        const yy = Math.min(top, bottom);
        const len = (maxLen * total) / profile.maxRow;
        const upLen = vp.volume === 'total' ? len : (len * row.up) / total;
        const inValue = i >= profile.vaLo && i <= profile.vaHi;
        const alpha = inValue ? vp.opacity : vp.opacity * 0.45;
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

      // Point of control and value-area edges run across the whole session.
      ctx.font = '10px -apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';
      ctx.textAlign = fromRight ? 'left' : 'right';
      ctx.textBaseline = 'bottom';
      const level = (price: number, label: string, color: string, lineWidth: number) => {
        const ly = y(price);
        if (ly == null) return;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = lineWidth;
        ctx.beginPath();
        ctx.moveTo(x0, ly);
        ctx.lineTo(x1, ly);
        ctx.stroke();
        if (vp.showLabels) ctx.fillText(`${label} ${price.toFixed(decimals)}`, fromRight ? x0 + 4 : x1 - 4, ly - 2);
      };
      if (vp.showVa) {
        level(profile.vah, 'VAH', vp.vaLineColor, 1);
        level(profile.val, 'VAL', vp.vaLineColor, 1);
      }
      if (vp.showPoc) level(profile.pocPrice, 'POC', vp.pocColor, vp.pocWidth);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
    }
  }
}
