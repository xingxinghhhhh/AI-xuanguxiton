import json
from datetime import date
from pathlib import Path

from a_share_ai.fundamentals.baostock_growth import (
    BaostockGrowthConfig,
    BaostockGrowthSource,
    GrowthState,
    compute_growth_snapshot,
)

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "fundamentals" / "growth"


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
        self.payload = json.loads((FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8"))
        self.logged_in = False
        self.logged_out = False

    def login(self) -> FakeResult:
        self.logged_in = True
        return FakeResult(fields=[], rows=[])

    def logout(self) -> None:
        self.logged_out = True

    def query_growth_data(self, *, code: str, year: int, quarter: int) -> FakeResult:
        return FakeResult(
            fields=self.payload.get("fields", []),
            rows=self.payload.get("rows", []),
            error_code=self.payload.get("error_code", "0"),
        )


def make_config(*, as_of: date = date(2026, 8, 10)) -> BaostockGrowthConfig:
    return BaostockGrowthConfig(
        symbol="600000.SH",
        as_of=as_of,
        start_year=2025,
        start_quarter=1,
        end_year=2025,
        end_quarter=4,
    )


def test_valid_growth_selects_latest_and_keeps_missing_optional_fields_null(
    tmp_path: Path,
) -> None:
    client = FakeClient("valid_reports.json")
    report = BaostockGrowthSource(make_config(), client=client).capture(tmp_path)

    assert report["status"] == "ready"
    assert report["growth_ready"] is True
    assert report["decision_ready"] is False
    assert report["selected_published_date"] == "2026-03-31"
    snapshot = json.loads((tmp_path / "growth_snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["report_date"] == "2025-12-31"
    assert snapshot["yoy_net_income"] == "0.32000000"
    assert snapshot["yoy_operating_revenue"] is None
    assert client.logged_out is True


def test_future_and_unknown_publication_fail_closed(tmp_path: Path) -> None:
    future_report = BaostockGrowthSource(
        make_config(), client=FakeClient("future_only.json")
    ).capture(tmp_path / "future")
    assert future_report["status"] == "future_only"
    assert future_report["growth_ready"] is False

    unknown_report = BaostockGrowthSource(
        make_config(), client=FakeClient("missing_pub_date.json")
    ).capture(tmp_path / "unknown")
    assert unknown_report["status"] == "publication_date_unknown"
    assert unknown_report["growth_ready"] is False


def test_invalid_numeric_and_provider_error_fail_closed(tmp_path: Path) -> None:
    invalid_report = BaostockGrowthSource(
        make_config(), client=FakeClient("invalid_numeric.json")
    ).capture(tmp_path / "invalid")
    assert invalid_report["status"] == "invalid"

    provider_report = BaostockGrowthSource(
        make_config(), client=FakeClient("provider_error.json")
    ).capture(tmp_path / "provider")
    assert provider_report["status"] == "provider_error"


def test_growth_selection_respects_as_of() -> None:
    client = FakeClient("valid_reports.json")
    capture = BaostockGrowthSource(
        make_config(as_of=date(2025, 6, 1)), client=client
    )._query()
    computation = compute_growth_snapshot(list(capture.records), as_of=date(2025, 6, 1))

    assert computation.status is GrowthState.READY
    assert computation.snapshot is not None
    assert computation.snapshot.report_date == "2025-03-31"
