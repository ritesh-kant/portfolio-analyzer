"""Shared symbol-to-sector lookup used by signal_agent, order_agent, and any future agents.

Import STOCK_TO_SECTOR wherever you need to map a .NS symbol to its sector name.
The dict is built once at import time from the canonical SECTOR_STOCKS source.
"""

from ..scrapers.sector_stocks import SECTOR_STOCKS

STOCK_TO_SECTOR: dict[str, str] = {
    stock: sector
    for sector, stocks in SECTOR_STOCKS.items()
    for stock in stocks
}
