"""Fail-closed validation and health reporting for replayed daily bars."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .contracts import DailyBar, DataStatus

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    symbol: str | None = None
    trade_date: str | None = None
    line_number: int | None = None

    def to_mapping(self) -> dict[str, str | int | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HealthReport:
    schema_version: str
    source: str
    as_of: str
    input_sha256: str
    output_sha256: str
    bar_count: int
    symbols: tuple[str, ...]
    first_trade_date: str | None
    last_trade_date: str | None
    latest_received_at: str | None
    final_state: str
    decision_ready: bool
    state_history: tuple[dict[str, str], ...]
    issues: tuple[ValidationIssue, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "as_of": self.as_of,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "bar_count": self.bar_count,
            "symbols": list(self.symbols),
            "first_trade_date": self.first_trade_date,
            "last_trade_date": self.last_trade_date,
            "latest_received_at": self.latest_received_at,
            "final_state": self.final_state,
            "decision_ready": self.decision_ready,
            "state_history": list(self.state_history),
            "issues": [issue.to_mapping() for issue in self.issues],
        }


def _issue(code: str, message: str, bar: DailyBar | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        symbol=bar.symbol if bar else None,
        trade_date=bar.trade_date.isoformat() if bar else None,
    )


def _check_bar(bar: DailyBar, *, as_of: datetime) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    positive_prices = (bar.open, bar.high, bar.low, bar.close)
    if any(price <= 0 for price in positive_prices):
        issues.append(_issue("OHLC_NON_POSITIVE", "OHLC prices must be positive", bar))
    if bar.high < max(bar.open, bar.close, bar.low):
        issues.append(_issue("OHLC_HIGH_INVALID", "high must be >= open, low and close", bar))
    if bar.low > min(bar.open, bar.close, bar.high):
        issues.append(_issue("OHLC_LOW_INVALID", "low must be <= open, high and close", bar))
    if bar.volume < 0:
        issues.append(_issue("VOLUME_NEGATIVE", "volume cannot be negative", bar))
    if bar.amount < 0:
        issues.append(_issue("AMOUNT_NEGATIVE", "amount cannot be negative", bar))
    as_of_shanghai = as_of.astimezone(SHANGHAI)
    if bar.trade_date > as_of_shanghai.date():
        issues.append(_issue("FUTURE_DATA", "trade_date is after as_of", bar))
    if bar.market_time > as_of or bar.received_at > as_of:
        issues.append(_issue("FUTURE_DATA", "market or receive time is after as_of", bar))
    if bar.data_status is DataStatus.INVALID:
        issues.append(_issue("UPSTREAM_INVALID", "record is marked invalid by its source", bar))
    return issues


def build_health_report(
    bars: Iterable[DailyBar],
    *,
    as_of: datetime,
    input_sha256: str,
    output_sha256: str,
    source: str,
    parse_issues: Iterable[ValidationIssue] = (),
    stale_after: timedelta = timedelta(hours=24),
) -> HealthReport:
    """Validate bars and return a deterministic, fail-closed health report."""

    bar_list = list(bars)
    issues = list(parse_issues)
    previous_dates: dict[str, date] = {}
    seen: set[tuple[str, date]] = set()
    state_history: list[dict[str, str]] = []
    current_state: DataStatus | None = None

    def record_state(state: DataStatus, reason: str) -> None:
        nonlocal current_state
        if current_state is state:
            return
        state_history.append({"state": state.value, "reason": reason})
        current_state = state

    for bar in bar_list:
        key = (bar.symbol, bar.trade_date)
        if key in seen:
            issues.append(_issue("DUPLICATE_RECORD", "duplicate symbol and trade_date", bar))
        seen.add(key)
        previous_date = previous_dates.get(bar.symbol)
        if previous_date is not None and bar.trade_date < previous_date:
            issues.append(_issue("DATE_OUT_OF_ORDER", "trade_date is in descending order", bar))
        previous_dates[bar.symbol] = bar.trade_date
        bar_issues = _check_bar(bar, as_of=as_of)
        issues.extend(bar_issues)

        if bar.data_status is DataStatus.DISCONNECTED:
            record_state(DataStatus.DISCONNECTED, "source_reported_disconnect")
        elif bar.data_status is DataStatus.RECOVERED or current_state is DataStatus.DISCONNECTED:
            record_state(DataStatus.RECOVERED, "data_resumed")
        elif bar.data_status is DataStatus.INVALID or bar_issues:
            record_state(DataStatus.INVALID, "record_validation_failed")
        else:
            record_state(DataStatus.CONNECTED, "valid_record")

    latest_received = max((bar.received_at for bar in bar_list), default=None)
    stale_detected = False
    if latest_received is not None and as_of - latest_received > stale_after:
        stale_detected = True
        record_state(DataStatus.STALE, "latest_record_is_too_old")
        issues.append(
            ValidationIssue(
                code="STALE_DATA",
                message=(
                    "latest received_at is older than "
                    f"{int(stale_after.total_seconds())} seconds"
                ),
            )
        )
    if not bar_list and not issues:
        record_state(DataStatus.DISCONNECTED, "no_records")
    if any(issue.code != "STALE_DATA" for issue in issues):
        record_state(DataStatus.INVALID, "one_or_more_validation_issues")

    final_state = current_state or DataStatus.DISCONNECTED
    decision_ready = final_state is DataStatus.CONNECTED and not issues and not stale_detected
    dates = [bar.trade_date for bar in bar_list]
    return HealthReport(
        schema_version="1.0",
        source=source,
        as_of=as_of.isoformat(),
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        bar_count=len(bar_list),
        symbols=tuple(sorted({bar.symbol for bar in bar_list})),
        first_trade_date=min(dates).isoformat() if dates else None,
        last_trade_date=max(dates).isoformat() if dates else None,
        latest_received_at=latest_received.isoformat() if latest_received else None,
        final_state=final_state.value,
        decision_ready=decision_ready,
        state_history=tuple(state_history),
        issues=tuple(issues),
    )
