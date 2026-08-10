"""Versioned, provider-neutral trading-calendar contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

CALENDAR_SCHEMA_VERSION = "1.0"
CALENDAR_REQUIRED_FIELDS = (
    "schema_version",
    "calendar_version",
    "market",
    "timezone",
    "covered_start",
    "covered_end",
    "trading_dates",
)


class CalendarError(ValueError):
    """Raised when a trading-calendar document is invalid."""


def _parse_calendar_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise CalendarError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarError(f"{field_name} must be an ISO date") from exc


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """A dated, immutable list of trading sessions for one market."""

    calendar_version: str
    market: str
    timezone: str
    covered_start: date
    covered_end: date
    trading_dates: tuple[date, ...]
    schema_version: str = CALENDAR_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, record: Mapping[str, Any]) -> TradingCalendar:
        missing = [field for field in CALENDAR_REQUIRED_FIELDS if field not in record]
        if missing:
            raise CalendarError(f"missing required fields: {', '.join(missing)}")
        schema_version = record["schema_version"]
        if schema_version != CALENDAR_SCHEMA_VERSION:
            raise CalendarError(f"unsupported schema_version: {schema_version!r}")
        calendar_version = record["calendar_version"]
        market = record["market"]
        timezone = record["timezone"]
        if not isinstance(calendar_version, str) or not calendar_version.strip():
            raise CalendarError("calendar_version must be a non-empty string")
        if not isinstance(market, str) or not market.strip():
            raise CalendarError("market must be a non-empty string")
        if timezone != "Asia/Shanghai":
            raise CalendarError("timezone must be Asia/Shanghai")
        covered_start = _parse_calendar_date(record["covered_start"], "covered_start")
        covered_end = _parse_calendar_date(record["covered_end"], "covered_end")
        if covered_start > covered_end:
            raise CalendarError("covered_start must be on or before covered_end")
        raw_dates = record["trading_dates"]
        if not isinstance(raw_dates, list):
            raise CalendarError("trading_dates must be a list")
        trading_dates = tuple(
            _parse_calendar_date(value, f"trading_dates[{index}]")
            for index, value in enumerate(raw_dates)
        )
        if len(set(trading_dates)) != len(trading_dates):
            raise CalendarError("trading_dates must not contain duplicates")
        if tuple(sorted(trading_dates)) != trading_dates:
            raise CalendarError("trading_dates must be in ascending order")
        if any(day < covered_start or day > covered_end for day in trading_dates):
            raise CalendarError("trading_dates must stay within the covered range")
        return cls(
            calendar_version=calendar_version.strip(),
            market=market.strip(),
            timezone=timezone,
            covered_start=covered_start,
            covered_end=covered_end,
            trading_dates=trading_dates,
            schema_version=schema_version,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "calendar_version": self.calendar_version,
            "market": self.market,
            "timezone": self.timezone,
            "covered_start": self.covered_start.isoformat(),
            "covered_end": self.covered_end.isoformat(),
            "trading_dates": [day.isoformat() for day in self.trading_dates],
        }


class TradingCalendarSource(Protocol):
    """Read-only adapter boundary for future calendar providers."""

    @property
    def name(self) -> str:
        """Stable provider name used in audit reports."""

    def load_calendar(self) -> TradingCalendar:
        """Load a normalized calendar without network or write capability."""


class JsonTradingCalendarSource:
    """Load a local JSON calendar fixture."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def name(self) -> str:
        return "json-calendar"

    def load_calendar(self) -> TradingCalendar:
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalendarError(f"cannot read calendar: {exc}") from exc
        if not isinstance(record, dict):
            raise CalendarError("calendar must be a JSON object")
        return TradingCalendar.from_mapping(record)


def expected_trading_dates(
    calendar: TradingCalendar, start: date, end: date
) -> tuple[date, ...]:
    """Return calendar sessions in an inclusive audit range."""

    return tuple(day for day in calendar.trading_dates if start <= day <= end)
