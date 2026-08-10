"""Deterministic daily-bar coverage audits against a local trading calendar."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from .calendar import TradingCalendar, expected_trading_dates
from .contracts import DailyBar

SHANGHAI = ZoneInfo("Asia/Shanghai")


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    MISSING_TRADING_DAY = "missing_trading_day"
    UNEXPECTED_NON_TRADING_DAY = "unexpected_non_trading_day"
    CALENDAR_UNKNOWN = "calendar_unknown"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CoverageIssue:
    code: str
    message: str
    trade_date: str | None = None

    def to_mapping(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CoverageReport:
    schema_version: str
    calendar_version: str | None
    calendar_source: str
    as_of: str
    start: str
    end: str
    bars_sha256: str
    calendar_sha256: str
    expected_count: int
    actual_count: int
    expected_trading_dates: tuple[str, ...]
    actual_dates: tuple[str, ...]
    missing_trading_dates: tuple[str, ...]
    unexpected_non_trading_dates: tuple[str, ...]
    status: str
    decision_ready: bool
    issues: tuple[CoverageIssue, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "calendar_version": self.calendar_version,
            "calendar_source": self.calendar_source,
            "as_of": self.as_of,
            "start": self.start,
            "end": self.end,
            "bars_sha256": self.bars_sha256,
            "calendar_sha256": self.calendar_sha256,
            "expected_count": self.expected_count,
            "actual_count": self.actual_count,
            "expected_trading_dates": list(self.expected_trading_dates),
            "actual_dates": list(self.actual_dates),
            "missing_trading_dates": list(self.missing_trading_dates),
            "unexpected_non_trading_dates": list(self.unexpected_non_trading_dates),
            "status": self.status,
            "decision_ready": self.decision_ready,
            "issues": [issue.to_mapping() for issue in self.issues],
        }


def audit_daily_coverage(
    bars: Iterable[DailyBar],
    *,
    calendar: TradingCalendar | None,
    start: date,
    end: date,
    as_of: datetime,
    bars_sha256: str,
    calendar_sha256: str,
    calendar_source: str,
    calendar_error: str | None = None,
    bars_error: str | None = None,
) -> CoverageReport:
    """Compare unique observed dates to expected sessions and fail closed."""

    bar_list = list(bars)
    actual_dates = tuple(
        sorted({bar.trade_date for bar in bar_list if start <= bar.trade_date <= end})
    )
    issues: list[CoverageIssue] = []
    expected_dates: tuple[date, ...] = ()
    calendar_version: str | None = calendar.calendar_version if calendar else None
    as_of_date = as_of.astimezone(SHANGHAI).date()

    if start > end:
        issues.append(CoverageIssue("INVALID_RANGE", "start must be on or before end"))
        status = CoverageStatus.INVALID
    elif bars_error:
        issues.append(CoverageIssue("INVALID", bars_error))
        status = CoverageStatus.INVALID
    elif calendar is None:
        issues.append(CoverageIssue("CALENDAR_UNKNOWN", calendar_error or "calendar unavailable"))
        status = CoverageStatus.CALENDAR_UNKNOWN
    elif start < calendar.covered_start or end > calendar.covered_end or end > as_of_date:
        issues.append(
            CoverageIssue(
                "CALENDAR_UNKNOWN",
                "calendar does not cover the requested range as of the audit time",
            )
        )
        status = CoverageStatus.CALENDAR_UNKNOWN
    else:
        expected_dates = expected_trading_dates(calendar, start, end)
        expected_set = set(expected_dates)
        actual_set = set(actual_dates)
        missing_dates = tuple(sorted(expected_set - actual_set))
        unexpected_dates = tuple(sorted(actual_set - expected_set))
        if unexpected_dates:
            status = CoverageStatus.UNEXPECTED_NON_TRADING_DAY
            issues.extend(
                CoverageIssue(
                    "UNEXPECTED_NON_TRADING_DAY",
                    "market data exists on a non-trading date",
                    day.isoformat(),
                )
                for day in unexpected_dates
            )
        elif missing_dates:
            status = CoverageStatus.MISSING_TRADING_DAY
            issues.extend(
                CoverageIssue(
                    "MISSING_TRADING_DAY",
                    "expected trading date has no market data",
                    day.isoformat(),
                )
                for day in missing_dates
            )
        else:
            status = CoverageStatus.COMPLETE

    if status in {
        CoverageStatus.COMPLETE,
        CoverageStatus.MISSING_TRADING_DAY,
        CoverageStatus.UNEXPECTED_NON_TRADING_DAY,
    }:
        expected_set = set(expected_dates)
        actual_set = set(actual_dates)
        missing_dates = tuple(sorted(expected_set - actual_set))
        unexpected_dates = tuple(sorted(actual_set - expected_set))
    else:
        missing_dates = ()
        unexpected_dates = ()
    if status is not CoverageStatus.COMPLETE and not issues:
        issues.append(CoverageIssue(status.value, "coverage audit failed"))
    return CoverageReport(
        schema_version="1.0",
        calendar_version=calendar_version,
        calendar_source=calendar_source,
        as_of=as_of.isoformat(),
        start=start.isoformat(),
        end=end.isoformat(),
        bars_sha256=bars_sha256,
        calendar_sha256=calendar_sha256,
        expected_count=len(expected_dates),
        actual_count=len(actual_dates),
        expected_trading_dates=tuple(day.isoformat() for day in expected_dates),
        actual_dates=tuple(day.isoformat() for day in actual_dates),
        missing_trading_dates=tuple(day.isoformat() for day in missing_dates),
        unexpected_non_trading_dates=tuple(day.isoformat() for day in unexpected_dates),
        status=status.value,
        decision_ready=status is CoverageStatus.COMPLETE,
        issues=tuple(issues),
    )
