import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.review_record import apply_analysis_review
from a_share_ai.cli import main
from tests.analysis.test_review import _prepare_review, _run

REVIEWED_AT = "2026-08-11T12:00:00+00:00"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_record(tmp_path: Path) -> dict[str, Path]:
    inputs = _prepare_review(tmp_path)
    review_dir = inputs["artifact_root"] / "review"
    _run(inputs, review_dir)
    inputs["packet"] = review_dir / "analysis_review_packet.json"
    inputs["packet_report"] = review_dir / "analysis_review_report.json"
    return inputs


def _submission(inputs: dict[str, Path], *, status: str = "confirmed") -> dict[str, Any]:
    packet = json.loads(inputs["packet"].read_text(encoding="utf-8"))
    packet_report = json.loads(inputs["packet_report"].read_text(encoding="utf-8"))
    items = [
        {
            "review_id": item["review_id"],
            "status": status,
            "notes": None if status == "confirmed" else "Needs human follow-up.",
            "reviewed_at": REVIEWED_AT,
        }
        for item in packet["items"]
    ]
    return {
        "review_packet_sha256": packet_report["output_sha256"],
        "items": items,
    }


def _apply(inputs: dict[str, Path], submission: dict[str, Any], name: str) -> dict[str, Any]:
    submission_path = inputs["artifact_root"] / f"{name}-submission.json"
    _write_json(submission_path, submission)
    report = apply_analysis_review(
        packet_path=inputs["packet"],
        packet_report_path=inputs["packet_report"],
        submission_path=submission_path,
        output_dir=inputs["artifact_root"] / name,
    )
    return report


def test_all_confirmed_review_is_complete_but_not_decision_ready(tmp_path: Path) -> None:
    inputs = _prepare_record(tmp_path)
    submission = _submission(inputs)
    first = _apply(inputs, submission, "confirmed-one")
    second = _apply(inputs, submission, "confirmed-two")

    assert first["status"] == "ready"
    assert first["review_complete"] is True
    assert first["review_gate_pass"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]


def test_challenged_and_follow_up_are_valid_but_fail_review_gate(tmp_path: Path) -> None:
    inputs = _prepare_record(tmp_path)
    submission = _submission(inputs)
    submission["items"][0]["status"] = "challenged"
    submission["items"][0]["notes"] = "Citation needs a manual check."
    submission["items"][1]["status"] = "follow_up"
    submission["items"][1]["notes"] = "Confirm the latest announcement."

    report = _apply(inputs, submission, "challenged")

    assert report["status"] == "ready"
    assert report["review_complete"] is True
    assert report["review_gate_pass"] is False
    assert report["decision_ready"] is False


def test_review_cli_writes_result_and_report(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare_record(tmp_path)
    submission_path = inputs["artifact_root"] / "submission.json"
    _write_json(submission_path, _submission(inputs))
    output_dir = inputs["artifact_root"] / "cli-result"

    exit_code = main(
        [
            "apply-analysis-review",
            "--packet",
            str(inputs["packet"]),
            "--packet-report",
            str(inputs["packet_report"]),
            "--submission",
            str(submission_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert (output_dir / "analysis_review_result.json").exists()
    assert (output_dir / "analysis_review_result_report.json").exists()


def test_missing_duplicate_and_unknown_review_ids_fail_closed(tmp_path: Path) -> None:
    inputs = _prepare_record(tmp_path)
    valid = _submission(inputs)

    missing = {**valid, "items": valid["items"][:-1]}
    assert _apply(inputs, missing, "missing")["issues"][0]["code"] == "REVIEW_ID_MISSING"

    duplicate = {**valid, "items": [*valid["items"], valid["items"][0]]}
    assert _apply(inputs, duplicate, "duplicate")["issues"][0]["code"] == "REVIEW_ID_DUPLICATE"

    unknown_item = {**valid["items"][0], "review_id": "unknown:claim"}
    unknown = {**valid, "items": [unknown_item, *valid["items"][1:]]}
    assert _apply(inputs, unknown, "unknown")["issues"][0]["code"] == "REVIEW_ID_UNKNOWN"


def test_hash_notes_timestamp_and_transaction_fields_fail_closed(tmp_path: Path) -> None:
    inputs = _prepare_record(tmp_path)
    valid = _submission(inputs)

    mismatch = {**valid, "review_packet_sha256": "0" * 64}
    assert _apply(inputs, mismatch, "hash")["issues"][0]["code"] == "PACKET_SHA_MISMATCH"

    no_notes = _submission(inputs, status="challenged")
    no_notes["items"][0]["notes"] = ""
    assert _apply(inputs, no_notes, "notes")["issues"][0]["code"] == "REVIEW_NOTES_REQUIRED"

    no_timezone = _submission(inputs)
    no_timezone["items"][0]["reviewed_at"] = "2026-08-11T12:00:00"
    assert _apply(inputs, no_timezone, "timezone")["issues"][0]["code"] == "REVIEWED_AT_INVALID"

    extra_top_level = {**valid, "decision": "HOLD"}
    assert _apply(inputs, extra_top_level, "extra-top")["issues"][0]["code"] == (
        "SUBMISSION_FIELD_INVALID"
    )

    extra_item = {**valid, "items": [dict(valid["items"][0], decision="BUY"), *valid["items"][1:]]}
    assert _apply(inputs, extra_item, "extra-item")["issues"][0]["code"] == (
        "SUBMISSION_FIELD_INVALID"
    )
