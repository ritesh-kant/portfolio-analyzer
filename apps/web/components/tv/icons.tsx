import type { ReactNode } from 'react';

interface IconProps {
  size?: number;
}

const wrap = (children: ReactNode, size = 20) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 20 20"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.3"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    {children}
  </svg>
);

const positionIcon = (letter: string, size?: number) =>
  wrap(
    <>
      <path d="M5 4h10M5 16h10" />
      <circle cx="3.5" cy="4" r="1.3" />
      <circle cx="16.5" cy="4" r="1.3" />
      <circle cx="3.5" cy="16" r="1.3" />
      <circle cx="16.5" cy="16" r="1.3" />
      <path d="M5 10h10" />
      <text
        x="10"
        y="9"
        fontSize="5.5"
        fontWeight="700"
        fill="currentColor"
        stroke="none"
        textAnchor="middle"
      >
        {letter}
      </text>
    </>,
    size,
  );

export const Icons = {
  cross: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 3v14M3 10h14" />
      </>,
      size,
    ),
  trend: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3.5 15.5l13-11" />
        <circle cx="3.5" cy="15.5" r="1.6" />
        <circle cx="16.5" cy="4.5" r="1.6" />
      </>,
      size,
    ),
  ray: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3.5 14.5l14-9" />
        <circle cx="3.5" cy="14.5" r="1.6" />
        <circle cx="9.5" cy="10.6" r="1.1" />
      </>,
      size,
    ),
  hline: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M2.5 10h15" />
        <circle cx="10" cy="10" r="1.6" />
      </>,
      size,
    ),
  hray: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M4 10h13.5" />
        <circle cx="4" cy="10" r="1.6" />
      </>,
      size,
    ),
  vline: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 2.5v15" />
        <circle cx="10" cy="10" r="1.6" />
      </>,
      size,
    ),
  rect: ({ size }: IconProps) =>
    wrap(
      <>
        <rect x="3.5" y="5.5" width="13" height="9" rx="1" />
      </>,
      size,
    ),
  brush: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3.5 16.5c0-2 1.2-3 2.8-3 1.3 0 2.2 1 1.8 2.3-.5 1.5-2.600 2.200-4.600.700z" />
        <path d="M8 13.500l7-8.500a1.300 1.300 0 0 1 2 1.600L10 14.500" />
      </>,
      size,
    ),
  highlighter: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M11.500 3.500l5 5-6 6-5-5z" />
        <path d="M5.500 9.500l-2 4.500 2 .5 1.500-1M3 17h6" />
      </>,
      size,
    ),
  arrow: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M4.500 15.500l10-10M9 5.500h5.500V11" />
        <circle cx="4" cy="16" r="1.300" />
      </>,
      size,
    ),
  arrowmarker: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3.500 16.500l3-1 7-7-2.500-2.500-7 7zM11 6l4.500-2.500L13.500 8z" />
      </>,
      size,
    ),
  arrowup: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 3L4 9.500h3.500V17h5V9.500H16z" />
      </>,
      size,
    ),
  arrowdown: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 17l6-6.500h-3.500V3h-5v7.500H4z" />
      </>,
      size,
    ),
  circle: ({ size }: IconProps) =>
    wrap(
      <>
        <circle cx="10" cy="10" r="6.500" />
        <circle cx="10" cy="10" r="1" />
      </>,
      size,
    ),
  ellipse: ({ size }: IconProps) =>
    wrap(
      <>
        <ellipse cx="10" cy="10" rx="7.500" ry="5" />
      </>,
      size,
    ),
  triangle: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 4l6.500 11.500h-13z" />
      </>,
      size,
    ),
  fib: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 4.5h14M3 8h14M3 11.5h14M3 15.5h14" />
        <path d="M5 15.5l10-11" strokeDasharray="1.5 2" />
      </>,
      size,
    ),
  measure: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 13.5l9.5-9.5 4 4-9.5 9.5z" />
        <path d="M6 10.5l1.5 1.5M8.5 8l1.5 1.5M11 5.5L12.5 7" />
      </>,
      size,
    ),
  text: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M5 5.5v-1h10v1M10 4.5v11M8 15.5h4" />
      </>,
      size,
    ),
  zoom: ({ size }: IconProps) =>
    wrap(
      <>
        <circle cx="8.5" cy="8.5" r="5" />
        <path d="M12.5 12.5l4.5 4.5M8.5 6.5v4M6.5 8.5h4" />
      </>,
      size,
    ),
  magnet: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M5 3.5v7a5 5 0 0010 0v-7M5 7h3M12 7h3" />
      </>,
      size,
    ),
  keep: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 3.5v6M7 9.5h6M5.5 13l4.5 3.5 4.5-3.5" />
      </>,
      size,
    ),
  lock: ({ size }: IconProps) =>
    wrap(
      <>
        <rect x="4.5" y="9" width="11" height="7.5" rx="1.5" />
        <path d="M7 9V6.5a3 3 0 016 0V9" />
      </>,
      size,
    ),
  unlock: ({ size }: IconProps) =>
    wrap(
      <>
        <rect x="4.5" y="9" width="11" height="7.5" rx="1.5" />
        <path d="M7 9V6.5a3 3 0 015.6-1.5" />
      </>,
      size,
    ),
  eye: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10z" />
        <circle cx="10" cy="10" r="2.2" />
      </>,
      size,
    ),
  eyeOff: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M2 10s3-5.5 8-5.5S18 10 18 10s-3 5.5-8 5.5S2 10 2 10z" />
        <path d="M3.5 16.5l13-13" />
      </>,
      size,
    ),
  trash: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M4 6h12M8 6V4h4v2M5.5 6l.8 10h7.4l.8-10M8.5 9v4.5M11.5 9v4.5" />
      </>,
      size,
    ),
  search: ({ size }: IconProps) =>
    wrap(
      <>
        <circle cx="8.5" cy="8.5" r="5" />
        <path d="M12.5 12.5L17 17" />
      </>,
      size,
    ),
  plus: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 4v12M4 10h12" />
      </>,
      size,
    ),
  moveTo: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3.5 10h10M10 6.5l3.5 3.5-3.5 3.5M16.5 4.5v11" />
      </>,
      size,
    ),
  check: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M4.5 10.5l3.5 3.5 7.5-8" />
      </>,
      size,
    ),
  indicators: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 14l3.5-5 3 3L14 5l3 4" />
        <path d="M3 17h14" />
      </>,
      size,
    ),
  candles: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M6 3v3M6 13v4M14 5v2M14 15v3" />
        <rect x="4" y="6" width="4" height="7" />
        <rect x="12" y="7" width="4" height="8" />
      </>,
      size,
    ),
  bars: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M6 3v14M3.5 7H6M6 13h2.5M14 4v13M11.5 8H14M14 14h2.5" />
      </>,
      size,
    ),
  line: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 14l4-5 3 3 4-6 3 3" />
      </>,
      size,
    ),
  area: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 15l4-5 3 3 4-6 3 3v6H3z" />
      </>,
      size,
    ),
  undo: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M7 6L3.5 9.5 7 13M4 9.5h8a4 4 0 010 8h-2" />
      </>,
      size,
    ),
  redo: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M13 6l3.5 3.5L13 13M16 9.5H8a4 4 0 000 8h2" />
      </>,
      size,
    ),
  camera: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 7h3l1.5-2h5L14 7h3v9H3z" />
        <circle cx="10" cy="11" r="2.8" />
      </>,
      size,
    ),
  fullscreen: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3.5 7.5v-4h4M12.5 3.5h4v4M16.5 12.5v4h-4M7.5 16.5h-4v-4" />
      </>,
      size,
    ),
  footprint: ({ size }: IconProps) =>
    wrap(
      <>
        <rect x="3" y="3" width="14" height="14" rx="1" />
        <path d="M10 3v14M3 7.5h14M3 12.5h14" />
      </>,
      size,
    ),
  delta: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 3.5L16.5 15.5H3.5L10 3.5Z" />
        <path d="M10 8.5v3.5" />
      </>,
      size,
    ),
  cvd: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M2.5 14l4-4 3 2.5 3-6 4.5 3" />
        <path d="M2.5 17.5h15" />
      </>,
      size,
    ),
  fvg: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 3.5v4.5M3 12v4.5M10 6v8M17 3.5v4.5M17 12v4.5" />
        <path d="M4.5 8h11v4h-11z" strokeDasharray="2 2" />
      </>,
      size,
    ),
  poc: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 4.5h4M3 7.5h7M3 10h11M3 12.5h6M3 15.5h3" />
        <path d="M2.5 10h15" strokeDasharray="1.5 1.5" />
      </>,
      size,
    ),
  gear: ({ size }: IconProps) =>
    wrap(
      <>
        <circle cx="10" cy="10" r="2.5" />
        <path d="M10 3v2M10 15v2M3 10h2M15 10h2M5 5l1.4 1.4M13.6 13.6L15 15M15 5l-1.4 1.4M6.4 13.6L5 15" />
      </>,
      size,
    ),
  chevron: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M6 8l4 4 4-4" />
      </>,
      size,
    ),
  arrowUp: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M10 16V4M5 9l5-5 5 5" />
      </>,
      size,
    ),
  flag: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M5.5 17V3.5M5.5 4h9l-2.25 3.5L14.5 11h-9" />
      </>,
      size,
    ),
  refresh: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M16 10a6 6 0 1 1-1.76-4.24" />
        <path d="M16 3.5v3.5h-3.5" />
      </>,
      size,
    ),
  restore: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M7.5 3.5v4h-4M12.5 3.5v4h4M12.5 16.5v-4h4M7.5 16.5v-4h-4" />
      </>,
      size,
    ),
  collapse: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M6 4l4 4 4-4M6 16l4-4 4 4" />
      </>,
      size,
    ),
  expand: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M6 7l4-4 4 4M6 13l4 4 4-4" />
      </>,
      size,
    ),
  close: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M5 5l10 10M15 5L5 15" />
      </>,
      size,
    ),
  home: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3.5 10L10 4l6.5 6M5.5 9v7h9V9" />
      </>,
      size,
    ),
  list: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M4 5.5h12M4 10h12M4 14.5h12" />
        <circle cx="2.2" cy="5.5" r=".4" />
        <circle cx="2.2" cy="10" r=".4" />
        <circle cx="2.2" cy="14.5" r=".4" />
      </>,
      size,
    ),
  bolt: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M11 2.5L4.5 11h4.5l-1 6.5L15.5 9H11z" />
      </>,
      size,
    ),
  replay: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M9 5L3.5 10 9 15zM16.5 5L11 10l5.5 5z" />
      </>,
      size,
    ),
  play: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M6.5 4.5v11l9-5.5z" />
      </>,
      size,
    ),
  pause: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M6.5 4.5v11M13.5 4.5v11" strokeWidth="2" />
      </>,
      size,
    ),
  stepForward: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M5 4.5v11l8-5.5zM15.5 4.5v11" />
      </>,
      size,
    ),
  sessions: ({ size }: IconProps) =>
    wrap(
      <>
        <rect x="3" y="4" width="5" height="12" fill="currentColor" fillOpacity="0.25" />
        <path d="M10 3v14M3 4h14M3 16h14" strokeDasharray="2 2" />
      </>,
      size,
    ),
  vprofile: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M4 3v14M4 5h8M4 8h5M4 11h10M4 14h6" />
      </>,
      size,
    ),
  long: ({ size }: IconProps) => positionIcon('L', size),
  short: ({ size }: IconProps) => positionIcon('S', size),
  copy: ({ size }: IconProps) =>
    wrap(
      <>
        <rect x="7" y="7" width="9" height="9" rx="1" />
        <path d="M4 13V4h9" />
      </>,
      size,
    ),
  screener: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M3 4.5h14l-5.2 6.2V16l-3.6-1.8v-3.5z" />
      </>,
      size,
    ),
  notes: ({ size }: IconProps) =>
    wrap(
      <>
        <path d="M4.5 3.5h11v13h-11z" />
        <path d="M7 7.5h6M7 10.5h6M7 13.5h3.5" />
      </>,
      size,
    ),
};
