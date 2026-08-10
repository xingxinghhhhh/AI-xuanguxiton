from pathlib import Path

import pytest

from a_share_ai.market.calendar import CalendarError, JsonTradingCalendarSource, TradingCalendar

FIXTURE = Path(__file__).parents[2] / "fixtures" / "market" / "calendar" / "sample.json"


def test_calendar_fixture_is_versioned_and_sorted() -> None:
    calendar = JsonTradingCalendarSource(FIXTURE).load_calendar()

    assert calendar.calendar_version == "cn-a-share-fixture-2026-01-v1"
    assert calendar.timezone == "Asia/Shanghai"
    assert [day.isoformat() for day in calendar.trading_dates] == [
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
    ]


def test_calendar_rejects_duplicate_dates() -> None:
    value = JsonTradingCalendarSource(FIXTURE).load_calendar().to_mapping()
    value["trading_dates"] = ["2026-01-02", "2026-01-02"]

    with pytest.raises(CalendarError, match="duplicates"):
        TradingCalendar.from_mapping(value)


def test_calendar_rejects_descending_dates() -> None:
    value = JsonTradingCalendarSource(FIXTURE).load_calendar().to_mapping()
    value["trading_dates"] = ["2026-01-05", "2026-01-02"]

    with pytest.raises(CalendarError, match="ascending"):
        TradingCalendar.from_mapping(value)


def test_calendar_rejects_dates_outside_covered_range() -> None:
    value = JsonTradingCalendarSource(FIXTURE).load_calendar().to_mapping()
    value["trading_dates"] = ["2026-01-01"]

    with pytest.raises(CalendarError, match="covered range"):
        TradingCalendar.from_mapping(value)
