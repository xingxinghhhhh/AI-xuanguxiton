"""Market contracts, adapters, replay, and health checks."""

from .baostock_market_context import (
    BaostockMarketContextSource,
    MarketContextProviderError,
)
from .calendar import JsonTradingCalendarSource, TradingCalendar, TradingCalendarSource
from .contracts import DailyBar, DataStatus, MarketDataSource
from .coverage import CoverageReport, CoverageStatus
from .market_context import (
    INDEX_SPECS,
    INDEX_SYMBOLS,
    MARKET_CONTEXT_VERSION,
    MarketContextConfig,
    MarketContextError,
    MarketIndexRecord,
    write_market_context,
)

__all__ = [
    "BaostockMarketContextSource",
    "CoverageReport",
    "CoverageStatus",
    "DailyBar",
    "DataStatus",
    "INDEX_SPECS",
    "INDEX_SYMBOLS",
    "JsonTradingCalendarSource",
    "MARKET_CONTEXT_VERSION",
    "MarketContextConfig",
    "MarketContextError",
    "MarketContextProviderError",
    "MarketIndexRecord",
    "MarketDataSource",
    "TradingCalendar",
    "TradingCalendarSource",
    "write_market_context",
]
