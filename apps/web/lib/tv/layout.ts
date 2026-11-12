/** Multi-chart layouts for /chart: a CSS grid, each cell is one independent chart (an iframe of /chart?pane=N). */
export interface LayoutCell {
  col: number;
  row: number;
  colSpan?: number;
  rowSpan?: number;
}

export interface ChartLayout {
  id: string;
  label: string;
  cols: string;
  rows: string;
  cells: LayoutCell[];
}

const lcm = (a: number, b: number): number => {
  let x = a;
  let y = b;
  while (y) [x, y] = [y, x % y];
  return (a * b) / x;
};

/**
 * Layout id = "r:2,3" (rows holding 2 then 3 charts) or "c:1,3" (columns holding 1 then 3 charts, stacked).
 * Rows/columns with different counts share the grid through a common multiple of divisions.
 */
function build(axis: 'r' | 'c', counts: number[]): ChartLayout {
  const common = counts.reduce(lcm, 1);
  const cells: LayoutCell[] = [];
  counts.forEach((n, i) => {
    const span = common / n;
    for (let k = 0; k < n; k++) {
      cells.push(
        axis === 'r'
          ? { col: k * span + 1, row: i + 1, colSpan: span }
          : { col: i + 1, row: k * span + 1, rowSpan: span },
      );
    }
  });
  const lines = (n: number) => Array(n).fill('1fr').join(' ');
  const total = counts.reduce((a, b) => a + b, 0);
  const id = `${axis}:${counts.join(',')}`;
  return {
    id,
    label: `${total} chart${total === 1 ? '' : 's'}`,
    cols: lines(axis === 'r' ? common : counts.length),
    rows: lines(axis === 'r' ? counts.length : common),
    cells,
  };
}

const same = (n: number, v: number) => Array<number>(n).fill(v);
const R = (...c: number[]) => build('r', c);
const C = (...c: number[]) => build('c', c);

/** Grouped by chart count, like TradingView's layout menu. */
export const LAYOUT_GROUPS: { count: number; layouts: ChartLayout[] }[] = [
  { count: 1, layouts: [R(1)] },
  { count: 2, layouts: [C(1, 1), R(1, 1)] },
  { count: 3, layouts: [C(1, 1, 1), R(1, 1, 1), C(1, 2), C(2, 1), R(1, 2), R(2, 1)] },
  {
    count: 4,
    layouts: [
      R(2, 2),
      R(...same(4, 1)),
      C(...same(4, 1)),
      C(1, 3),
      C(3, 1),
      R(1, 3),
      R(3, 1),
      C(1, 2, 1),
    ],
  },
  {
    count: 5,
    layouts: [
      R(2, 3),
      R(3, 2),
      C(...same(5, 1)),
      R(...same(5, 1)),
      C(1, 4),
      C(4, 1),
      R(1, 4),
      R(4, 1),
    ],
  },
  {
    count: 6,
    layouts: [R(3, 3), C(...same(6, 1)), R(...same(6, 1)), C(3, 3), R(2, 2, 2), C(2, 2, 2)],
  },
  { count: 7, layouts: [R(3, 4), C(...same(7, 1)), C(1, 6)] },
  { count: 8, layouts: [R(4, 4), R(2, 2, 2, 2), C(...same(8, 1)), R(...same(8, 1))] },
  { count: 9, layouts: [R(3, 3, 3), R(4, 5), C(...same(9, 1)), R(...same(9, 1))] },
  { count: 10, layouts: [R(5, 5), C(...same(10, 1)), R(...same(10, 1))] },
  { count: 12, layouts: [R(4, 4, 4), R(3, 3, 3, 3), C(...same(12, 1))] },
  { count: 14, layouts: [R(7, 7)] },
  { count: 16, layouts: [R(4, 4, 4, 4), C(...same(16, 1))] },
];

export const LAYOUTS: ChartLayout[] = LAYOUT_GROUPS.flatMap((g) => g.layouts);

export const LAYOUT_KEY = 'tv:layout';
export const SYNC_KEY = 'tv:layoutSync';

export interface LayoutSync {
  symbol: boolean;
  interval: boolean;
}
export const SYNC_DEFAULTS: LayoutSync = { symbol: true, interval: false };

const LEGACY: Record<string, string> = {
  '2v': 'c:1,1',
  '2h': 'r:1,1',
  '3v': 'c:1,1,1',
  '1+2': 'c:1,2',
  '2x2': 'r:2,2',
  '3x2': 'r:3,3',
};

export function layoutById(id: string | null | undefined): ChartLayout {
  const key = id && LEGACY[id] ? LEGACY[id] : id;
  return LAYOUTS.find((l) => l.id === key) ?? LAYOUTS[0]!;
}
