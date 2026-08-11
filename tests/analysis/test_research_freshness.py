import json
from datetime import datetime
from pathlib import Path
from typing import Any

from a_share_ai.analysis.research_freshness import audit_research_freshness
from a_share_ai.analysis.research_release import build_research_release
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_research_release import _prepare_release
from tests.analysis.test_review_record import _write_json

CALENDAR = Path(__file__).parents[2] / "fixtures" / "market" / "calendar" / "sample.json"


def _prepare_freshness(tmp_path: Path, release_as_of: str) -> dict[str, Path]:
    inputs = _prepare_release(tmp_path)
    release_dir = inputs["artifact_root"] / "release"
    build_research_release(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        safety_report_path=inputs["safety_report"],
        review_packet_path=inputs["packet"],
        review_result_path=inputs["result"],
        review_result_report_path=inputs["result_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=release_dir,
    )
    inputs["manifest"] = release_dir / "research_release_manifest.json"
    inputs["release_report"] = release_dir / "research_release_report.json"
    manifest = json.loads(inputs["manifest"].read_text(encoding="utf-8"))
    manifest["as_of"] = release_as_of
    _write_json(inputs["manifest"], manifest)
    release_report = json.loads(inputs["release_report"].read_text(encoding="utf-8"))
    release_report["as_of"] = release_as_of
    release_report["output_sha256"] = sha256_bytes(inputs["manifest"].read_bytes())
    _write_json(inputs["release_report"], release_report)

    calendar = inputs["artifact_root"] / "calendar.json"
    calendar_payload = json.loads(CALENDAR.read_text(encoding="utf-8"))
    calendar_payload["covered_end"] = "2026-01-11"
    _write_json(calendar, calendar_payload)
    calendar_report = inputs["artifact_root"] / "calendar-report.json"
    _write_json(
        calendar_report,
        {
            "calendar_sha256": sha256_bytes(calendar.read_bytes()),
            "calendar_version": "cn-a-share-fixture-2026-01-v1",
            "decision_ready": True,
            "status": "complete",
        },
    )
    inputs["calendar"] = calendar
    inputs["calendar_report"] = calendar_report
    return inputs


def _run(inputs: dict[str, Path], output_name: str, evaluation_at: str) -> dict[str, Any]:
    return audit_research_freshness(
        release_manifest_path=inputs["manifest"],
        release_report_path=inputs["release_report"],
        calendar_path=inputs["calendar"],
        calendar_report_path=inputs["calendar_report"],
        evaluation_at=datetime.fromisoformat(evaluation_at),
        output_dir=inputs["artifact_root"] / output_name,
    )


def test_freshness_is_fresh_at_close_and_deterministic(tmp_path: Path) -> None:
    inputs = _prepare_freshness(tmp_path, "2026-01-06T07:00:00+00:00")
    evaluation = "2026-01-06T07:00:00+00:00"
    first = _run(inputs, "fresh-one", evaluation)
    second = _run(inputs, "fresh-two", evaluation)

    assert first["freshness_status"] == "fresh"
    assert first["freshness_ready"] is True
    assert first["expected_latest_trading_date"] == "2026-01-06"
    assert first["trading_day_lag"] == 0
    assert first["decision_ready"] is False
    first_raw = (
        inputs["artifact_root"] / "fresh-one" / "research_freshness_report.json"
    ).read_bytes()
    second_raw = (
        inputs["artifact_root"] / "fresh-two" / "research_freshness_report.json"
    ).read_bytes()
    assert sha256_bytes(first_raw) == sha256_bytes(second_raw)
    assert second["freshness_ready"] is True


def test_freshness_applies_close_cutoff_stale_and_weekend_rules(tmp_path: Path) -> None:
    inputs = _prepare_freshness(tmp_path, "2026-01-05T07:00:00+00:00")

    before_close = _run(inputs, "before-close", "2026-01-06T06:59:00+00:00")
    assert before_close["freshness_status"] == "fresh"
    assert before_close["expected_latest_trading_date"] == "2026-01-05"

    after_close = _run(inputs, "after-close", "2026-01-06T07:00:00+00:00")
    assert after_close["freshness_status"] == "stale"
    assert after_close["freshness_ready"] is False
    assert after_close["trading_day_lag"] == 1

    weekend = _run(inputs, "weekend", "2026-01-07T08:00:00+00:00")
    assert weekend["freshness_status"] == "stale"
    assert weekend["expected_latest_trading_date"] == "2026-01-06"


def test_freshness_fails_closed_for_unknown_future_and_hash(tmp_path: Path) -> None:
    inputs = _prepare_freshness(tmp_path, "2026-01-06T07:00:00+00:00")
    unknown = _run(inputs, "unknown", "2026-01-12T08:00:00+00:00")
    assert unknown["freshness_status"] == "calendar_unknown"
    assert unknown["freshness_ready"] is False

    future = _run(inputs, "future", "2026-01-05T07:00:00+00:00")
    assert future["freshness_status"] == "invalid"
    assert future["issues"][0]["code"] == "RELEASE_AFTER_EVALUATION"

    calendar_report = json.loads(inputs["calendar_report"].read_text(encoding="utf-8"))
    calendar_report["calendar_sha256"] = "0" * 64
    _write_json(inputs["calendar_report"], calendar_report)
    invalid = _run(inputs, "hash", "2026-01-06T07:00:00+00:00")
    assert invalid["freshness_status"] == "invalid"
    assert invalid["issues"][0]["code"] == "HASH_MISMATCH"


def test_freshness_cli_returns_success_only_when_fresh(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare_freshness(tmp_path, "2026-01-06T07:00:00+00:00")
    output_dir = inputs["artifact_root"] / "cli"
    exit_code = main(
        [
            "audit-research-freshness",
            "--release-manifest",
            str(inputs["manifest"]),
            "--release-report",
            str(inputs["release_report"]),
            "--calendar",
            str(inputs["calendar"]),
            "--calendar-report",
            str(inputs["calendar_report"]),
            "--evaluation-at",
            "2026-01-06T07:00:00+00:00",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["freshness_ready"] is True
    assert (output_dir / "research_freshness_report.json").exists()
