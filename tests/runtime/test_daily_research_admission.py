import json
from datetime import datetime
from pathlib import Path

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.runtime.daily_research_admission import (
    DAILY_RESEARCH_ADMISSION_VERSION,
    build_daily_research_admission,
)
from a_share_ai.runtime.daily_research_run_audit import audit_daily_research_run
from tests.runtime.test_daily_research_run_audit import _write_chain


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _prepare(
    root: Path,
    *,
    as_of: str = "2026-08-10T08:00:00+00:00",
    blocked: bool = False,
) -> dict[str, Path]:
    run_report = _write_chain(
        root, status="blocked" if blocked else "ready", failed_index=5 if blocked else None
    )
    run_payload = json.loads(run_report.read_text(encoding="utf-8"))
    run_payload["as_of"] = as_of
    run_payload["received_at"] = as_of
    _write_json(run_report, run_payload)
    run_audit_dir = root / "run-audit"
    audit_daily_research_run(
        run_report_path=run_report,
        artifact_root=root,
        output_dir=run_audit_dir,
    )
    calendar = root / "calendar.json"
    _write_json(
        calendar,
        {
            "schema_version": "1.0",
            "calendar_version": "calendar-fixture-v1",
            "market": "CN-A",
            "timezone": "Asia/Shanghai",
            "covered_start": "2026-08-01",
            "covered_end": "2026-08-10",
            "trading_dates": [
                "2026-08-03",
                "2026-08-04",
                "2026-08-05",
                "2026-08-06",
                "2026-08-07",
                "2026-08-10",
            ],
        },
    )
    calendar_report = root / "calendar_report.json"
    _write_json(
        calendar_report,
        {
            "status": "complete",
            "decision_ready": True,
            "calendar_version": "calendar-fixture-v1",
            "calendar_sha256": sha256_bytes(calendar.read_bytes()),
        },
    )
    return {
        "run_report": run_report,
        "run_audit_report": run_audit_dir / "daily_research_run_audit_report.json",
        "calendar": calendar,
        "calendar_report": calendar_report,
    }


def _build(inputs: dict[str, Path], evaluation_at: str, output_dir: Path) -> dict[str, object]:
    return build_daily_research_admission(
        run_report_path=inputs["run_report"],
        run_audit_report_path=inputs["run_audit_report"],
        calendar_path=inputs["calendar"],
        calendar_report_path=inputs["calendar_report"],
        evaluation_at=datetime.fromisoformat(evaluation_at),
        artifact_root=inputs["run_report"].parent,
        output_dir=output_dir,
    )


