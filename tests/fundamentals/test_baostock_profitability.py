import json
from datetime import date
from pathlib import Path

from a_share_ai.fundamentals.baostock_profitability import (
    BaostockProfitabilityConfig,
    BaostockProfitabilitySource,
    ProfitabilityState,
    compute_profitability_snapshot,
)

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "fundamentals" / "profitability"


class FakeResult:
    def __init__(self, *, fields: list[str], rows: list[list[str]], error_code: str = "0") -> None:
        self.fields = fields
        self.rows = rows
        self.error_code = error_code
        self.error_msg = "success" if error_code == "0" else "provider unavailable"
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


class FakeClient:
    def __init__(self, fixture_name: str) -> None:
        payload = json.loads((FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8"))
        self.payload = payload
        self.logged_in = False
        self.logged_out = False

    def login(self) -> FakeResult:
        self.logged_in = True
        return FakeResult(fields=[], rows=[])

    def logout(self) -> None:
        self.logged_out = True

    def query_profit_data(self, *, code: str, year: int, quarter: int) -> FakeResult:
        return FakeResult(
            fields=self.payload.get("fields", []),
            rows=self.payload.get("rows", []),
            error_code=self.payload.get("error_code", "0"),
        )


def make_config(*, as_of: date = date(2026, 8, 10)) -> BaostockProfitabilityConfig:
    return BaostockProfitabilityConfig(
        symbol="600000.SH",
        as_of=as_of,
        start_year=2025,
        start_quarter=1,
        end_year=2025,
        end_quarter=4,
    )


def test_config_generates_explicit_quarter_range() -> None:
    assert make_config().periods == ((2025, 1), (2025, 2), (2025, 3), (2025, 4))


def test_valid_provider_response_selects_latest_published_report(tmp_path: Path) -> None:
    client = FakeClient("valid_reports.json")
    report = BaostockProfitabilitySource(make_config(), client=client).capture(tmp_path)

    assert report["status"] == "ready"
    assert report["fundamental_ready"] is True
    assert report["decision_ready"] is False
    assert report["selected_published_date"] == "2026-03-31"
    snapshot = json.loads((tmp_path / "profitability_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["report_date"] == "2025-12-31"
    assert snapshot["net_profit"] == "140.00000000"
    assert client.logged_in is True
    assert client.logged_out is True


def test_publication_date_and_future_report_states_fail_closed(tmp_path: Path) -> None:
    future_client = FakeClient("future_only.json")
    future_source = BaostockProfitabilitySource(
        make_config(as_of=date(2026, 8, 10)), client=future_client
    )
    future_report = future_source.capture(tmp_path / "future")
    assert future_report["status"] == "future_only"
    assert future_report["fundamental_ready"] is False

    missing = FakeClient("missing_pub_date.json")
    missing_report = BaostockProfitabilitySource(make_config(), client=missing).capture(
        tmp_path / "missing"
    )
    assert missing_report["status"] == "publication_date_unknown"
    assert missing_report["fundamental_ready"] is False


def test_invalid_numeric_and_provider_error_are_fail_closed(tmp_path: Path) -> None:
    invalid_report = BaostockProfitabilitySource(
        make_config(), client=FakeClient("invalid_numeric.json")
    ).capture(tmp_path / "invalid")
    assert invalid_report["status"] == "invalid"
    assert invalid_report["fundamental_ready"] is False

    provider_report = BaostockProfitabilitySource(
        make_config(), client=FakeClient("provider_error.json")
    ).capture(tmp_path / "provider")
    assert provider_report["status"] == "provider_error"
    assert provider_report["fundamental_ready"] is False


def test_future_filter_is_deterministic_without_network() -> None:
    client = FakeClient("valid_reports.json")
    source = BaostockProfitabilitySource(make_config(as_of=date(2025, 6, 1)), client=client)
    capture = source._query()
    computation = compute_profitability_snapshot(list(capture.records), as_of=date(2025, 6, 1))

    assert computation.status is ProfitabilityState.READY
    assert computation.snapshot is not None
    assert computation.snapshot.report_date == "2025-03-31"
