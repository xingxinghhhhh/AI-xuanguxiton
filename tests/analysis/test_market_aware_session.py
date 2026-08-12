import json
from datetime import datetime
from pathlib import Path
from typing import Any

from a_share_ai.analysis.market_aware_release_replay import replay_market_aware_release
from a_share_ai.analysis.market_aware_session import build_market_aware_session
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_release_replay import (
    _build_pending_review,
    _write_submission,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_session(
    tmp_path: Path, *, calendar_dates: list[str] | None = None
) -> dict[str, Path]:
    packet_path, packet_report_path, artifact_root, replay_dir = _build_pending_review(tmp_path)
    submission_path = _write_submission(packet_path)
    release_replay_dir = artifact_root / "release-replay"
    replay_report = replay_market_aware_release(
        packet_path=packet_path,
        packet_report_path=packet_report_path,
        submission_path=submission_path,
        artifact_root=artifact_root,
        output_dir=release_replay_dir,
    )
    assert replay_report["research_release_ready"] is True

    dates = calendar_dates or ["2026-08-06", "2026-08-07", "2026-08-10"]
    calendar_path = artifact_root / "session-calendar.json"
    calendar_payload = {
        "calendar_version": "session-fixture-v1",
        "covered_end": dates[-1],
        "covered_start": dates[0],
        "dates": dates,
        "market": "CN-A",
        "schema_version": "1.0",
        "timezone": "Asia/Shanghai",
        "trading_dates": dates,
    }
    _write_json(calendar_path, calendar_payload)
    calendar_report_path = artifact_root / "session-calendar-report.json"
    _write_json(
        calendar_report_path,
        {
            "calendar_sha256": sha256_bytes(calendar_path.read_bytes()),
            "calendar_version": "session-fixture-v1",
            "decision_ready": True,
            "status": "complete",
        },
    )
    return {
        "artifact_root": artifact_root,
        "calendar": calendar_path,
        "calendar_report": calendar_report_path,
        "manifest": release_replay_dir / "release" / "research_release_manifest.json",
        "release_report": release_replay_dir / "release" / "research_release_report.json",
        "replay_report": release_replay_dir / "market_aware_release_replay_report.json",
        "replay_dir": replay_dir,
    }


def _run_session(
    inputs: dict[str, Path],
    output_name: str,
    evaluation_at: str,
    reference_at: str = "2026-08-12T12:00:00+00:00",
) -> dict[str, Any]:
    return build_market_aware_session(
        release_manifest_path=inputs["manifest"],
        release_report_path=inputs["release_report"],
        replay_report_path=inputs["replay_report"],
        calendar_path=inputs["calendar"],
        calendar_report_path=inputs["calendar_report"],
        evaluation_at=datetime.fromisoformat(evaluation_at),
        reference_at=datetime.fromisoformat(reference_at),
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["artifact_root"] / output_name,
    )


def test_market_aware_session_is_ready_and_deterministic(tmp_path: Path) -> None:
    inputs = _prepare_session(tmp_path)
    evaluation = "2026-08-10T13:00:00+00:00"

    first = _run_session(inputs, "session-one", evaluation)
    second = _run_session(inputs, "session-two", evaluation)

    assert first["status"] == "ready"
    assert first["session_ready"] is True
    assert first["freshness_status"] == "fresh"
    assert first["research_release_ready"] is True
    assert first["review_complete"] is True
    assert first["review_gate_pass"] is True
    assert first["decision_ready"] is False
    assert first["market_context_summary_version"] == "market-context-summary-v1"
    assert first["relative_strength_version"] == "relative-strength-v1"
    assert first["reference_at"] == "2026-08-12T12:00:00+00:00"
    assert first["time_policy"] == "explicit-reference-v1"
    assert first["output_sha256"] == second["output_sha256"]
    assert sha256_bytes(
        (inputs["artifact_root"] / "session-one" / "market_aware_session.json").read_bytes()
    ) == sha256_bytes(
        (inputs["artifact_root"] / "session-two" / "market_aware_session.json").read_bytes()
    )


def test_market_aware_session_blocks_stale_and_unknown_calendar(tmp_path: Path) -> None:
    stale_inputs = _prepare_session(
        tmp_path / "stale",
        calendar_dates=["2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11"],
    )
    stale = _run_session(stale_inputs, "session", "2026-08-11T08:00:00+00:00")
    assert stale["status"] == "stale"
    assert stale["session_ready"] is False
    assert stale["freshness_status"] == "stale"
    assert stale["research_release_ready"] is True

    unknown_inputs = _prepare_session(tmp_path / "unknown")
    unknown = _run_session(unknown_inputs, "session", "2026-08-12T08:00:00+00:00")
    assert unknown["status"] == "calendar_unknown"
    assert unknown["session_ready"] is False


def test_market_aware_session_fails_closed_for_replay_hash_and_path_boundary(
    tmp_path: Path,
) -> None:
    inputs = _prepare_session(tmp_path / "replay-hash")
    replay_report = json.loads(inputs["replay_report"].read_text(encoding="utf-8"))
    replay_report["bundle_sha256"] = "0" * 64
    _write_json(inputs["replay_report"], replay_report)
    replay_invalid = _run_session(inputs, "session", "2026-08-10T13:00:00+00:00")
    assert replay_invalid["status"] == "invalid"
    assert replay_invalid["issues"][0]["code"] == "HASH_MISMATCH"

    boundary_inputs = _prepare_session(tmp_path / "boundary")
    boundary = build_market_aware_session(
        release_manifest_path=boundary_inputs["manifest"],
        release_report_path=boundary_inputs["release_report"],
        replay_report_path=boundary_inputs["replay_report"],
        calendar_path=boundary_inputs["calendar"],
        calendar_report_path=boundary_inputs["calendar_report"],
        evaluation_at=datetime.fromisoformat("2026-08-10T13:00:00+00:00"),
        reference_at=datetime.fromisoformat("2026-08-12T12:00:00+00:00"),
        artifact_root=boundary_inputs["artifact_root"] / "nested-root",
        output_dir=boundary_inputs["artifact_root"] / "session",
    )
    assert boundary["status"] == "invalid"
    assert boundary["issues"][0]["code"] == "ARTIFACT_ROOT_INVALID"


def test_market_aware_session_blocks_review_gate_and_future_evaluation(tmp_path: Path) -> None:
    inputs = _prepare_session(tmp_path / "review")
    replay_report = json.loads(inputs["replay_report"].read_text(encoding="utf-8"))
    replay_report["review_gate_pass"] = False
    _write_json(inputs["replay_report"], replay_report)
    blocked = _run_session(inputs, "blocked", "2026-08-10T13:00:00+00:00")
    assert blocked["status"] == "invalid"
    assert blocked["issues"][0]["code"] == "FIELD_MISMATCH"

    future_inputs = _prepare_session(tmp_path / "future")
    before_release = _run_session(
        future_inputs, "before-release", "2026-08-10T11:00:00+00:00"
    )
    assert before_release["status"] == "invalid"
    assert before_release["issues"][0]["code"] == "RELEASE_AFTER_EVALUATION"

    future = _run_session(
        future_inputs,
        "future",
        "2026-08-12T13:00:00+00:00",
        reference_at="2026-08-12T12:00:00+00:00",
    )
    assert future["status"] == "invalid"
    assert future["issues"][0]["code"] == "TIME_IN_FUTURE"


def test_market_aware_session_cli_returns_success_only_when_ready(
    tmp_path: Path, capsys: Any
) -> None:
    inputs = _prepare_session(tmp_path)
    output_dir = inputs["artifact_root"] / "cli-session"
    exit_code = main(
        [
            "build-market-aware-session",
            "--release-manifest",
            str(inputs["manifest"]),
            "--release-report",
            str(inputs["release_report"]),
            "--replay-report",
            str(inputs["replay_report"]),
            "--calendar",
            str(inputs["calendar"]),
            "--calendar-report",
            str(inputs["calendar_report"]),
            "--evaluation-at",
            "2026-08-10T13:00:00+00:00",
            "--reference-at",
            "2026-08-12T12:00:00+00:00",
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["session_ready"] is True
    assert (output_dir / "market_aware_session.json").exists()
    assert (output_dir / "market_aware_session_report.json").exists()