def test_fresh_admission_is_ready_and_self_hashed(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path / "fresh")

    report = _build(inputs, "2026-08-10T16:00:00+08:00", tmp_path / "fresh/admission")

    assert report["admission_version"] == DAILY_RESEARCH_ADMISSION_VERSION
    assert report["status"] == "ready"
    assert report["freshness_status"] == "fresh"
    assert report["expected_latest_trading_date"] == "2026-08-10"
    assert report["audit_ready"] is True
    assert report["admission_ready"] is True
    assert report["decision_ready"] is False
    assert report["output_sha256"] == sha256_bytes(
        (
            json.dumps(
                {**report, "output_sha256": None}, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode()
    )
    assert json.loads(
        (tmp_path / "fresh/admission/daily_research_admission.json").read_text(encoding="utf-8")
    ) == json.loads(
        (tmp_path / "fresh/admission/daily_research_admission_report.json").read_text(
            encoding="utf-8"
        )
    )


def test_before_close_uses_previous_completed_trading_date(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path / "before-close", as_of="2026-08-07T12:00:00+00:00")

    report = _build(inputs, "2026-08-10T13:00:00+08:00", tmp_path / "before-close/admission")

    assert report["status"] == "ready"
    assert report["expected_latest_trading_date"] == "2026-08-07"
    assert report["admission_ready"] is True


def test_weekend_uses_previous_trading_session(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path / "weekend", as_of="2026-08-07T12:00:00+00:00")

    report = _build(inputs, "2026-08-08T16:00:00+08:00", tmp_path / "weekend/admission")

    assert report["status"] == "ready"
    assert report["expected_latest_trading_date"] == "2026-08-07"
    assert report["admission_ready"] is True


def test_stale_admission_is_not_ready(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path / "stale", as_of="2026-08-07T12:00:00+00:00")

    report = _build(inputs, "2026-08-10T16:00:00+08:00", tmp_path / "stale/admission")

    assert report["status"] == "stale"
    assert report["freshness_status"] == "stale"
    assert report["admission_ready"] is False
    assert report["issues"][0]["code"] == "RESEARCH_STALE"


def test_weekend_outside_calendar_is_unknown(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path / "unknown")

    report = _build(inputs, "2026-08-15T16:00:00+08:00", tmp_path / "unknown/admission")

    assert report["status"] == "calendar_unknown"
    assert report["admission_ready"] is False
    assert report["issues"][0]["code"] == "CALENDAR_UNKNOWN"


def test_blocked_upstream_is_not_admitted(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path / "blocked", blocked=True)

    report = _build(inputs, "2026-08-10T16:00:00+08:00", tmp_path / "blocked/admission")

    assert report["status"] == "blocked"
    assert report["audit_ready"] is False
    assert report["admission_ready"] is False
    assert report["issues"][0]["code"] == "UPSTREAM_NOT_READY"


def test_future_run_and_calendar_hash_tamper_fail_closed(tmp_path: Path) -> None:
    future_inputs = _prepare(tmp_path / "future")
    future = _build(future_inputs, "2026-08-10T13:00:00+08:00", tmp_path / "future/admission")
    assert future["status"] == "invalid"
    assert future["issues"][0]["code"] == "RUN_AFTER_EVALUATION"

    tampered_inputs = _prepare(tmp_path / "tampered")
    tampered_inputs["calendar"].write_text(
        tampered_inputs["calendar"]
        .read_text(encoding="utf-8")
        .replace("calendar-fixture-v1", "tampered-calendar"),
        encoding="utf-8",
    )
    tampered = _build(tampered_inputs, "2026-08-10T16:00:00+08:00", tmp_path / "tampered/admission")
    assert tampered["status"] == "invalid"
    assert tampered["issues"][0]["code"] == "HASH_MISMATCH"


def test_calendar_path_outside_root_fails_closed(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path / "outside")
    inputs["calendar"] = tmp_path / "outside-calendar.json"

    report = _build(inputs, "2026-08-10T16:00:00+08:00", tmp_path / "outside/admission")

    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"


def test_cli_admission_returns_zero_one_and_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fresh = _prepare(tmp_path / "cli-fresh")
    ready_code = main(
        [
            "build-daily-research-admission",
            "--run-report",
            str(fresh["run_report"]),
            "--run-audit-report",
            str(fresh["run_audit_report"]),
            "--calendar",
            str(fresh["calendar"]),
            "--calendar-report",
            str(fresh["calendar_report"]),
            "--evaluation-at",
            "2026-08-10T16:00:00+08:00",
            "--artifact-root",
            str(tmp_path / "cli-fresh"),
            "--output-dir",
            str(tmp_path / "cli-fresh/admission"),
        ]
    )
    assert ready_code == 0
    assert json.loads(capsys.readouterr().out)["admission_ready"] is True

    stale = _prepare(tmp_path / "cli-stale", as_of="2026-08-07T12:00:00+00:00")
    stale_code = main(
        [
            "build-daily-research-admission",
            "--run-report",
            str(stale["run_report"]),
            "--run-audit-report",
            str(stale["run_audit_report"]),
            "--calendar",
            str(stale["calendar"]),
            "--calendar-report",
            str(stale["calendar_report"]),
            "--evaluation-at",
            "2026-08-10T16:00:00+08:00",
            "--artifact-root",
            str(tmp_path / "cli-stale"),
            "--output-dir",
            str(tmp_path / "cli-stale/admission"),
        ]
    )
    assert stale_code == 1
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        main(["build-daily-research-admission", "--run-report", str(tmp_path / "missing.json")])
    assert exc_info.value.code == 2
