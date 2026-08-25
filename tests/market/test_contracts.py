from datetime import date, datetime
from decimal import Decimal

import pytest

from a_share_ai.market.contracts import ContractError, DailyBar, DataStatus


def record(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "symbol": "600000.SH",
        "trade_date": "2026-08-10",
        "open": "10.00",
        "high": "10.50",
        "low": "9.90",
        "close": "10.30",
        "volume": "1000",
        "amount": "10300.00",
        "source": "test",
        "market_time": "2026-08-10T15:00:00+08:00",
        "received_at": "2026-08-10T15:05:00+08:00",
        "data_status": "connected",
    }
    value.update(overrides)
    return value


def test_contract_preserves_decimal_values_and_timezone() -> None:
    bar = DailyBar.from_mapping(record())

    assert bar.symbol == "600000.SH"
    assert bar.trade_date == date(2026, 8, 10)
    assert bar.close == Decimal("10.30")
    assert bar.market_time == datetime.fromisoformat("2026-08-10T15:00:00+08:00")
    assert bar.data_status is DataStatus.CONNECTED


def test_contract_rejects_missing_field() -> None:
    value = record()
    del value["received_at"]

    with pytest.raises(ContractError, match="received_at"):
        DailyBar.from_mapping(value)


def test_contract_rejects_naive_timestamp() -> None:
    with pytest.raises(ContractError, match="timezone"):
        DailyBar.from_mapping(record(market_time="2026-08-10T15:00:00"))


def test_contract_normalizes_symbol_and_serializes_exactly() -> None:
    bar = DailyBar.from_mapping(record(symbol=" 600000.sh "))

    assert bar.to_mapping()["symbol"] == "600000.SH"
    assert bar.to_mapping()["close"] == "10.30"
