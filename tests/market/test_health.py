from datetime import datetime, timedelta

from a_share_ai.market.contracts import DailyBar, DataStatus
from a_share_ai.market.health import build_health_report

AS_OF = datetime.fromisoformat("2026-08-10T12:00:00+00:00")


def bar(**overrides: object) -> DailyBar:
    value: dict[str, object] = {
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
    return DailyBar.from_mapping(value)


def report(bars: list[DailyBar], **kwargs: object):
    return build_health_report(
        bars,
        as_of=AS_OF,
        input_sha256="input",
        output_sha256="output",
        source="test",
        **kwargs,
    )


def test_valid_bars_are_decision_ready() -> None:
    result = report([bar()])

    assert result.final_state == DataStatus.CONNECTED.value
    assert result.decision_ready is True
    assert result.issues == ()


def test_duplicate_record_is_invalid() -> None:
    result = report([bar(), bar()])

    assert result.final_state == DataStatus.INVALID.value
    assert result.decision_ready is False
    assert any(issue.code == "DUPLICATE_RECORD" for issue in result.issues)


def test_date_descending_is_invalid() -> None:
    result = report([bar(trade_date="2026-08-10"), bar(trade_date="2026-08-09")])

    assert result.final_state == DataStatus.INVALID.value
    assert any(issue.code == "DATE_OUT_OF_ORDER" for issue in result.issues)


def test_ohlc_and_negative_volume_are_invalid() -> None:
    result = report([bar(high="9.00", volume="-1")])

    codes = {issue.code for issue in result.issues}
    assert {"OHLC_HIGH_INVALID", "VOLUME_NEGATIVE"} <= codes
    assert result.decision_ready is False


def test_future_data_is_invalid() -> None:
    result = report([bar(trade_date="2026-08-11")])

    assert any(issue.code == "FUTURE_DATA" for issue in result.issues)
    assert result.decision_ready is False


def test_stale_data_is_not_decision_ready() -> None:
    result = report([bar(received_at="2026-08-08T15:05:00+08:00")], stale_after=timedelta(hours=24))

    assert result.final_state == DataStatus.STALE.value
    assert any(issue.code == "STALE_DATA" for issue in result.issues)
    assert result.decision_ready is False


def test_disconnect_and_recovery_are_recorded() -> None:
    result = report(
        [
            bar(data_status="disconnected"),
            bar(trade_date="2026-08-11", data_status="recovered"),
        ]
    )

    states = [item["state"] for item in result.state_history]
    assert states[:2] == ["disconnected", "recovered"]
    assert result.final_state == "invalid"
    assert result.decision_ready is False


def test_empty_input_is_disconnected() -> None:
    result = report([])

    assert result.final_state == DataStatus.DISCONNECTED.value
    assert result.decision_ready is False
