import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_share_ai.market.baostock_market_context import (
    MARKET_CONTEXT_FIELDS,
    BaostockMarketContextSource,
    MarketContextProviderError,
)
from a_share_ai.market.market_context import (
    INDEX_SPECS,
    MarketContextConfig,
    MarketContextError,
)

CALENDAR = Path("fixtures/market/calendar/sample.json")
FIELDS = list(MARKET_CONTEXT_FIELDS)
DATES = ("2026-01-02", "2026-01-05", "2026-01-06")


def _rows(provider_symbol: str, dates: tuple[str, ...] = DATES) -> list[list[str]]:
    return [
        [day, provider_symbol, "10.00", "10.50", "9.80", "10.20", "1000", "10200"]
        for day in dates
    ]


class FakeResponse:
    def __init__(self, rows=None, fields=None, error_code="0", error_msg="success"):
        self.rows = rows if rows is not None else []
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
    def __init__(self, responses=None):
        self.responses = responses or {
            provider: FakeResponse(rows=_rows(provider))
            for _symbol, provider, _name in INDEX_SPECS
        }
        self.logged_in = False
        self.logged_out = False
        self.query_args: list[tuple[tuple, dict]] = []

    def login(self):
        self.logged_in = True
        return SimpleNamespace(error_code="0", error_msg="success")

    def query_history_k_data_plus(self, *args, **kwargs):
        self.query_args.append((args, kwargs))
        return self.responses[args[0]]

    def logout(self):
        self.logged_out = True


def config() -> MarketContextConfig:
    return MarketContextConfig(
        start=date(2026, 1, 2),
        end=date(2026, 1, 6),
        as_of=datetime.fromisoformat("2026-01-06T12:00:00+00:00"),
        received_at=datetime.fromisoformat("2026-01-07T00:00:00+00:00"),
    )


def test_config_requires_point_in_time_order() -> None:
    with pytest.raises(MarketContextError, match="received_at"):
        MarketContextConfig(
            start=date(2026, 1, 2),
            end=date(2026, 1, 6),
            as_of=datetime.fromisoformat("2026-01-06T12:00:00+00:00"),
            received_at=datetime.fromisoformat("2026-01-06T11:00:00+00:00"),
        )


def test_capture_reads_three_fixed_indexes_and_writes_auditable_snapshot(tmp_path: Path) -> None:
    client = FakeClient()
    report = BaostockMarketContextSource(config(), client=client).capture(
        calendar_path=CALENDAR,
        output_dir=tmp_path,
    )

    assert report["market_context_ready"] is True
    assert report["decision_ready"] is False
    assert report["index_symbols"] == [symbol for symbol, _provider, _name in INDEX_SPECS]
    assert all(item["status"] == "ready" for item in report["index_reports"].values())
    assert client.logged_in is True
    assert client.logged_out is True
    assert [args[0][0] for args in client.query_args] == [
        provider for _symbol, provider, _name in INDEX_SPECS
    ]
    assert all(args[1]["adjustflag"] == "3" for args in client.query_args)

    snapshot = json.loads((tmp_path / "market_context_snapshot.json").read_text(encoding="utf-8"))
    assert len(snapshot["indexes"]) == 9
    assert {item["instrument_type"] for item in snapshot["indexes"]} == {"index"}
    assert all((tmp_path / name).exists() for name in (
        "request.json",
        "raw_response.json",
        "market_context_snapshot.json",
        "market_context_report.json",
    ))


def test_capture_is_deterministic_for_same_provider_response(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    BaostockMarketContextSource(config(), client=FakeClient()).capture(
        calendar_path=CALENDAR, output_dir=first
    )
    BaostockMarketContextSource(config(), client=FakeClient()).capture(
        calendar_path=CALENDAR, output_dir=second
    )

    for name in (
        "request.json",
        "raw_response.json",
        "market_context_snapshot.json",
        "market_context_report.json",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_capture_fails_closed_when_one_index_is_incomplete(tmp_path: Path) -> None:
    responses = {
        provider: FakeResponse(
            rows=_rows(provider, DATES if provider != "sz.399006" else DATES[:2])
        )
        for _symbol, provider, _name in INDEX_SPECS
    }
    report = BaostockMarketContextSource(
        config(), client=FakeClient(responses)
    ).capture(
        calendar_path=CALENDAR,
        output_dir=tmp_path,
    )

    assert report["market_context_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] == "MISSING_TRADING_DATE"


def test_capture_maps_reordered_fields_and_rejects_non_trading_date(tmp_path: Path) -> None:
    reordered = ["amount", "close", "date", "code", "volume", "open", "low", "high"]
    base = _rows("sh.000001")
    reordered_rows = [[row[FIELDS.index(field)] for field in reordered] for row in base]
    query_responses = {
        provider: FakeResponse(rows=_rows(provider))
        for _symbol, provider, _name in INDEX_SPECS
    }
    query_responses["sh.000001"] = FakeResponse(rows=reordered_rows, fields=reordered)
    records, _request, _raw = BaostockMarketContextSource(
        config(), client=FakeClient(query_responses)
    )._query()
    assert str(records[0].close) == "10.20"

    capture_responses = {
        provider: FakeResponse(rows=_rows(provider))
        for _symbol, provider, _name in INDEX_SPECS
    }
    capture_responses["sh.000001"] = FakeResponse(rows=reordered_rows, fields=reordered)
    capture_responses["sz.399001"] = FakeResponse(
        rows=_rows("sz.399001", ("2026-01-04",))
    )
    report = BaostockMarketContextSource(
        config(), client=FakeClient(capture_responses)
    ).capture(
        calendar_path=CALENDAR,
        output_dir=tmp_path,
    )
    assert report["market_context_ready"] is False
    assert report["issues"][0]["code"] == "NON_TRADING_DATE"


def test_provider_error_still_logs_out() -> None:
    responses = {
        provider: FakeResponse(rows=_rows(provider))
        for _symbol, provider, _name in INDEX_SPECS
    }
    responses["sz.399001"] = FakeResponse(error_code="1001", error_msg="unavailable")
    client = FakeClient(responses)

    with pytest.raises(MarketContextProviderError, match="unavailable"):
        BaostockMarketContextSource(config(), client=client)._query()
    assert client.logged_out is True
