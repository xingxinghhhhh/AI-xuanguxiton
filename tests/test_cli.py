import json
from pathlib import Path

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes

FIXTURE = Path(__file__).parent.parent / "fixtures" / "market" / "valid_daily.jsonl"
COVERAGE_ROOT = Path(__file__).parent.parent / "fixtures" / "market"
COVERAGE_BARS = COVERAGE_ROOT / "coverage" / "valid_daily.jsonl"
COVERAGE_CALENDAR = COVERAGE_ROOT / "calendar" / "sample.json"
MISSING_BARS = COVERAGE_ROOT / "coverage" / "missing_trading_day.jsonl"
UNEXPECTED_BARS = COVERAGE_ROOT / "coverage" / "unexpected_non_trading_day.jsonl"
NARROW_CALENDAR = COVERAGE_ROOT / "calendar" / "sample_narrow.json"
TECHNICAL_ROOT = Path(__file__).parent.parent / "fixtures" / "analysis" / "technical"
PRICE_PLAN_ROOT = (
    Path(__file__).parent.parent / "fixtures" / "decision" / "technical_price_plan"
)


def test_cli_generates_hashes_and_returns_success(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "report"
    exit_code = main(
        [
            "replay-health",
            "--input",
            str(FIXTURE),
            "--as-of",
            "2026-08-10T12:00:00Z",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    report = json.loads((output_dir / "health_report.json").read_text(encoding="utf-8"))
    assert report["decision_ready"] is True
    assert len(report["input_sha256"]) == 64
    assert len(report["output_sha256"]) == 64
    assert report["as_of"] == "2026-08-10T12:00:00+00:00"
    assert "decision_ready" in capsys.readouterr().out


def test_cli_returns_failure_but_writes_report_for_stale_data(tmp_path: Path) -> None:
    input_path = tmp_path / "stale.jsonl"
    input_path.write_text(
        FIXTURE.read_text(encoding="utf-8").replace(
            "2026-08-10T15:05:00+08:00", "2026-08-01T15:05:00+08:00"
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "report"

    exit_code = main(
        [
            "replay-health",
            "--input",
            str(input_path),
            "--as-of",
            "2026-08-10T12:00:00Z",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "health_report.json").read_text(encoding="utf-8"))
    assert report["decision_ready"] is False
    assert any(issue["code"] == "STALE_DATA" for issue in report["issues"])


def run_coverage_cli(
    bars: Path, calendar: Path, output_dir: Path, *, end: str = "2026-01-06"
) -> int:
    return main(
        [
            "audit-coverage",
            "--bars",
            str(bars),
            "--calendar",
            str(calendar),
            "--start",
            "2026-01-02",
            "--end",
            end,
            "--as-of",
            "2026-01-07T00:00:00Z",
            "--output-dir",
            str(output_dir),
        ]
    )


def read_coverage_report(output_dir: Path) -> dict:
    return json.loads((output_dir / "coverage_report.json").read_text(encoding="utf-8"))


def test_cli_audit_coverage_complete(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "complete"

    assert run_coverage_cli(COVERAGE_BARS, COVERAGE_CALENDAR, output_dir) == 0
    report = read_coverage_report(output_dir)

    assert report["status"] == "complete"
    assert report["decision_ready"] is True
    assert len(report["bars_sha256"]) == 64
    assert len(report["calendar_sha256"]) == 64
    capsys.readouterr()


def test_cli_audit_coverage_missing_day(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "missing"

    assert run_coverage_cli(MISSING_BARS, COVERAGE_CALENDAR, output_dir) == 1
    report = read_coverage_report(output_dir)

    assert report["status"] == "missing_trading_day"
    assert report["decision_ready"] is False
    capsys.readouterr()


def test_cli_audit_coverage_unexpected_non_trading_day(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "unexpected"

    assert run_coverage_cli(UNEXPECTED_BARS, COVERAGE_CALENDAR, output_dir, end="2026-01-05") == 1
    report = read_coverage_report(output_dir)

    assert report["status"] == "unexpected_non_trading_day"
    assert report["unexpected_non_trading_dates"] == ["2026-01-03"]
    capsys.readouterr()


def test_cli_audit_coverage_unknown_calendar(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "unknown"

    assert run_coverage_cli(COVERAGE_BARS, NARROW_CALENDAR, output_dir) == 1
    report = read_coverage_report(output_dir)

    assert report["status"] == "calendar_unknown"
    assert report["decision_ready"] is False
    capsys.readouterr()


def test_cli_baostock_capture_rejects_unsupported_symbol_without_network(
    tmp_path: Path, capsys
) -> None:
    output_dir = tmp_path / "baostock"
    exit_code = main(
        [
            "capture-baostock-daily",
            "--symbol",
            "600000",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-31",
            "--received-at",
            "2026-02-01T00:00:00Z",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "capture_report.json").read_text(encoding="utf-8"))
    assert report["decision_ready"] is False
    assert "six digits" in report["error"]
    capsys.readouterr()


def test_cli_announcements_rejects_unsupported_symbol_without_network(
    tmp_path: Path, capsys
) -> None:
    output_dir = tmp_path / "announcements"
    exit_code = main(
        [
            "capture-announcements",
            "--symbol",
            "600000",
            "--start",
            "2025-12-29",
            "--end",
            "2025-12-31",
            "--as-of",
            "2025-12-31",
            "--received-at",
            "2026-08-10T12:00:00Z",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "announcements_report.json").read_text(encoding="utf-8"))
    assert report["decision_ready"] is False
    assert "six digits" in report["issues"][0]["message"]
    capsys.readouterr()


def test_cli_analysis_input_rejects_unsupported_symbol_without_network(
    tmp_path: Path, capsys
) -> None:
    output_dir = tmp_path / "analysis-input"
    exit_code = main(
        [
            "build-analysis-input",
            "--symbol",
            "600000",
            "--as-of",
            "2026-08-10T12:00:00Z",
            "--input-root",
            str(tmp_path),
            "--market-bars",
            "bars.jsonl",
            "--market-health-report",
            "health.json",
            "--coverage-report",
            "coverage.json",
            "--calendar",
            "calendar.json",
            "--technical-input",
            "technical-input.jsonl",
            "--technical-features",
            "technical-features.jsonl",
            "--technical-report",
            "technical.json",
            "--price-plan-input",
            "price-input.jsonl",
            "--price-plan",
            "price.json",
            "--price-plan-report",
            "price-report.json",
            "--profitability-snapshot",
            "profitability.json",
            "--profitability-report",
            "profitability-report.json",
            "--growth-snapshot",
            "growth.json",
            "--growth-report",
            "growth-report.json",
            "--announcements-snapshot",
            "announcements.json",
            "--announcements-report",
            "announcements-report.json",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "analysis_input_report.json").read_text(encoding="utf-8"))
    assert report["analysis_input_ready"] is False
    assert "six digits" in report["issues"][0]["message"]
    capsys.readouterr()


def test_cli_computes_ready_technical_features(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "technical"
    exit_code = main(
        [
            "compute-technical-features",
            "--input",
            str(TECHNICAL_ROOT / "valid_80_bars.jsonl"),
            "--as-of",
            "2026-04-01T00:00:00Z",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    report = json.loads((output_dir / "technical_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "ready"
    assert report["decision_ready"] is True
    assert report["sample_count"] == 80
    assert (
        len((output_dir / "technical_features.jsonl").read_text(encoding="utf-8").splitlines())
        == 80
    )
    capsys.readouterr()


def test_cli_keeps_insufficient_history_fail_closed(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "technical-short"
    exit_code = main(
        [
            "compute-technical-features",
            "--input",
            str(TECHNICAL_ROOT / "insufficient_history.jsonl"),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "technical_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "insufficient_history"
    assert report["decision_ready"] is False
    capsys.readouterr()


def test_cli_health_gate_rejects_invalid_technical_input(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "technical-invalid"
    exit_code = main(
        [
            "compute-technical-features",
            "--input",
            str(TECHNICAL_ROOT / "invalid_input.jsonl"),
            "--as-of",
            "2026-04-01T00:00:00Z",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "technical_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "invalid"
    assert report["decision_ready"] is False
    assert any(issue["code"] == "OHLC_HIGH_INVALID" for issue in report["issues"])
    capsys.readouterr()


def write_technical_report(input_path: Path, *, status: str = "ready") -> Path:
    input_bytes = input_path.read_bytes()
    report_path = input_path.with_name("technical_report.json")
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "indicator_version": "technical-v1",
                "input_sha256": "source",
                "output_sha256": sha256_bytes(input_bytes),
                "as_of": "2026-04-01T00:00:00+00:00",
                "symbol": "600000.SH",
                "sample_count": len(input_bytes.splitlines()),
                "last_trade_date": "2026-03-21",
                "warmup_state": status,
                "status": status,
                "decision_ready": status == "ready",
                "issues": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return report_path


def test_cli_computes_ready_price_plan(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "valid_features.jsonl"
    input_path.write_bytes((PRICE_PLAN_ROOT / "valid_features.jsonl").read_bytes())
    report_path = write_technical_report(input_path)
    output_dir = tmp_path / "price-plan"

    exit_code = main(
        [
            "compute-price-plan",
            "--input",
            str(input_path),
            "--technical-report",
            str(report_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    report = json.loads((output_dir / "price_plan_report.json").read_text(encoding="utf-8"))
    plan = json.loads((output_dir / "technical_price_plan.json").read_text(encoding="utf-8"))
    assert report["status"] == "ready"
    assert report["price_plan_ready"] is True
    assert report["decision_ready"] is False
    assert plan["take_profit_2"] == "12.00000000"
    capsys.readouterr()


def test_cli_rejects_no_valid_price_zone(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "no_valid_zone.jsonl"
    input_path.write_bytes((PRICE_PLAN_ROOT / "no_valid_zone.jsonl").read_bytes())
    report_path = write_technical_report(input_path)
    output_dir = tmp_path / "no-plan"

    assert (
        main(
            [
                "compute-price-plan",
                "--input",
                str(input_path),
                "--technical-report",
                str(report_path),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 1
    )
    report = json.loads((output_dir / "price_plan_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "no_valid_price_plan"
    assert report["price_plan_ready"] is False
    capsys.readouterr()


def test_cli_rejects_insufficient_technical_features(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "insufficient.jsonl"
    input_path.write_bytes((TECHNICAL_ROOT / "insufficient_history.jsonl").read_bytes())
    report_path = write_technical_report(input_path, status="insufficient_history")
    output_dir = tmp_path / "insufficient-plan"

    assert (
        main(
            [
                "compute-price-plan",
                "--input",
                str(input_path),
                "--technical-report",
                str(report_path),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 1
    )
    report = json.loads((output_dir / "price_plan_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "no_valid_price_plan"
    assert any(issue["code"] == "INPUT_NOT_READY" for issue in report["issues"])
    capsys.readouterr()


def test_cli_profitability_rejects_unsupported_symbol_without_network(
    tmp_path: Path, capsys
) -> None:
    output_dir = tmp_path / "profitability"
    exit_code = main(
        [
            "capture-profitability",
            "--symbol",
            "600000",
            "--as-of",
            "2026-08-10",
            "--start-year",
            "2025",
            "--start-quarter",
            "1",
            "--end-year",
            "2025",
            "--end-quarter",
            "4",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "profitability_report.json").read_text(encoding="utf-8"))
    assert report["fundamental_ready"] is False
    assert report["issues"][0]["code"] == "INVALID_REQUEST"
    capsys.readouterr()


def test_cli_growth_rejects_unsupported_symbol_without_network(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "growth"
    exit_code = main(
        [
            "capture-growth",
            "--symbol",
            "600000",
            "--as-of",
            "2026-08-10",
            "--start-year",
            "2025",
            "--start-quarter",
            "1",
            "--end-year",
            "2025",
            "--end-quarter",
            "4",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 1
    report = json.loads((output_dir / "growth_report.json").read_text(encoding="utf-8"))
    assert report["growth_ready"] is False
    assert report["issues"][0]["code"] == "INVALID_REQUEST"
    capsys.readouterr()
