import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import a_share_ai.runtime.daily_research_run as runtime
from a_share_ai.runtime.daily_research_run import (
    DAILY_RESEARCH_RUN_VERSION,
    DailyResearchRunError,
    load_daily_research_spec,
    run_daily_research,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_spec(root: Path, **overrides: Any) -> Path:
    _write_json(
        root / "runtime_spec.json",
        {
            "run_version": DAILY_RESEARCH_RUN_VERSION,
            "symbol": "600000.SH",
            "start_date": "2026-08-07",
            "end_date": "2026-08-07",
            "as_of": "2026-08-10T12:00:00+00:00",
            "received_at": "2026-08-10T12:00:00+00:00",
            "calendar_path": "market/calendar.json",
            "fundamentals_start": {"year": 2025, "quarter": 1},
            "fundamentals_end": {"year": 2025, "quarter": 1},
            "announcement_start": "2026-08-01",
            "announcement_end": "2026-08-07",
            **overrides,
        },
    )
    _write_json(
        root / "market/calendar.json",
        {
            "schema_version": "1.0",
            "calendar_version": "fixture-v1",
            "market": "CN-A",
            "timezone": "Asia/Shanghai",
            "covered_start": "2026-08-07",
            "covered_end": "2026-08-07",
            "trading_dates": ["2026-08-07"],
        },
    )
    return root / "runtime_spec.json"


def _write_daily_artifacts(output_dir: Path) -> None:
    raw_bar = {
        "schema_version": "1.0",
        "symbol": "600000.SH",
        "trade_date": "2026-08-07",
        "open": "10.00",
        "high": "10.40",
        "low": "9.95",
        "close": "10.30",
        "volume": "1200000",
        "amount": "12360000.00",
        "source": "fake-provider",
        "market_time": "2026-08-07T15:00:00+08:00",
        "received_at": "2026-08-10T12:00:00+00:00",
        "data_status": "connected",
    }
    normalized = (json.dumps(raw_bar, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "request.json").write_text("{}\n", encoding="utf-8")
    (output_dir / "raw_response.json").write_text("{}\n", encoding="utf-8")
    (output_dir / "normalized_daily.jsonl").write_bytes(normalized)
    _write_json(
        output_dir / "capture_report.json",
        {"bar_count": 1, "error": None, "decision_ready": False},
    )


class _FakeDailySource:
    def __init__(self, config: Any) -> None:
        self.config = config

    def capture(self, output_dir: Path) -> dict[str, Any]:
        _write_daily_artifacts(output_dir)
        return {"bar_count": 1, "error": None, "decision_ready": False}


class _FakeMarketContextSource:
    def __init__(self, config: Any) -> None:
        self.config = config

    def capture(self, *, calendar_path: Path, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ("request.json", "raw_response.json"):
            (output_dir / name).write_text("{}\n", encoding="utf-8")
        _write_json(output_dir / "market_context_snapshot.json", {})
        report = {
            "market_context_ready": True,
            "decision_ready": False,
            "status": "ready",
        }
        _write_json(output_dir / "market_context_report.json", report)
        return report


class _FakeFundamentalSource:
    ready_field = "fundamental_ready"
    prefix = "profitability"

    def __init__(self, config: Any) -> None:
        self.config = config

    def capture(self, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ("request.json", "raw_response.json"):
            (output_dir / name).write_text("{}\n", encoding="utf-8")
        _write_json(output_dir / f"{self.prefix}_snapshot.json", {})
        report = {self.ready_field: True, "decision_ready": False, "status": "ready"}
        _write_json(output_dir / f"{self.prefix}_report.json", report)
        return report


class _FakeProfitabilitySource(_FakeFundamentalSource):
    ready_field = "fundamental_ready"
    prefix = "profitability"


class _FakeGrowthSource(_FakeFundamentalSource):
    ready_field = "growth_ready"
    prefix = "growth"


class _FakeAnnouncementsSource:
    def __init__(self, config: Any) -> None:
        self.config = config

    def capture(self, output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ("request.json", "raw_response.json"):
            (output_dir / name).write_text("{}\n", encoding="utf-8")
        _write_json(output_dir / "announcements_snapshot.json", {})
        report = {"announcement_ready": True, "decision_ready": False, "status": "empty"}
        _write_json(output_dir / "announcements_report.json", report)
        return report


class _FakeAnalysisInputSource:
    def __init__(self, config: Any) -> None:
        self.config = config

    def capture(self, output_dir: Path) -> dict[str, Any]:
        bundle = {
            "bundle_version": "analysis-input-v2",
            "decision_ready": False,
            "summaries": {
                "market": {
                    "market_context": {
                        "market_context_summary_version": "market-context-summary-v1"
                    }
                },
                "technical": {"relative_strength": {"version": "relative-strength-v1"}},
            },
        }
        _write_json(output_dir / "analysis_input_bundle.json", bundle)
        report = {"analysis_input_ready": True, "decision_ready": False, "status": "ready"}
        _write_json(output_dir / "analysis_input_report.json", report)
        return report


def _patch_success_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "BaostockDailySource", _FakeDailySource)
    monkeypatch.setattr(runtime, "BaostockMarketContextSource", _FakeMarketContextSource)
    monkeypatch.setattr(runtime, "BaostockProfitabilitySource", _FakeProfitabilitySource)
    monkeypatch.setattr(runtime, "BaostockGrowthSource", _FakeGrowthSource)
    monkeypatch.setattr(runtime, "CninfoAnnouncementSource", _FakeAnnouncementsSource)
    monkeypatch.setattr(runtime, "AnalysisInputSource", _FakeAnalysisInputSource)

    class _ReadyFeatureReport:
        def to_mapping(self) -> dict[str, Any]:
            return {"decision_ready": True, "status": "ready"}

    class _ReadyPriceReport:
        def to_mapping(self) -> dict[str, Any]:
            return {"price_plan_ready": True, "decision_ready": False, "status": "ready"}

    monkeypatch.setattr(
        runtime,
        "compute_feature_snapshots",
        lambda *args, **kwargs: SimpleNamespace(snapshots=(), status="ready"),
    )
    monkeypatch.setattr(runtime, "serialize_feature_snapshots", lambda snapshots: b"{}\n")
    monkeypatch.setattr(
        runtime, "build_feature_report", lambda *args, **kwargs: _ReadyFeatureReport()
    )
    monkeypatch.setattr(
        runtime,
        "compute_price_plan",
        lambda *args, **kwargs: SimpleNamespace(plan={}),
    )
    monkeypatch.setattr(runtime, "serialize_price_plan", lambda plan: b"{}\n")
    monkeypatch.setattr(
        runtime, "build_price_plan_report", lambda *args, **kwargs: _ReadyPriceReport()
    )


def test_spec_is_strict_and_path_bounded(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    spec = load_daily_research_spec(spec_path=spec_path, input_root=tmp_path)
    assert spec.symbol == "600000.SH"
    assert spec.calendar_path == (tmp_path / "market/calendar.json").resolve()

    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    spec_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DailyResearchRunError, match="fields are invalid"):
        load_daily_research_spec(spec_path=spec_path, input_root=tmp_path)


def test_provider_failure_stops_later_stages_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _write_spec(tmp_path)

    class _FailingDailySource:
        def __init__(self, config: Any) -> None:
            self.config = config

        def capture(self, output_dir: Path) -> dict[str, Any]:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(runtime, "BaostockDailySource", _FailingDailySource)
    report = run_daily_research(
        spec_path=spec_path,
        input_root=tmp_path,
        output_dir=tmp_path / "run",
        source_mode="public-read-only",
    )

    assert report["status"] == "blocked"
    assert report["decision_ready"] is False
    assert [stage["status"] for stage in report["stages"]] == [
        "failed",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert (tmp_path / "run/daily_research_run_report.json").is_file()
    assert "provider unavailable" in report["issues"][0]["message"]


def test_successful_run_is_single_pass_and_keeps_read_only_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_success_dependencies(monkeypatch)
    spec_path = _write_spec(tmp_path)
    report = run_daily_research(
        spec_path=spec_path,
        input_root=tmp_path,
        output_dir=tmp_path / "run",
        source_mode="public-read-only",
    )

    assert report["status"] == "ready", json.dumps(report, ensure_ascii=False)
    assert report["analysis_input_ready"] is True
    assert report["decision_ready"] is False
    assert report["market_context_summary_version"] == "market-context-summary-v1"
    assert report["relative_strength_version"] == "relative-strength-v1"
    assert all(stage["status"] == "ready" for stage in report["stages"])
    assert report["analysis_input_path"] == "run/analysis-input/analysis_input_bundle.json"
    assert report["analysis_input_sha256"]


def test_cli_invalid_spec_returns_parameter_error(tmp_path: Path) -> None:
    _write_spec(tmp_path, run_version="wrong")
    from a_share_ai.cli import main

    assert (
        main(
            [
                "run-daily-research",
                "--spec",
                str(tmp_path / "runtime_spec.json"),
                "--input-root",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "run"),
            ]
        )
        == 2
    )
