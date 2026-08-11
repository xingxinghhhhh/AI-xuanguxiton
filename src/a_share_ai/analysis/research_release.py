"""Build a fail-closed release manifest for one reviewed research chain."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic

RESEARCH_RELEASE_VERSION = "research-release-v1"
DECISION_INPUT_VERSION = "decision-input-v1"
ANALYSIS_SAFETY_VERSION = "analysis-safety-v1"
ANALYSIS_REVIEW_VERSION = "analysis-review-v1"
ANALYSIS_REVIEW_RECORD_VERSION = "analysis-review-record-v1"


class ResearchReleaseError(ValueError):
    """A fail-closed research release validation error."""

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
        raise ResearchReleaseError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResearchReleaseError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _require(payload: dict[str, Any], field: str, expected: Any, *, label: str) -> None:
    if payload.get(field) != expected:
        raise ResearchReleaseError("FIELD_MISMATCH", f"{label}.{field} is inconsistent")


def _safe_artifact(path: Path, *, root: Path, label: str) -> tuple[str, bytes, str]:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    try:
        relative = path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ResearchReleaseError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not path_resolved.is_file():
        raise ResearchReleaseError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = path_resolved.read_bytes()
    except OSError as exc:
        raise ResearchReleaseError("INPUT_UNAVAILABLE", f"{label}: {exc}") from exc
    return relative.as_posix(), raw, sha256_bytes(raw)


def _parse_as_of(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchReleaseError("AS_OF_INVALID", f"{label}.as_of is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchReleaseError("AS_OF_INVALID", f"{label}.as_of is not ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchReleaseError("AS_OF_INVALID", f"{label}.as_of must include timezone")
    if parsed.astimezone(UTC) > datetime.now(UTC):
        raise ResearchReleaseError("AS_OF_IN_FUTURE", f"{label}.as_of is in the future")
    return value


def _same_field(
    payloads: list[tuple[str, dict[str, Any]]], field: str, *, expected: Any | None = None
) -> Any:
    values = [payload.get(field) for _label, payload in payloads]
    if expected is not None and any(value != expected for value in values):
        raise ResearchReleaseError("FIELD_MISMATCH", f"{field} is inconsistent")
    if len(set(values)) != 1:
        raise ResearchReleaseError("CHAIN_MISMATCH", f"{field} is inconsistent across artifacts")
    return values[0]


def _artifact_entry(
    *, path: Path, root: Path, label: str, version: str, status: str
) -> dict[str, str]:
    relative, _raw, digest = _safe_artifact(path, root=root, label=label)
    return {"path": relative, "sha256": digest, "status": status, "version": version}


def _validate_inputs(
    *,
    decision_input_path: Path,
    decision_input_report_path: Path,
    safety_report_path: Path,
    review_packet_path: Path,
    review_result_path: Path,
    review_result_report_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise ResearchReleaseError("ARTIFACT_ROOT_INVALID", "artifact root must be a directory")

    paths = {
        "decision_input_snapshot": decision_input_path,
        "decision_input_report": decision_input_report_path,
        "safety_report": safety_report_path,
        "analysis_review_packet": review_packet_path,
        "analysis_review_result": review_result_path,
        "analysis_review_result_report": review_result_report_path,
    }
    raw_inputs: dict[str, bytes] = {}
    references: dict[str, dict[str, str]] = {}
    for label, path in paths.items():
        relative, raw, digest = _safe_artifact(path, root=root, label=label)
        raw_inputs[label] = raw
        references[label] = {"path": relative, "sha256": digest}

    snapshot, _ = _read_json(decision_input_path, label="decision_input_snapshot")
    snapshot_report, _ = _read_json(
        decision_input_report_path, label="decision_input_report"
    )
    safety, _ = _read_json(safety_report_path, label="analysis_safety_report")
    packet, packet_raw = _read_json(review_packet_path, label="analysis_review_packet")
    result, result_raw = _read_json(review_result_path, label="analysis_review_result")
    result_report, _ = _read_json(
        review_result_report_path, label="analysis_review_result_report"
    )

    _require(snapshot, "decision_input_version", DECISION_INPUT_VERSION, label="snapshot")
    _require(snapshot_report, "decision_input_version", DECISION_INPUT_VERSION, label="report")
    _require(snapshot, "decision_input_ready", True, label="snapshot")
    _require(snapshot_report, "decision_input_ready", True, label="report")
    _require(snapshot, "decision_ready", False, label="snapshot")
    _require(snapshot_report, "decision_ready", False, label="report")
    _require(snapshot, "status", "ready", label="snapshot")
    _require(snapshot_report, "status", "ready", label="report")
    if snapshot_report.get("output_sha256") != sha256_bytes(raw_inputs["decision_input_snapshot"]):
        raise ResearchReleaseError("HASH_MISMATCH", "decision input report does not match snapshot")

    _require(safety, "analysis_safety_version", ANALYSIS_SAFETY_VERSION, label="safety")
    _require(safety, "safety_ready", True, label="safety")
    _require(safety, "decision_input_ready", True, label="safety")
    _require(safety, "decision_ready", False, label="safety")
    _require(safety, "status", "pass", label="safety")

    _require(packet, "analysis_review_version", ANALYSIS_REVIEW_VERSION, label="packet")
    _require(packet, "review_packet_ready", True, label="packet")
    _require(packet, "review_status", "pending", label="packet")
    _require(packet, "review_complete", False, label="packet")
    _require(packet, "decision_ready", False, label="packet")

    _require(
        result,
        "analysis_review_record_version",
        ANALYSIS_REVIEW_RECORD_VERSION,
        label="result",
    )
    _require(result, "analysis_review_version", ANALYSIS_REVIEW_VERSION, label="result")
    _require(result, "status", "ready", label="result")
    _require(result, "review_status", "complete", label="result")
    _require(result, "review_complete", True, label="result")
    _require(result, "review_gate_pass", True, label="result")
    _require(result, "decision_ready", False, label="result")
    if result.get("review_packet_sha256") != sha256_bytes(packet_raw):
        raise ResearchReleaseError("HASH_MISMATCH", "review result does not match packet")
    result_items = result.get("items")
    if not isinstance(result_items, list) or not result_items:
        raise ResearchReleaseError("REVIEW_INCOMPLETE", "review result items are required")
    if any(item.get("status") != "confirmed" for item in result_items if isinstance(item, dict)):
        raise ResearchReleaseError("REVIEW_GATE_FAILED", "review result contains unconfirmed items")
    if any(not isinstance(item, dict) for item in result_items):
        raise ResearchReleaseError("REVIEW_INCOMPLETE", "review result contains invalid items")

    _require(
        result_report,
        "analysis_review_record_version",
        ANALYSIS_REVIEW_RECORD_VERSION,
        label="result_report",
    )
    _require(
        result_report,
        "analysis_review_version",
        ANALYSIS_REVIEW_VERSION,
        label="result_report",
    )
    _require(result_report, "status", "ready", label="result_report")
    _require(result_report, "review_status", "complete", label="result_report")
    _require(result_report, "review_complete", True, label="result_report")
    _require(result_report, "review_gate_pass", True, label="result_report")
    _require(result_report, "decision_ready", False, label="result_report")
    if result_report.get("output_sha256") != sha256_bytes(result_raw):
        raise ResearchReleaseError("HASH_MISMATCH", "review result report does not match result")
    if result_report.get("review_packet_sha256") != sha256_bytes(packet_raw):
        raise ResearchReleaseError("HASH_MISMATCH", "review result report packet SHA mismatch")
    if result_report.get("item_count") != len(result_items):
        raise ResearchReleaseError("FIELD_MISMATCH", "review result item count is inconsistent")

    chain = [
        ("snapshot", snapshot),
        ("snapshot_report", snapshot_report),
        ("safety", safety),
        ("packet", packet),
        ("result", result),
        ("result_report", result_report),
    ]
    symbol = _same_field(chain, "symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ResearchReleaseError("SYMBOL_INVALID", "symbol is required")
    as_of = _same_field(chain, "as_of")
    _parse_as_of(as_of, label="release")
    if packet.get("decision_input_sha256") != references["decision_input_snapshot"]["sha256"]:
        raise ResearchReleaseError("HASH_MISMATCH", "packet decision input SHA mismatch")
    if packet.get("safety_report_sha256") != references["safety_report"]["sha256"]:
        raise ResearchReleaseError("HASH_MISMATCH", "packet safety report SHA mismatch")
    snapshot_analysis = snapshot.get("analysis")
    snapshot_analysis_report = snapshot.get("analysis_report")
    if not isinstance(snapshot_analysis, dict) or not isinstance(snapshot_analysis_report, dict):
        raise ResearchReleaseError(
            "CHAIN_MISMATCH", "decision input analysis references are missing"
        )
    if packet.get("analysis_sha256") != snapshot_analysis.get("sha256"):
        raise ResearchReleaseError("CHAIN_MISMATCH", "analysis SHA mismatch")
    if packet.get("analysis_report_sha256") != snapshot_analysis_report.get("sha256"):
        raise ResearchReleaseError("CHAIN_MISMATCH", "analysis report chain mismatch")
    if packet.get("quality_report_sha256") != safety.get("quality_report_sha256"):
        raise ResearchReleaseError("CHAIN_MISMATCH", "quality report chain mismatch")
    if packet.get("render_report_sha256") != safety.get("render_report_sha256"):
        raise ResearchReleaseError("CHAIN_MISMATCH", "render report chain mismatch")

    manifest_artifacts = {
        "decision_input_snapshot": _artifact_entry(
            path=decision_input_path,
            root=root,
            label="decision_input_snapshot",
            version=DECISION_INPUT_VERSION,
            status="ready",
        ),
        "decision_input_report": _artifact_entry(
            path=decision_input_report_path,
            root=root,
            label="decision_input_report",
            version=DECISION_INPUT_VERSION,
            status="ready",
        ),
        "safety_report": _artifact_entry(
            path=safety_report_path,
            root=root,
            label="safety_report",
            version=ANALYSIS_SAFETY_VERSION,
            status="pass",
        ),
        "analysis_review_packet": _artifact_entry(
            path=review_packet_path,
            root=root,
            label="analysis_review_packet",
            version=ANALYSIS_REVIEW_VERSION,
            status="pending",
        ),
        "analysis_review_result": _artifact_entry(
            path=review_result_path,
            root=root,
            label="analysis_review_result",
            version=ANALYSIS_REVIEW_RECORD_VERSION,
            status="ready",
        ),
        "analysis_review_result_report": _artifact_entry(
            path=review_result_report_path,
            root=root,
            label="analysis_review_result_report",
            version=ANALYSIS_REVIEW_RECORD_VERSION,
            status="ready",
        ),
    }
    return {
        "artifacts": manifest_artifacts,
        "as_of": as_of,
        "decision_input_ready": True,
        "review_complete": True,
        "review_gate_pass": True,
        "safety_ready": True,
        "symbol": symbol,
    }


def build_research_release(
    *,
    decision_input_path: Path,
    decision_input_report_path: Path,
    safety_report_path: Path,
    review_packet_path: Path,
    review_result_path: Path,
    review_result_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a final research release manifest without making a decision."""

    try:
        details = _validate_inputs(
            decision_input_path=decision_input_path,
            decision_input_report_path=decision_input_report_path,
            safety_report_path=safety_report_path,
            review_packet_path=review_packet_path,
            review_result_path=review_result_path,
            review_result_report_path=review_result_report_path,
            artifact_root=artifact_root,
        )
        manifest: dict[str, Any] = {
            "as_of": details["as_of"],
            "artifacts": details["artifacts"],
            "decision_input_ready": True,
            "decision_ready": False,
            "issues": [],
            "research_release_ready": True,
            "research_release_version": RESEARCH_RELEASE_VERSION,
            "review_complete": True,
            "review_gate_pass": True,
            "safety_ready": True,
            "status": "ready",
            "symbol": details["symbol"],
        }
    except ResearchReleaseError as exc:
        manifest = {
            "as_of": None,
            "artifacts": {},
            "decision_input_ready": False,
            "decision_ready": False,
            "issues": [{"code": exc.code, "message": str(exc)}],
            "research_release_ready": False,
            "research_release_version": RESEARCH_RELEASE_VERSION,
            "review_complete": False,
            "review_gate_pass": False,
            "safety_ready": False,
            "status": "invalid",
            "symbol": None,
        }
    manifest_bytes = _json_bytes(manifest)
    write_atomic(output_dir / "research_release_manifest.json", manifest_bytes)
    report = {
        "as_of": manifest.get("as_of"),
        "artifact_count": len(manifest.get("artifacts", {})),
        "decision_input_ready": manifest.get("decision_input_ready") is True,
        "decision_ready": False,
        "issues": manifest.get("issues", []),
        "output_sha256": sha256_bytes(manifest_bytes),
        "research_release_ready": manifest.get("research_release_ready") is True,
        "research_release_version": RESEARCH_RELEASE_VERSION,
        "review_complete": manifest.get("review_complete") is True,
        "review_gate_pass": manifest.get("review_gate_pass") is True,
        "safety_ready": manifest.get("safety_ready") is True,
        "status": manifest.get("status"),
        "symbol": manifest.get("symbol"),
    }
    write_atomic(output_dir / "research_release_report.json", _json_bytes(report))
    return report
