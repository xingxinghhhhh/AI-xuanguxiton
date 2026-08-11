"""Validate user-supplied human review records without changing claims."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic

ANALYSIS_REVIEW_RECORD_VERSION = "analysis-review-record-v1"
ANALYSIS_REVIEW_VERSION = "analysis-review-v1"
ALLOWED_REVIEW_STATUSES = ("confirmed", "challenged", "follow_up")
SUBMISSION_FIELDS = {"review_packet_sha256", "items"}
SUBMISSION_ITEM_FIELDS = {"review_id", "status", "notes", "reviewed_at"}


class AnalysisReviewRecordError(ValueError):
    """A fail-closed review record error with a stable issue code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisReviewRecordError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisReviewRecordError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _require(payload: dict[str, Any], field: str, expected: Any, *, label: str) -> None:
    if payload.get(field) != expected:
        raise AnalysisReviewRecordError("FIELD_MISMATCH", f"{label}.{field} is inconsistent")


def _parse_reviewed_at(value: Any, *, review_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisReviewRecordError(
            "REVIEWED_AT_INVALID", f"{review_id}.reviewed_at is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalysisReviewRecordError(
            "REVIEWED_AT_INVALID", f"{review_id}.reviewed_at is not ISO datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnalysisReviewRecordError(
            "REVIEWED_AT_INVALID", f"{review_id}.reviewed_at must include timezone"
        )
    return value


def _validate_packet(
    *, packet_path: Path, packet_report_path: Path
) -> tuple[dict[str, Any], bytes, str]:
    packet, packet_raw = _read_json(packet_path, label="analysis_review_packet")
    packet_report, _ = _read_json(packet_report_path, label="analysis_review_report")
    packet_sha = sha256_bytes(packet_raw)
    _require(packet, "analysis_review_version", ANALYSIS_REVIEW_VERSION, label="packet")
    _require(packet_report, "analysis_review_version", ANALYSIS_REVIEW_VERSION, label="report")
    _require(packet, "review_packet_ready", True, label="packet")
    _require(packet_report, "review_packet_ready", True, label="report")
    _require(packet, "review_status", "pending", label="packet")
    _require(packet_report, "review_status", "pending", label="report")
    _require(packet, "review_complete", False, label="packet")
    _require(packet_report, "review_complete", False, label="report")
    _require(packet, "decision_ready", False, label="packet")
    _require(packet_report, "decision_ready", False, label="report")
    if packet_report.get("status") != "pending":
        raise AnalysisReviewRecordError("UPSTREAM_NOT_READY", "review report is not pending")
    if packet_report.get("output_sha256") != packet_sha:
        raise AnalysisReviewRecordError("HASH_MISMATCH", "review packet SHA does not match report")
    items = packet.get("items")
    if not isinstance(items, list) or not items:
        raise AnalysisReviewRecordError("PACKET_INVALID", "review packet items are required")
    packet_ids: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise AnalysisReviewRecordError("PACKET_INVALID", f"packet.items[{index}] is invalid")
        review_id = item.get("review_id")
        if not isinstance(review_id, str) or not review_id:
            raise AnalysisReviewRecordError("PACKET_INVALID", f"packet.items[{index}] ID invalid")
        if review_id in packet_ids:
            raise AnalysisReviewRecordError(
                "PACKET_DUPLICATE_ID", f"duplicate packet ID {review_id}"
            )
        if item.get("review_status") != "pending":
            raise AnalysisReviewRecordError("PACKET_INVALID", f"{review_id} is not pending")
        if item.get("review_notes") is not None or item.get("reviewed_at") is not None:
            raise AnalysisReviewRecordError("PACKET_INVALID", f"{review_id} is already reviewed")
        packet_ids.append(review_id)
    return packet, packet_raw, packet_sha


def _validate_submission(
    *, submission: dict[str, Any], packet: dict[str, Any], packet_sha: str
) -> list[dict[str, Any]]:
    if set(submission) != SUBMISSION_FIELDS:
        raise AnalysisReviewRecordError(
            "SUBMISSION_FIELD_INVALID", "submission contains unsupported fields"
        )
    if submission.get("review_packet_sha256") != packet_sha:
        raise AnalysisReviewRecordError("PACKET_SHA_MISMATCH", "submission packet SHA differs")
    submitted_items = submission.get("items")
    packet_items = packet["items"]
    if not isinstance(submitted_items, list):
        raise AnalysisReviewRecordError("SUBMISSION_INVALID", "submission.items must be a list")
    packet_ids = [item["review_id"] for item in packet_items]
    records: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(submitted_items):
        if not isinstance(item, dict) or set(item) != SUBMISSION_ITEM_FIELDS:
            raise AnalysisReviewRecordError(
                "SUBMISSION_FIELD_INVALID", f"submission.items[{index}] fields are invalid"
            )
        review_id = item.get("review_id")
        if not isinstance(review_id, str) or review_id not in packet_ids:
            raise AnalysisReviewRecordError("REVIEW_ID_UNKNOWN", f"unknown review ID {review_id}")
        if review_id in records:
            raise AnalysisReviewRecordError(
                "REVIEW_ID_DUPLICATE", f"duplicate review ID {review_id}"
            )
        status = item.get("status")
        if status not in ALLOWED_REVIEW_STATUSES:
            raise AnalysisReviewRecordError("REVIEW_STATUS_INVALID", f"{review_id}.status invalid")
        notes = item.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise AnalysisReviewRecordError("REVIEW_NOTES_INVALID", f"{review_id}.notes invalid")
        if status in {"challenged", "follow_up"} and not isinstance(notes, str):
            raise AnalysisReviewRecordError(
                "REVIEW_NOTES_REQUIRED", f"{review_id} requires notes"
            )
        if status in {"challenged", "follow_up"} and not notes.strip():
            raise AnalysisReviewRecordError(
                "REVIEW_NOTES_REQUIRED", f"{review_id} requires non-empty notes"
            )
        reviewed_at = _parse_reviewed_at(item.get("reviewed_at"), review_id=review_id)
        records[review_id] = {
            "notes": notes,
            "review_id": review_id,
            "reviewed_at": reviewed_at,
            "status": status,
        }
    if set(records) != set(packet_ids):
        missing = sorted(set(packet_ids) - set(records))
        raise AnalysisReviewRecordError(
            "REVIEW_ID_MISSING", f"submission is missing review IDs: {','.join(missing)}"
        )
    return [records[review_id] for review_id in packet_ids]


def apply_analysis_review(
    *,
    packet_path: Path,
    packet_report_path: Path,
    submission_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Apply a user-supplied review record and write an immutable result."""

    try:
        packet, _packet_raw, packet_sha = _validate_packet(
            packet_path=packet_path,
            packet_report_path=packet_report_path,
        )
        submission, _submission_raw = _read_json(submission_path, label="review_submission")
        records = _validate_submission(
            submission=submission,
            packet=packet,
            packet_sha=packet_sha,
        )
        review_complete = len(records) == len(packet["items"])
        review_gate_pass = review_complete and all(
            record["status"] == "confirmed" for record in records
        )
        result: dict[str, Any] = {
            "analysis_review_record_version": ANALYSIS_REVIEW_RECORD_VERSION,
            "analysis_review_version": ANALYSIS_REVIEW_VERSION,
            "as_of": packet.get("as_of"),
            "decision_ready": False,
            "items": records,
            "issues": [],
            "review_complete": review_complete,
            "review_gate_pass": review_gate_pass,
            "review_packet_sha256": packet_sha,
            "review_status": "complete" if review_complete else "pending",
            "status": "ready",
            "symbol": packet.get("symbol"),
        }
    except AnalysisReviewRecordError as exc:
        result = {
            "analysis_review_record_version": ANALYSIS_REVIEW_RECORD_VERSION,
            "analysis_review_version": ANALYSIS_REVIEW_VERSION,
            "as_of": None,
            "decision_ready": False,
            "issues": [{"code": exc.code, "message": str(exc)}],
            "items": [],
            "review_complete": False,
            "review_gate_pass": False,
            "review_packet_sha256": None,
            "review_status": "invalid",
            "status": "invalid",
            "symbol": None,
        }
    result_bytes = _json_bytes(result)
    write_atomic(output_dir / "analysis_review_result.json", result_bytes)
    report = {
        "analysis_review_record_version": ANALYSIS_REVIEW_RECORD_VERSION,
        "analysis_review_version": ANALYSIS_REVIEW_VERSION,
        "as_of": result.get("as_of"),
        "decision_ready": False,
        "issues": result.get("issues", []),
        "item_count": len(result.get("items", [])),
        "output_sha256": sha256_bytes(result_bytes),
        "review_complete": result.get("review_complete") is True,
        "review_gate_pass": result.get("review_gate_pass") is True,
        "review_packet_sha256": result.get("review_packet_sha256"),
        "review_status": result.get("review_status"),
        "status": result.get("status"),
        "symbol": result.get("symbol"),
    }
    write_atomic(output_dir / "analysis_review_result_report.json", _json_bytes(report))
    return report
