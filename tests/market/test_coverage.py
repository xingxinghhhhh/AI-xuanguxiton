from datetime import date, datetime
from pathlib import Path

from a_share_ai.market.adapter import JsonlReplaySource
from a_share_ai.market.calendar import JsonTradingCalendarSource
from a_share_ai.market.coverage import CoverageStatus, audit_daily_coverage

CALENDAR = Path(__file__).parents[2] / "fixtures" / "market" / "calendar" / "sample.json"
BARS = Path(__file__).parents[2] / "fixtures" / "market" / "coverage" / "valid_daily.jsonl"
AS_OF = datetime.fromisoformat("2026-01-07T00:00:00+00:00")


def load_bars():
    return list(JsonlReplaySource(BARS).iter_daily_bars())


def load_calendar():
    return JsonTradingCalendarSource(CALENDAR).load_calendar()


def audit(bars, **kwargs):
    start = kwargs.pop("start", date(2026, 1, 2))
    end = kwargs.pop("end", date(2026, 1, 6))
    return audit_daily_coverage(
        bars,
        calendar=load_calendar(),
        start=start,
        end=end,
        as_of=AS_OF,
        bars_sha256="bars",
        calendar_sha256="calendar",
        calendar_source="json-calendar",
        **kwargs,
    )


def test_complete_daily_coverage_is_ready() -> None:
    result = audit(load_bars())

    assert result.status == CoverageStatus.COMPLETE.value
    assert result.decision_ready is True
    assert result.missing_trading_dates == ()
    assert result.unexpected_non_trading_dates == ()


def test_missing_trading_day_blocks_decision() -> None:
    bars = load_bars()[:-1]
    result = audit(bars)

    assert result.status == CoverageStatus.MISSING_TRADING_DAY.value
    assert result.decision_ready is False
    assert result.missing_trading_dates == ("2026-01-06",)


def test_unexpected_non_trading_day_blocks_decision() -> None:
    bars = [bar for bar in load_bars() if bar.trade_date != date(2026, 1, 6)]
    bars.append(
        bars[-1].__class__.from_mapping(
            {
                **bars[-1].to_mapping(),
                "trade_date": "2026-01-03",
                "market_time": "2026-01-03T15:00:00+08:00",
                "received_at": "2026-01-03T15:05:00+08:00",
            }
        )
    )
    result = audit(bars, start=date(2026, 1, 2), end=date(2026, 1, 5))

    assert result.status == CoverageStatus.UNEXPECTED_NON_TRADING_DAY.value
    assert result.decision_ready is False
    assert result.unexpected_non_trading_dates == ("2026-01-03",)


def test_calendar_range_outside_coverage_is_unknown() -> None:
    result = audit(load_bars(), end=date(2026, 1, 7))

    assert result.status == CoverageStatus.CALENDAR_UNKNOWN.value
    assert result.decision_ready is False
    assert any(issue.code == "CALENDAR_UNKNOWN" for issue in result.issues)


def test_missing_calendar_is_unknown() -> None:
    result = audit_daily_coverage(
        load_bars(),
        calendar=None,
        start=date(2026, 1, 2),
        end=date(2026, 1, 6),
        as_of=AS_OF,
        bars_sha256="bars",
        calendar_sha256="calendar",
        calendar_source="json-calendar",
        calendar_error="fixture unavailable",
    )

    assert result.status == CoverageStatus.CALENDAR_UNKNOWN.value
    assert result.decision_ready is False


def test_invalid_bar_input_is_invalid_not_calendar_unknown() -> None:
    result = audit_daily_coverage(
        load_bars(),
        calendar=load_calendar(),
        start=date(2026, 1, 2),
        end=date(2026, 1, 6),
        as_of=AS_OF,
        bars_sha256="bars",
        calendar_sha256="calendar",
        calendar_source="json-calendar",
        bars_error="line 2 is malformed",
    )

    assert result.status == CoverageStatus.INVALID.value
    assert result.decision_ready is False
