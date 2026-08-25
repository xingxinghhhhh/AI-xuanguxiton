"""Fixed, read-only benchmark-index context contracts and validation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .calendar import TradingCalendar, expected_trading_dates
from .replay import sha256_bytes, write_atomic

MARKET_CONTEXT_VERSION = "market-context-v1"
INDEX_INSTRUMENT_TYPE = "index"
SHANGHAI = ZoneInfo("Asia/Shanghai")
INDEX_SPECS = (
    ("000001.SH", "sh.000001", "上证综合指数"),
    ("399001.SZ", "sz.399001", "深证成指"),
    ("399006.SZ", "sz.399006", "创业板指"),
)
INDEX_SYMBOLS = tuple(spec[0] for spec in INDEX_SPECS)


class MarketContextError(ValueError):
    """Raised when benchmark context cannot satisfy its fixed contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise MarketContextError("DECIMAL_INVALID", f"{field} must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketContextError("DECIMAL_INVALID", f"{field} must be decimal") from exc
    if not parsed.is_finite():
        raise MarketContextError("DECIMAL_INVALID", f"{field} must be finite")
    return parsed


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise MarketContextError("DATE_INVALID", f"{field} must be ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MarketContextError("DATE_INVALID", f"{field} must be ISO date") from exc


def _datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise MarketContextError("TIME_INVALID", f"{field} must be ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketContextError("TIME_INVALID", f"{field} must be ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketContextError("TIME_INVALID", f"{field} must include timezone")
    return parsed


def _decimal_mapping(value: Decimal) -> str:
    return str(value)


@dataclass(frozen=True, slots=True)
class MarketIndexRecord:
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
    data_status: str = "connected"
    instrument_type: str = INDEX_INSTRUMENT_TYPE

    @classmethod
    def from_mapping(cls, record: Mapping[str, Any]) -> MarketIndexRecord:
        required = (
            "symbol",
            "instrument_type",
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
        missing = [field for field in required if field not in record]
        if missing:
            raise MarketContextError(
                "RECORD_FIELDS_INVALID", f"missing fields: {', '.join(missing)}"
            )
        symbol = record["symbol"]
        if symbol not in INDEX_SYMBOLS:
            raise MarketContextError("INDEX_INVALID", f"unsupported index: {symbol!r}")
        if record["instrument_type"] != INDEX_INSTRUMENT_TYPE:
            raise MarketContextError("INDEX_INVALID", "instrument_type must be index")
        source = record["source"]
        if not isinstance(source, str) or not source.strip():
            raise MarketContextError("RECORD_FIELDS_INVALID", "source must be non-empty")
        if record["data_status"] != "connected":
            raise MarketContextError("RECORD_FIELDS_INVALID", "data_status must be connected")
        parsed = cls(
            symbol=symbol,
            trade_date=_date(record["trade_date"], "trade_date"),
            open=_decimal(record["open"], "open"),
            high=_decimal(record["high"], "high"),
            low=_decimal(record["low"], "low"),
            close=_decimal(record["close"], "close"),
            volume=_decimal(record["volume"], "volume"),
            amount=_decimal(record["amount"], "amount"),
            source=source.strip(),
            market_time=_datetime(record["market_time"], "market_time"),
            received_at=_datetime(record["received_at"], "received_at"),
            data_status=record["data_status"],
            instrument_type=record["instrument_type"],
        )
        if parsed.market_time.astimezone(SHANGHAI).date() != parsed.trade_date:
            raise MarketContextError(
                "MARKET_TIME_INVALID", "market_time date differs from trade_date"
            )
        if parsed.received_at < parsed.market_time:
            raise MarketContextError("RECEIVED_AT_INVALID", "received_at precedes market_time")
        if min(
            parsed.open,
            parsed.high,
            parsed.low,
            parsed.close,
            parsed.volume,
            parsed.amount,
        ) < 0:
            raise MarketContextError(
                "OHLC_INVALID", "prices, volume, and amount must be non-negative"
            )
        if parsed.high < max(parsed.open, parsed.close, parsed.low):
            raise MarketContextError("OHLC_INVALID", "high is below another OHLC value")
        if parsed.low > min(parsed.open, parsed.close, parsed.high):
            raise MarketContextError("OHLC_INVALID", "low is above another OHLC value")
        return parsed

    def to_mapping(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "instrument_type": self.instrument_type,
            "trade_date": self.trade_date.isoformat(),
            "open": _decimal_mapping(self.open),
            "high": _decimal_mapping(self.high),
            "low": _decimal_mapping(self.low),
            "close": _decimal_mapping(self.close),
            "volume": _decimal_mapping(self.volume),
            "amount": _decimal_mapping(self.amount),
            "source": self.source,
            "market_time": self.market_time.isoformat(),
            "received_at": self.received_at.isoformat(),
            "data_status": self.data_status,
        }


@dataclass(frozen=True, slots=True)
class MarketContextConfig:
    start: date
    end: date
    as_of: datetime
    received_at: datetime

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise MarketContextError("DATE_RANGE_INVALID", "start must be on or before end")
        for value, label in ((self.as_of, "as_of"), (self.received_at, "received_at")):
            if value.tzinfo is None or value.utcoffset() is None:
                raise MarketContextError("TIME_INVALID", f"{label} must include timezone")
        if self.received_at < self.as_of:
            raise MarketContextError("RECEIVED_AT_INVALID", "received_at must not precede as_of")

    @property
    def as_of_date(self) -> date:
        return self.as_of.astimezone(SHANGHAI).date()

    def request_mapping(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "end": self.end.isoformat(),
            "frequency": "d",
            "indexes": [
                {"name": name, "provider_symbol": provider, "symbol": symbol}
                for symbol, provider, name in INDEX_SPECS
            ],
            "start": self.start.isoformat(),
            "received_at": self.received_at.isoformat(),
            "fields": [
                "date",
                "code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            ],
        }


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def write_market_context(
    *,
    config: MarketContextConfig,
    calendar: TradingCalendar,
    calendar_sha256: str,
    records: Iterable[MarketIndexRecord],
    request: dict[str, Any],
    raw_response: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Validate and persist one deterministic benchmark context snapshot."""

    try:
        if config.start < calendar.covered_start or config.end > calendar.covered_end:
            raise MarketContextError("CALENDAR_UNKNOWN", "calendar does not cover requested range")
        if config.end > config.as_of_date:
            raise MarketContextError("FUTURE_DATA", "end is after as_of date")
        record_list = list(records)
        expected_dates = set(expected_trading_dates(calendar, config.start, config.end))
        by_symbol: dict[str, list[MarketIndexRecord]] = {symbol: [] for symbol in INDEX_SYMBOLS}
        for record in record_list:
            if record.symbol not in by_symbol:
                raise MarketContextError("INDEX_INVALID", f"unexpected index {record.symbol}")
            if record.trade_date < config.start or record.trade_date > config.end:
                raise MarketContextError("DATE_RANGE_INVALID", "record is outside requested range")
            if record.trade_date > config.as_of_date:
                raise MarketContextError("FUTURE_DATA", "record is after as_of date")
            if record.trade_date not in expected_dates:
                raise MarketContextError(
                    "NON_TRADING_DATE", "record is not a calendar trading date"
                )
            by_symbol[record.symbol].append(record)
        index_reports: dict[str, Any] = {}
        for symbol in INDEX_SYMBOLS:
            rows = sorted(by_symbol[symbol], key=lambda item: item.trade_date)
            dates = [row.trade_date for row in rows]
            if len(dates) != len(set(dates)):
                raise MarketContextError("DUPLICATE_DATE", f"duplicate dates for {symbol}")
            if dates != sorted(dates):
                raise MarketContextError(
                    "DATE_ORDER_INVALID", f"dates are not ordered for {symbol}"
                )
            missing = sorted(expected_dates - set(dates))
            index_reports[symbol] = {
                "record_count": len(rows),
                "start": dates[0].isoformat() if dates else None,
                "end": dates[-1].isoformat() if dates else None,
                "missing_trading_dates": [day.isoformat() for day in missing],
                "status": "ready" if not missing else "incomplete",
            }
            if missing:
                raise MarketContextError("MISSING_TRADING_DATE", f"missing dates for {symbol}")
        all_records = [
            record.to_mapping()
            for symbol in INDEX_SYMBOLS
            for record in sorted(by_symbol[symbol], key=lambda item: item.trade_date)
        ]
        snapshot = {
            "as_of": config.as_of.isoformat(),
            "calendar_version": calendar.calendar_version,
            "indexes": all_records,
            "market_context_version": MARKET_CONTEXT_VERSION,
            "received_at": config.received_at.isoformat(),
            "request": request,
        }
        snapshot_bytes = _json_bytes(snapshot)
        raw_bytes = _json_bytes(raw_response)
        request_bytes = _json_bytes(request)
        report = {
            "as_of": config.as_of.isoformat(),
            "calendar_sha256": calendar_sha256,
            "calendar_version": calendar.calendar_version,
            "decision_ready": False,
            "end": config.end.isoformat(),
            "index_reports": index_reports,
            "index_symbols": list(INDEX_SYMBOLS),
            "market_context_ready": True,
            "market_context_version": MARKET_CONTEXT_VERSION,
            "raw_response_sha256": sha256_bytes(raw_bytes),
            "received_at": config.received_at.isoformat(),
            "request": request,
            "snapshot_sha256": sha256_bytes(snapshot_bytes),
            "start": config.start.isoformat(),
            "status": "ready",
            "issues": [],
        }
    except MarketContextError as exc:
        snapshot = {
            "as_of": config.as_of.isoformat(),
            "indexes": [],
            "market_context_version": MARKET_CONTEXT_VERSION,
            "received_at": config.received_at.isoformat(),
            "request": request,
        }
        snapshot_bytes = _json_bytes(snapshot)
        raw_bytes = _json_bytes(raw_response)
        request_bytes = _json_bytes(request)
        report = {
            "as_of": config.as_of.isoformat(),
            "calendar_sha256": calendar_sha256,
            "calendar_version": calendar.calendar_version,
            "decision_ready": False,
            "end": config.end.isoformat(),
            "index_reports": {},
            "index_symbols": list(INDEX_SYMBOLS),
            "market_context_ready": False,
            "market_context_version": MARKET_CONTEXT_VERSION,
            "raw_response_sha256": sha256_bytes(raw_bytes),
            "received_at": config.received_at.isoformat(),
            "request": request,
            "snapshot_sha256": sha256_bytes(snapshot_bytes),
            "start": config.start.isoformat(),
            "status": "invalid",
            "issues": [{"code": exc.code, "message": str(exc)}],
        }
    write_atomic(output_dir / "request.json", request_bytes)
    write_atomic(output_dir / "raw_response.json", raw_bytes)
    write_atomic(output_dir / "market_context_snapshot.json", snapshot_bytes)
    write_atomic(output_dir / "market_context_report.json", _json_bytes(report))
    return report
