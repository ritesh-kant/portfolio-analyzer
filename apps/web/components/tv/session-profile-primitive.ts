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
import { type VolumeProfile, buildProfile } from '../../lib/tv/volume-profile';
import { logicalX } from './coords';

export type SvpStyle = 'split' | 'total';

export interface SvpOptions {
  /** Price rows per session; 0 = automatic (about one row per few screen pixels). */
  rows: number;
  /** Fraction of a session's width its busiest row may fill. */
  width: number;
  style: SvpStyle;
  /** Candle length in seconds. */
  secs: number;
  /** 1-minute candles for a finer profile, where they cover the session. */
  fine: Bar[] | null;
}

const NONE: SvpOptions = { rows: 24, width: 0.3, style: 'split', secs: 60, fine: null };
/** Target screen height of one row in automatic mode. */
const AUTO_ROW_PX = 3;

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

  private profileOf(a: number, b: number, rows: number): VolumeProfile | null {
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
    const key = `${a}|${b}|${rows}|${useFine}|${i1}|${last?.volume}|${last?.high}|${last?.low}`;
    if (this.cache.has(key)) return this.cache.get(key)!;
    if (this.cache.size > 600) this.cache.clear();
    const profile = i1 >= i0 ? buildProfile(source, i0, i1, rows) : null;
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
    const { style, width } = this.opts;

    for (const [a, b] of this.sessions) {
      if (b < visible.from - 1 || a > visible.to + 1) continue;
      const x0 = logicalX(chart, a - 0.5);
      const x1 = logicalX(chart, b + 0.5);
      if (x0 == null || x1 == null || x1 < 0 || x0 > w) continue;

      let rows = this.opts.rows;
      if (rows <= 0) {
        let lo = Infinity;
        let hi = -Infinity;
        for (let i = a; i <= b; i += 1) {
          lo = Math.min(lo, this.bars[i]!.low);
          hi = Math.max(hi, this.bars[i]!.high);
        }
        const yHi = y(hi);
        const yLo = y(lo);
        rows = yHi != null && yLo != null ? Math.round(Math.abs(yLo - yHi) / AUTO_ROW_PX) : 24;
        rows = Math.max(12, Math.min(400, rows));
      }

      const profile = this.profileOf(a, b, rows);
      if (!profile || profile.maxRow <= 0) continue;
      const maxLen = (x1 - x0) * width;

      if (style === 'total') {
        // Grey box over the value area's price span, across the whole session.
        const vTop = y(profile.vah);
        const vBottom = y(profile.val);
        if (vTop != null && vBottom != null) {
          ctx.fillStyle = 'rgba(140, 140, 150, 0.22)';
          ctx.fillRect(x0, Math.min(vTop, vBottom), x1 - x0, Math.abs(vBottom - vTop));
        }
      }

      profile.rows.forEach((row, i) => {
        const top = y(row.price + profile.step);
        const bottom = y(row.price);
        const total = row.up + row.down;
        if (top == null || bottom == null || total <= 0) return;
        const h = Math.max(1, Math.abs(bottom - top) - (Math.abs(bottom - top) > 3 ? 1 : 0));
        const len = (maxLen * total) / profile.maxRow;
        const rowTop = Math.min(top, bottom);
        const inValue = i >= profile.vaLo && i <= profile.vaHi;
        if (style === 'total') {
          ctx.fillStyle = inValue ? 'rgba(205, 205, 212, 0.8)' : 'rgba(59, 130, 246, 0.9)';
          ctx.fillRect(x0, rowTop, len, h);
        } else {
          const upLen = (len * row.up) / total;
          ctx.fillStyle = inValue ? 'rgba(41, 98, 255, 0.45)' : 'rgba(41, 98, 255, 0.22)';
          ctx.fillRect(x0, rowTop, upLen, h);
          ctx.fillStyle = inValue ? 'rgba(251, 192, 45, 0.5)' : 'rgba(251, 192, 45, 0.25)';
          ctx.fillRect(x0 + upLen, rowTop, len - upLen, h);
        }
      });

      const poc = y(profile.pocPrice);
      if (poc != null) {
        ctx.strokeStyle = style === 'total' ? '#e879f9' : 'rgba(242, 54, 69, 0.75)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x0, poc);
        ctx.lineTo(x1, poc);
        ctx.stroke();
      }

      if (style === 'total') {
        // The session's open→close as a short bar just left of the profile.
        const open = y(this.bars[a]!.open);
        const close = y(this.bars[b]!.close);
        if (open != null && close != null) {
          ctx.strokeStyle = this.bars[b]!.close >= this.bars[a]!.open ? '#22c55e' : '#ef4444';
          ctx.lineWidth = 3;
          ctx.beginPath();
          ctx.moveTo(x0 - 3, open);
          ctx.lineTo(x0 - 3, close);
          ctx.stroke();
        }
      }
    }
  }
}
