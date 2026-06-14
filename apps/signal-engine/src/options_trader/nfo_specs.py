"""NFO single-stock option specs — lot size + strike step per symbol.

Sourced from the Zerodha public instruments dump (api.kite.trade/instruments,
NFO-OPT segment) on 2026-06-13. Lot sizes drift quarterly — regenerate from
research/backtests/ before any important production decision.

NOT_FO: symbols in the news signal cohort with NO single-stock options; the
options strategy drops these silently (has_options() returns False).
"""

# symbol -> (lot_size, strike_step)
NFO_SPECS: dict[str, tuple[int, float]] = {
    'ADANIENSOL': (675, 20.0),
    'ADANIPORTS': (475, 20.0),
    'ALKEM': (125, 50.0),
    'APOLLOHOSP': (125, 50.0),
    'ASHOKLEY': (5000, 2.5),
    'AUROPHARMA': (550, 20.0),
    'AXISBANK': (625, 10.0),
    'BAJAJ-AUTO': (75, 100.0),
    'BAJFINANCE': (750, 10.0),
    'BEL': (1425, 5.0),
    'BHARTIARTL': (475, 20.0),
    'BHEL': (2625, 2.5),
    'BPCL': (1975, 5.0),
    'BRITANNIA': (125, 50.0),
    'CDSL': (475, 20.0),
    'CGPOWER': (850, 10.0),
    'COFORGE': (375, 20.0),
    'GAIL': (3150, 1.0),
    'GLENMARK': (375, 20.0),
    'HAL': (150, 50.0),
    'HCLTECH': (350, 10.0),
    'HDFCAMC': (300, 14.0),
    'HDFCBANK': (550, 5.0),
    'HEROMOTOCO': (150, 50.0),
    'HINDPETRO': (2025, 5.0),
    'HINDUNILVR': (300, 20.0),
    'HINDZINC': (1225, 10.0),
    'ICICIBANK': (700, 10.0),
    'ICICIGI': (325, 20.0),
    'IDEA': (71475, 1.0),
    'INDIGO': (150, 50.0),
    'INDUSINDBK': (700, 10.0),
    'INFY': (400, 5.0),
    'INOXWIND': (3575, 1.0),
    'IOC': (4875, 1.0),
    'ITC': (1600, 2.0),
    'JSWENERGY': (1000, 5.0),
    'KALYANKJIL': (1175, 5.0),
    'KOTAKBANK': (2000, 2.5),
    'LUPIN': (425, 20.0),
    'M&M': (200, 20.0),
    'MANAPPURAM': (3000, 5.0),
    'MARUTI': (50, 100.0),
    'MCX': (625, 50.0),
    'MUTHOOTFIN': (275, 50.0),
    'NBCC': (6500, 1.0),
    'NHPC': (6400, 1.0),
    'NMDC': (6750, 1.0),
    'NTPC': (1500, 2.5),
    'NUVAMA': (500, 20.0),
    'OIL': (1400, 5.0),
    'ONGC': (2250, 2.5),
    'PATANJALI': (900, 5.0),
    'PERSISTENT': (100, 50.0),
    'PIIND': (175, 20.0),
    'POWERGRID': (1900, 2.5),
    'PRESTIGE': (450, 20.0),
    'RELIANCE': (500, 10.0),
    'SBICARD': (800, 10.0),
    'SBIN': (750, 10.0),
    'SUZLON': (9025, 1.0),
    'TATAELXSI': (100, 50.0),
    'TCS': (175, 20.0),
    'TECHM': (600, 20.0),
    'TITAN': (175, 50.0),
    'TRENT': (150, 20.0),
    'UPL': (1355, 5.0),
    'VEDL': (1150, 10.0),
    'WIPRO': (3000, 2.5),
    'ZYDUSLIFE': (900, 10.0),
}

NOT_FO: set[str] = {
    'AARTIIND', 'AFCONS', 'ANANDRATHI', 'CHAMBLFERT', 'COROMANDEL',
    'DATAPATTNS', 'DEEPAKFERT', 'GODIGIT', 'GRAVITA', 'IFCI', 'IGL',
    'INTELLECT', 'IPCALAB', 'LTTS', 'MGL', 'MRPL', 'NATCOPHARM',
    'PPLPHARMA', 'RAILTEL', 'TATACOMM', 'ZEEL',
}


def has_options(symbol: str) -> bool:
    return symbol in NFO_SPECS


def lot_size(symbol: str) -> int:
    return NFO_SPECS[symbol][0]


def strike_step(symbol: str) -> float:
    return NFO_SPECS[symbol][1]
