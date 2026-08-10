import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_share_ai.market.adapters.baostock_daily import (
    BAOSTOCK_FIELDS,
    BaostockDailyConfig,
    BaostockDailySource,
    BaostockError,
    provider_symbol_for,
)

FIELDS = list(BAOSTOCK_FIELDS)
ROWS = [
    ["2026-01-05", "sh.600000", "10.30", "10.55", "10.20", "10.50", "1300000", "13585000.00"],
    ["2026-01-06", "sh.600000", "10.50", "10.65", "10.35", "10.60", "1350000", "14310000.00"],
]


class FakeResponse:
    def __init__(self, rows=None, fields=None, error_code="0", error_msg="success"):
        self.rows = rows if rows is not None else ROWS
        self.fields = fields if fields is not None else FIELDS
        self.error_code = error_code
        self.error_msg = error_msg
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


class FakeClient:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.logged_in = False
        self.logged_out = False
        self.query_args = None

    def login(self):
        self.logged_in = True
        return SimpleNamespace(error_code="0", error_msg="success")

    def query_history_k_data_plus(self, *args, **kwargs):
        self.query_args = (args, kwargs)
        return self.response

    def logout(self):
        self.logged_out = True


def config() -> BaostockDailyConfig:
    return BaostockDailyConfig(
        symbol="600000.SH",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        received_at=datetime.fromisoformat("2026-02-01T00:00:00+00:00"),
    )


def test_provider_symbol_mapping_is_strict() -> None:
    assert provider_symbol_for("600000.SH") == "sh.600000"
    assert provider_symbol_for("000001.sz") == "sz.000001"
    with pytest.raises(BaostockError):
        provider_symbol_for("600000")


def test_config_fixes_no_adjustment_and_requires_timezone() -> None:
    assert config().adjustflag == "3"
    with pytest.raises(BaostockError, match="timezone"):
        BaostockDailyConfig(
            symbol="600000.SH",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            received_at=datetime(2026, 2, 1),
        )
    with pytest.raises(BaostockError, match="no adjustment"):
        BaostockDailyConfig(
            symbol="600000.SH",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            received_at=datetime.fromisoformat("2026-02-01T00:00:00+00:00"),
            adjustflag="2",
        )


def test_source_maps_reordered_response_fields_to_decimal_bars() -> None:
    reordered_fields = ["amount", "close", "date", "code", "volume", "open", "low", "high"]
    reordered_rows = [[row[FIELDS.index(field)] for field in reordered_fields] for row in ROWS]
    client = FakeClient(FakeResponse(rows=reordered_rows, fields=reordered_fields))

    bars = list(BaostockDailySource(config(), client=client).iter_daily_bars())

    assert [bar.trade_date.isoformat() for bar in bars] == ["2026-01-05", "2026-01-06"]
    assert str(bars[0].close) == "10.50"
    assert bars[0].symbol == "600000.SH"
    assert client.query_args[0][0] == "sh.600000"
    assert client.query_args[1]["adjustflag"] == "3"
    assert client.logged_out is True


def test_source_rejects_empty_required_field_and_logs_out() -> None:
    bad_rows = [ROWS[0].copy()]
    bad_rows[0][5] = ""
    client = FakeClient(FakeResponse(rows=bad_rows))

    with pytest.raises(BaostockError, match="empty required field"):
        list(BaostockDailySource(config(), client=client).iter_daily_bars())
    assert client.logged_out is True


def test_source_rejects_provider_error() -> None:
    client = FakeClient(FakeResponse(error_code="1001", error_msg="provider unavailable"))

    with pytest.raises(BaostockError, match="provider unavailable"):
        list(BaostockDailySource(config(), client=client).iter_daily_bars())
    assert client.logged_out is True


def test_source_wraps_query_exception_and_still_cleans_up() -> None:
    class RaisingClient(FakeClient):
        def query_history_k_data_plus(self, *args, **kwargs):
            raise RuntimeError("network down")

    client = RaisingClient()
    with pytest.raises(BaostockError, match="network down"):
        list(BaostockDailySource(config(), client=client).iter_daily_bars())
    assert client.logged_out is True


def test_capture_writes_raw_response_request_normalized_bars_and_hashes(tmp_path: Path) -> None:
    report = BaostockDailySource(config(), client=FakeClient()).capture(tmp_path)

    assert report["bar_count"] == 2
    assert report["decision_ready"] is False
    assert len(report["raw_response_sha256"]) == 64
    assert len(report["normalized_output_sha256"]) == 64
    assert (tmp_path / "request.json").exists()
    assert (tmp_path / "raw_response.json").exists()
    assert (tmp_path / "normalized_daily.jsonl").exists()
    saved = json.loads((tmp_path / "capture_report.json").read_text(encoding="utf-8"))
    assert saved["request"]["adjustflag"] == "3"
