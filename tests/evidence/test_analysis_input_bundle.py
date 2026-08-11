import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from a_share_ai.analysis.contracts import AnalysisReportConfig
from a_share_ai.analysis.validator import AnalysisReportSource
from a_share_ai.cli import main
from a_share_ai.evidence.analysis_input_bundle import AnalysisInputConfig, AnalysisInputSource
from a_share_ai.market.replay import sha256_bytes

# The fixture builder keeps report fields compact to mirror the JSON contracts.
# ruff: noqa: E501


AS_OF = datetime(2026, 8, 10, 12, tzinfo=UTC)


def write_json(path: Path, value: dict) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def write_jsonl(path: Path, values: list[dict]) -> bytes:
    raw = ("\n".join(json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def build_artifacts(root: Path, *, mixed_as_of: bool = False, future_feature: bool = False) -> AnalysisInputConfig:
    symbol = "600000.SH"
    as_of_string = "2026-08-09T12:00:00+00:00" if mixed_as_of else "2026-08-10T12:00:00+00:00"
    bars_path = root / "market" / "normalized_daily.jsonl"
    bars = [
        {
            "schema_version": "1.0",
            "symbol": symbol,
            "trade_date": "2026-08-07",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume": "100",
            "amount": "1000",
            "source": "fixture",
            "market_time": "2026-08-07T15:00:00+08:00",
            "received_at": "2026-08-10T12:00:00+00:00",
            "data_status": "connected",
        }
    ]
    bars_raw = write_jsonl(bars_path, bars)
    calendar_path = root / "market" / "calendar.json"
    calendar_raw = write_json(calendar_path, {"schema_version": "calendar-v1", "dates": ["2026-08-07"]})
    health_path = root / "market" / "health_report.json"
    write_json(
        health_path,
        {
            "schema_version": "1.0", "source": "jsonl-replay", "as_of": as_of_string,
            "symbols": [symbol], "bar_count": 1, "final_state": "connected", "decision_ready": True,
            "first_trade_date": "2026-08-07", "last_trade_date": "2026-08-07",
            "latest_received_at": "2026-08-10T12:00:00+00:00",
            "input_sha256": sha256_bytes(bars_raw), "output_sha256": sha256_bytes(bars_raw),
        },
    )
    coverage_path = root / "market" / "coverage_report.json"
    write_json(
        coverage_path,
        {
            "schema_version": "1.0", "calendar_source": "json-calendar", "as_of": as_of_string,
            "start": "2026-08-07", "end": "2026-08-07", "status": "complete",
            "decision_ready": True, "bars_sha256": sha256_bytes(bars_raw),
            "calendar_sha256": sha256_bytes(calendar_raw),
        },
    )
    technical_input = root / "analysis" / "technical_input.jsonl"
    technical_input_raw = write_jsonl(technical_input, bars)
    technical_features = root / "analysis" / "technical_features.jsonl"
    feature_date = "2026-08-11" if future_feature else "2026-08-07"
    feature_raw = write_jsonl(
        technical_features,
        [{"symbol": symbol, "trade_date": feature_date, "close": "10.5", "sma_20": "10"}],
    )
    technical_report = root / "analysis" / "technical_report.json"
    technical_report_raw = write_json(
        technical_report,
        {
            "schema_version": "1.0", "source": "technical-input", "symbol": symbol,
            "as_of": as_of_string, "status": "ready", "decision_ready": True,
            "indicator_version": "technical-v1", "warmup_state": "ready", "sample_count": 1,
            "last_trade_date": feature_date, "input_sha256": sha256_bytes(technical_input_raw),
            "output_sha256": sha256_bytes(feature_raw),
        },
    )
    price_plan_input = root / "analysis" / "price_plan_input.jsonl"
    price_plan_input_raw = write_jsonl(price_plan_input, [{"symbol": symbol, "trade_date": "2026-08-07"}])
    price_plan = root / "analysis" / "technical_price_plan.json"
    price_plan_raw = write_json(
        price_plan,
        {"schema_version": "1.0", "symbol": symbol, "as_of": as_of_string, "trade_date": "2026-08-07"},
    )
    price_plan_report = root / "analysis" / "price_plan_report.json"
    write_json(
        price_plan_report,
        {
            "schema_version": "1.0", "source": "technical-price-plan", "symbol": symbol,
            "as_of": as_of_string, "status": "ready", "price_plan_ready": True,
            "decision_ready": False, "price_plan_version": "price-plan-v1", "indicator_version": "technical-v1",
            "input_sha256": sha256_bytes(price_plan_input_raw), "output_sha256": sha256_bytes(price_plan_raw),
            "technical_report_sha256": sha256_bytes(technical_report_raw), "trade_date": "2026-08-07",
        },
    )
    for kind, ready_field in (("profitability", "fundamental_ready"), ("growth", "growth_ready")):
        directory = root / "fundamentals" / kind
        snapshot_path = directory / f"{kind}_snapshot.json"
        snapshot_raw = write_json(
            snapshot_path,
            {
                "schema_version": "1.0", "source": f"baostock-{kind}", "symbol": symbol,
                "as_of": "2026-08-10", "published_date": "2026-03-31", "report_date": "2025-12-31",
                ready_field: True, "decision_ready": False,
            },
        )
        raw_path = directory / "raw_response.json"
        raw_response = write_json(raw_path, {"fields": [], "rows": []})
        write_json(
            directory / f"{kind}_report.json",
            {
                "schema_version": "1.0", "source": f"baostock-{kind}", "symbol": symbol,
                "as_of": "2026-08-10", "status": "ready", ready_field: True,
                "decision_ready": False, "selected_published_date": "2026-03-31",
                "selected_report_date": "2025-12-31", "snapshot_sha256": sha256_bytes(snapshot_raw),
                "raw_response_sha256": sha256_bytes(raw_response),
            },
        )
    events = root / "events"
    announcement_snapshot = events / "announcements_snapshot.json"
    announcement_snapshot_raw = write_json(
        announcement_snapshot,
        {
            "schema_version": "1.0", "source": "cninfo-announcements", "symbol": symbol,
            "as_of": "2026-08-10", "records": [
                {"symbol": symbol, "announcement_id": "1", "published_date": "2026-08-07"}
            ], "announcement_ready": True, "decision_ready": False,
        },
    )
    request_raw = write_json(events / "request.json", {"symbol": symbol, "as_of": "2026-08-10"})
    raw_response = write_json(events / "raw_response.json", {"announcements": []})
    write_json(
        events / "announcements_report.json",
        {
            "schema_version": "1.0", "source": "cninfo-announcements", "symbol": symbol,
            "as_of": "2026-08-10", "status": "ready", "record_count": 1,
            "announcement_ready": True, "decision_ready": False,
            "request_sha256": sha256_bytes(request_raw), "raw_response_sha256": sha256_bytes(raw_response),
            "snapshot_sha256": sha256_bytes(announcement_snapshot_raw),
        },
    )
    return AnalysisInputConfig(
        symbol=symbol, as_of=AS_OF, input_root=root, market_bars=bars_path,
        market_health_report=health_path, coverage_report=coverage_path, calendar=calendar_path,
        technical_input=technical_input, technical_features=technical_features, technical_report=technical_report,
        price_plan_input=price_plan_input, price_plan=price_plan, price_plan_report=price_plan_report,
        profitability_snapshot=root / "fundamentals" / "profitability" / "profitability_snapshot.json",
        profitability_report=root / "fundamentals" / "profitability" / "profitability_report.json",
        growth_snapshot=root / "fundamentals" / "growth" / "growth_snapshot.json",
        growth_report=root / "fundamentals" / "growth" / "growth_report.json",
        announcements_snapshot=announcement_snapshot, announcements_report=events / "announcements_report.json",
    )


def add_market_context(config: AnalysisInputConfig) -> AnalysisInputConfig:
    calendar_raw = write_json(
        config.calendar,
        {
            "schema_version": "1.0",
            "calendar_version": "fixture-calendar-v1",
            "market": "CN",
            "timezone": "Asia/Shanghai",
            "covered_start": "2026-08-07",
            "covered_end": "2026-08-07",
            "trading_dates": ["2026-08-07"],
        },
    )
    coverage = json.loads(config.coverage_report.read_text(encoding="utf-8"))
    coverage["calendar_sha256"] = sha256_bytes(calendar_raw)
    write_json(config.coverage_report, coverage)
    context_dir = config.input_root / "market_context"
    snapshot_path = context_dir / "market_context_snapshot.json"
    report_path = context_dir / "market_context_report.json"
    as_of = config.as_of.isoformat()
    records = [
        {
            "symbol": symbol,
            "instrument_type": "index",
            "trade_date": "2026-08-07",
            "open": "100",
            "high": "105",
            "low": "99",
            "close": "103",
            "volume": "1000",
            "amount": "103000",
            "source": "baostock",
            "market_time": "2026-08-07T15:00:00+08:00",
            "received_at": as_of,
            "data_status": "connected",
        }
        for symbol in ("000001.SH", "399001.SZ", "399006.SZ")
    ]
    request = {
        "as_of": as_of,
        "start": "2026-08-07",
        "end": "2026-08-07",
        "indexes": [{"symbol": symbol} for symbol in ("000001.SH", "399001.SZ", "399006.SZ")],
    }
    snapshot_raw = write_json(
        snapshot_path,
        {
            "as_of": as_of,
            "calendar_version": "fixture-calendar-v1",
            "indexes": records,
            "market_context_version": "market-context-v1",
            "received_at": as_of,
            "request": request,
        },
    )
    write_json(
        report_path,
        {
            "as_of": as_of,
            "calendar_sha256": sha256_bytes(calendar_raw),
            "calendar_version": "fixture-calendar-v1",
            "decision_ready": False,
            "end": "2026-08-07",
            "index_reports": {
                symbol: {
                    "record_count": 1,
                    "start": "2026-08-07",
                    "end": "2026-08-07",
                    "missing_trading_dates": [],
                    "status": "ready",
                }
                for symbol in ("000001.SH", "399001.SZ", "399006.SZ")
            },
            "index_symbols": ["000001.SH", "399001.SZ", "399006.SZ"],
            "market_context_ready": True,
            "market_context_version": "market-context-v1",
            "raw_response_sha256": "0" * 64,
            "received_at": as_of,
            "request": request,
            "snapshot_sha256": sha256_bytes(snapshot_raw),
            "start": "2026-08-07",
            "status": "ready",
            "issues": [],
        },
    )
    return replace(
        config,
        market_context_snapshot=snapshot_path,
        market_context_report=report_path,
    )


def test_valid_bundle_references_all_inputs_and_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "input"
    config = build_artifacts(root)
    first = AnalysisInputSource(config).capture(tmp_path / "out-1")
    second = AnalysisInputSource(config).capture(tmp_path / "out-2")

    assert first["status"] == "ready"
    assert first["analysis_input_ready"] is True
    assert first["decision_ready"] is False
    assert first["bundle_sha256"] == second["bundle_sha256"]
    bundle = json.loads((tmp_path / "out-1" / "analysis_input_bundle.json").read_text(encoding="utf-8"))
    assert len(bundle["evidence"]) == 6
    assert all("artifact_paths" in entry for entry in bundle["evidence"])
    assert bundle["bundle_version"] == "analysis-input-v1"


def test_v2_bundle_adds_market_context_to_existing_market_evidence(tmp_path: Path) -> None:
    config = add_market_context(build_artifacts(tmp_path / "input"))
    report = AnalysisInputSource(config).capture(tmp_path / "out")

    assert report["status"] == "ready"
    assert report["bundle_version"] == "analysis-input-v2"
    bundle = json.loads((tmp_path / "out" / "analysis_input_bundle.json").read_text(encoding="utf-8"))
    market = next(entry for entry in bundle["evidence"] if entry["name"] == "market")
    assert bundle["summaries"]["market"]["market_context"]["version"] == "market-context-v1"
    assert len(market["artifact_paths"]) == 5


def test_v2_context_hash_tampering_fails_closed(tmp_path: Path) -> None:
    config = add_market_context(build_artifacts(tmp_path / "input"))
    context_report = json.loads(config.market_context_report.read_text(encoding="utf-8"))
    context_report["snapshot_sha256"] = "0" * 64
    write_json(config.market_context_report, context_report)
    report = AnalysisInputSource(config).capture(tmp_path / "out")

    assert report["status"] == "invalid"
    assert "SHA-256" in report["issues"][0]["message"]


def test_v2_bundle_is_consumable_by_offline_analysis_validator(tmp_path: Path) -> None:
    config = add_market_context(build_artifacts(tmp_path / "input"))
    bundle_report = AnalysisInputSource(config).capture(tmp_path / "bundle")
    assert bundle_report["status"] == "ready"
    analysis_report = AnalysisReportSource(
        AnalysisReportConfig(
            bundle_path=tmp_path / "bundle" / "analysis_input_bundle.json",
            input_root=config.input_root,
            response_fixture=Path("fixtures/analysis/report/valid_provider.json"),
            output_dir=tmp_path / "analysis",
        )
    ).capture()

    assert analysis_report["status"] == "ready"
    assert analysis_report["analysis_ready"] is True


def test_cli_builds_v2_bundle_with_paired_context_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = add_market_context(build_artifacts(tmp_path / "input"))
    flags = {
        "market_bars": "--market-bars",
        "market_health_report": "--market-health-report",
        "coverage_report": "--coverage-report",
        "calendar": "--calendar",
        "technical_input": "--technical-input",
        "technical_features": "--technical-features",
        "technical_report": "--technical-report",
        "price_plan_input": "--price-plan-input",
        "price_plan": "--price-plan",
        "price_plan_report": "--price-plan-report",
        "profitability_snapshot": "--profitability-snapshot",
        "profitability_report": "--profitability-report",
        "growth_snapshot": "--growth-snapshot",
        "growth_report": "--growth-report",
        "announcements_snapshot": "--announcements-snapshot",
        "announcements_report": "--announcements-report",
        "market_context_snapshot": "--market-context-snapshot",
        "market_context_report": "--market-context-report",
    }
    argv = [
        "build-analysis-input",
        "--symbol",
        config.symbol,
        "--as-of",
        config.as_of.isoformat(),
        "--input-root",
        str(config.input_root),
    ]
    for field, flag in flags.items():
        argv.extend((flag, str(getattr(config, field))))
    argv.extend(("--output-dir", str(tmp_path / "cli-out")))

    assert main(argv) == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["bundle_version"] == "analysis-input-v2"


def test_context_paths_must_be_provided_as_a_pair(tmp_path: Path) -> None:
    config = build_artifacts(tmp_path / "input")
    with pytest.raises(RuntimeError, match="provided together"):
        replace(config, market_context_snapshot=config.calendar)


def test_mixed_as_of_fails_closed(tmp_path: Path) -> None:
    config = build_artifacts(tmp_path / "input", mixed_as_of=True)
    report = AnalysisInputSource(config).capture(tmp_path / "out")

    assert report["status"] == "invalid"
    assert report["analysis_input_ready"] is False
    assert "cutoff" in report["issues"][0]["message"]


def test_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "input"
    config = build_artifacts(root)
    config.technical_features.write_text('{"symbol":"600000.SH","trade_date":"2026-08-07"}\n', encoding="utf-8")
    report = AnalysisInputSource(config).capture(tmp_path / "out")

    assert report["status"] == "invalid"
    assert "SHA-256" in report["issues"][0]["message"]


def test_future_feature_fails_closed(tmp_path: Path) -> None:
    config = build_artifacts(tmp_path / "input", future_feature=True)
    report = AnalysisInputSource(config).capture(tmp_path / "out")

    assert report["analysis_input_ready"] is False
    assert "future" in report["issues"][0]["message"]


def test_missing_artifact_and_path_escape_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "input"
    config = build_artifacts(root)
    config.calendar.unlink()
    missing_report = AnalysisInputSource(config).capture(tmp_path / "missing")
    assert missing_report["status"] == "invalid"
    assert "missing" in missing_report["issues"][0]["message"]

    escaped = build_artifacts(tmp_path / "input-escape")
    escaped_values = {
        field: getattr(escaped, field) for field in escaped.__dataclass_fields__
    }
    escaped_values["calendar"] = tmp_path / "outside.json"
    escaped = AnalysisInputConfig(**escaped_values)
    escaped_report = AnalysisInputSource(escaped).capture(tmp_path / "escape")
    assert escaped_report["analysis_input_ready"] is False
