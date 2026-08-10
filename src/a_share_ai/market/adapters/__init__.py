"""Optional external market-data adapters."""

from .baostock_daily import (
    BAOSTOCK_FIELDS,
    BaostockDailyConfig,
    BaostockDailySource,
    BaostockError,
    provider_symbol_for,
)

__all__ = [
    "BAOSTOCK_FIELDS",
    "BaostockDailyConfig",
    "BaostockDailySource",
    "BaostockError",
    "provider_symbol_for",
]
