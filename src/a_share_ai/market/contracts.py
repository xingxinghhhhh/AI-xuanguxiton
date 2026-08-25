"""Versioned, provider-neutral contracts for read-only daily market data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol

SCHEMA_VERSION = "1.0"
REQUIRED_FIELDS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "source",
    "market_time",
    "received_at",
    "data_status",
)


class ContractError(ValueError):
    """Raised when an input record cannot be represented by the standard contract."""


class DataStatus(StrEnum):
    CONNECTED = "connected"
    STALE = "stale"
    DISCONNECTED = "disconnected"
    RECOVERED = "recovered"
    INVALID = "invalid"


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ContractError(f"{field_name} must be a decimal value")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ContractError(f"{field_name} must be a decimal value") from exc
    if not parsed.is_finite():
        raise ContractError(f"{field_name} must be finite")
    return parsed


def _parse_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise ContractError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{field_name} must be an ISO date") from exc


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{field_name} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field_name} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{field_name} must include a timezone")
    return parsed


@dataclass(frozen=True, slots=True)
class DailyBar:
    """A provider-neutral, read-only daily OHLCV record."""

    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal
    source: str
    market_time: datetime
    received_at: datetime
    data_status: DataStatus = DataStatus.CONNECTED
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, record: Mapping[str, Any]) -> DailyBar:
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise ContractError(f"missing required fields: {', '.join(missing)}")
        symbol = record["symbol"]
        source = record["source"]
        if not isinstance(symbol, str) or not symbol.strip():
            raise ContractError("symbol must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise ContractError("source must be a non-empty string")
        status_value = record["data_status"]
        try:
            data_status = DataStatus(status_value)
        except ValueError as exc:
            raise ContractError(f"unsupported data_status: {status_value!r}") from exc
        schema_version = record.get("schema_version", SCHEMA_VERSION)
        if schema_version != SCHEMA_VERSION:
            raise ContractError(f"unsupported schema_version: {schema_version!r}")
        return cls(
            symbol=symbol.strip().upper(),
            trade_date=_parse_date(record["trade_date"], "trade_date"),
            open=_parse_decimal(record["open"], "open"),
            high=_parse_decimal(record["high"], "high"),
            low=_parse_decimal(record["low"], "low"),
            close=_parse_decimal(record["close"], "close"),
            volume=_parse_decimal(record["volume"], "volume"),
            amount=_parse_decimal(record["amount"], "amount"),
            source=source.strip(),
            market_time=_parse_datetime(record["market_time"], "market_time"),
            received_at=_parse_datetime(record["received_at"], "received_at"),
            data_status=data_status,
            schema_version=schema_version,
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "trade_date": self.trade_date.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "amount": str(self.amount),
            "source": self.source,
            "market_time": self.market_time.isoformat(),
            "received_at": self.received_at.isoformat(),
            "data_status": self.data_status.value,
        }


class MarketDataSource(Protocol):
    """Read-only adapter boundary for future data providers."""

    @property
    def name(self) -> str:
        """Stable provider name used in reports."""

    def iter_daily_bars(self) -> Iterable[DailyBar]:
        """Yield normalized daily bars without any write or trading capability."""
