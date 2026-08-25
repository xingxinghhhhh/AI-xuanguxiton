"""Build a deterministic, pending-only human review packet."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .contracts import ANALYSIS_REPORT_VERSION, ANALYSIS_SECTIONS, EVIDENCE_IDS

ANALYSIS_REVIEW_VERSION = "analysis-review-v1"
DECISION_INPUT_VERSION = "decision-input-v1"
ANALYSIS_SAFETY_VERSION = "analysis-safety-v1"

CLAIM_FIELDS = {"claim_id", "citation_ids", "kind", "observed_dates", "text"}


class AnalysisReviewError(ValueError):
    """A fail-closed review packet validation error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisReviewError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalysisReviewError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _safe_path(root: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise AnalysisReviewError("PATH_INVALID", f"{label} must be relative")
    if ".." in Path(value).parts:
        raise AnalysisReviewError("PATH_INVALID", f"{label} must not escape")
    root_resolved = root.resolve()
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise AnalysisReviewError("PATH_OUTSIDE_ROOT", f"{label} escapes root") from exc
    return candidate


def _file_sha(path: Path, *, label: str) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise AnalysisReviewError("FILE_UNAVAILABLE", f"{label}: {exc}") from exc


def _require(payload: Mapping[str, Any], field: str, expected: Any, *, label: str) -> None:
    if payload.get(field) != expected:
        raise AnalysisReviewError("FIELD_MISMATCH", f"{label}.{field} is inconsistent")


def _require_sha(payload: Mapping[str, Any], field: str, actual: str, *, label: str) -> None:
    if payload.get(field) != actual:
        raise AnalysisReviewError("HASH_MISMATCH", f"{label}.{field} does not match")


def _reference(
    snapshot: Mapping[str, Any],
    name: str,
    *,
    artifact_root: Path,
    label: str,
) -> tuple[dict[str, Any], Path, str]:
    entry = snapshot.get(name)
    if not isinstance(entry, dict):
        raise AnalysisReviewError("SNAPSHOT_FIELD_INVALID", f"snapshot.{name} is invalid")
    path = _safe_path(artifact_root, entry.get("path"), label=f"snapshot.{name}.path")
    actual = _file_sha(path, label=label)
    _require_sha(entry, "sha256", actual, label=f"snapshot.{name}")
    return entry, path, actual


def _validate_citation(
    citation: Mapping[str, Any], *, input_root: Path, evidence_id: str
) -> dict[str, Any]:
    report_path = _safe_path(
        input_root,
        citation.get("report_path"),
        label=f"citation.{evidence_id}.report_path",
    )
    report_sha = _file_sha(report_path, label=f"citation.{evidence_id}.report")
    _require_sha(citation, "report_sha256", report_sha, label=f"citation.{evidence_id}")
    artifact_paths = citation.get("artifact_paths")
    artifact_shas = citation.get("artifact_sha256")
    if not isinstance(artifact_paths, list) or not isinstance(artifact_shas, list):
        raise AnalysisReviewError("CITATION_INVALID", f"citation.{evidence_id} manifest invalid")
    if len(artifact_paths) != len(artifact_shas):
        raise AnalysisReviewError("CITATION_INVALID", f"citation.{evidence_id} manifest differs")
    resolved_artifacts: list[dict[str, str]] = []
    for index, (path_value, expected_sha) in enumerate(zip(artifact_paths, artifact_shas)):
        artifact_path = _safe_path(
            input_root,
            path_value,
            label=f"citation.{evidence_id}.artifact[{index}]",
        )
        actual_sha = _file_sha(
            artifact_path,
            label=f"citation.{evidence_id}.artifact[{index}]",
        )
        if expected_sha != actual_sha:
            raise AnalysisReviewError(
                "HASH_MISMATCH", f"citation.{evidence_id}.artifact[{index}] does not match"
            )
        resolved_artifacts.append(
            {"path": Path(path_value).as_posix(), "sha256": actual_sha}
        )
    return {
        "artifact_paths": [item["path"] for item in resolved_artifacts],
        "artifact_sha256": [item["sha256"] for item in resolved_artifacts],
        "evidence_id": evidence_id,
        "report_path": Path(citation["report_path"]).as_posix(),
        "report_sha256": report_sha,
    }


def _validate_inputs(
    *,
    decision_input_path: Path,
    decision_input_report_path: Path,
    safety_report_path: Path,
    analysis_path: Path,
    analysis_report_path: Path,
    artifact_root: Path,
) -> dict[str, Any]:
    snapshot, snapshot_raw = _read_json(decision_input_path, label="decision_input_snapshot")
    decision_report, decision_report_raw = _read_json(
        decision_input_report_path, label="decision_input_report"
    )
    safety_report, _safety_report_raw = _read_json(
        safety_report_path, label="analysis_safety_report"
    )
    analysis, _analysis_raw = _read_json(analysis_path, label="analysis")
    analysis_report, _analysis_report_raw = _read_json(
        analysis_report_path, label="analysis_report"
    )

    _require(snapshot, "decision_input_version", DECISION_INPUT_VERSION, label="snapshot")
    _require(decision_report, "decision_input_version", DECISION_INPUT_VERSION, label="report")
    _require(snapshot, "status", "ready", label="snapshot")
    _require(decision_report, "status", "ready", label="report")
    _require(snapshot, "decision_input_ready", True, label="snapshot")
    _require(decision_report, "decision_input_ready", True, label="report")
    _require(snapshot, "decision_ready", False, label="snapshot")
    _require(decision_report, "decision_ready", False, label="report")
    _require_sha(
        decision_report,
        "output_sha256",
        sha256_bytes(snapshot_raw),
        label="decision_input_report",
    )
    if decision_report_raw == b"":
        raise AnalysisReviewError("INPUT_JSON_INVALID", "decision input report is empty")

    symbol = snapshot.get("symbol")
    as_of = snapshot.get("as_of")
    if not isinstance(symbol, str) or not isinstance(as_of, str):
        raise AnalysisReviewError("SNAPSHOT_FIELD_INVALID", "snapshot symbol/as_of is invalid")
    try:
        parsed_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnalysisReviewError("AS_OF_INVALID", "snapshot as_of is invalid") from exc
    if parsed_as_of.tzinfo is None or parsed_as_of.utcoffset() is None:
        raise AnalysisReviewError("AS_OF_INVALID", "snapshot as_of needs timezone")
    if parsed_as_of > datetime.now(UTC):
        raise AnalysisReviewError("AS_OF_IN_FUTURE", "snapshot as_of is in the future")

    entry_names = (
        "input_bundle",
        "input_bundle_report",
        "analysis",
        "analysis_report",
        "render",
        "quality",
    )
    entries: dict[str, tuple[dict[str, Any], Path, str]] = {
        name: _reference(snapshot, name, artifact_root=artifact_root, label=name)
        for name in entry_names
    }
    analysis_entry, expected_analysis_path, analysis_sha = entries["analysis"]
    analysis_report_entry, expected_analysis_report_path, analysis_report_sha = entries[
        "analysis_report"
    ]
    _, _, bundle_sha = entries["input_bundle"]
    _, _, quality_sha = entries["quality"]
    _, _, render_report_sha = entries["render"]
    if analysis_path.resolve() != expected_analysis_path.resolve():
        raise AnalysisReviewError("PATH_MISMATCH", "analysis path differs from snapshot")
    if analysis_report_path.resolve() != expected_analysis_report_path.resolve():
        raise AnalysisReviewError("PATH_MISMATCH", "analysis report path differs from snapshot")

    safety_sha = _file_sha(safety_report_path, label="analysis_safety_report")
    try:
        safety_relative = safety_report_path.resolve().relative_to(artifact_root.resolve())
    except ValueError as exc:
        raise AnalysisReviewError(
            "PATH_OUTSIDE_ROOT", "safety report escapes artifact root"
        ) from exc
    _safe_path(artifact_root, safety_relative.as_posix(), label="safety report")
    _require(safety_report, "analysis_safety_version", ANALYSIS_SAFETY_VERSION, label="safety")
    _require(safety_report, "status", "pass", label="safety")
    _require(safety_report, "safety_ready", True, label="safety")
    _require(safety_report, "decision_input_ready", True, label="safety")
    _require(safety_report, "decision_ready", False, label="safety")
    if safety_report.get("decision_input_sha256") != sha256_bytes(snapshot_raw):
        raise AnalysisReviewError("HASH_MISMATCH", "safety snapshot SHA differs")
    if safety_report.get("analysis_sha256") != analysis_sha:
        raise AnalysisReviewError("HASH_MISMATCH", "safety analysis SHA differs")
    if safety_report.get("quality_report_sha256") != quality_sha:
        raise AnalysisReviewError("HASH_MISMATCH", "safety quality SHA differs")
    if safety_report.get("render_report_sha256") != render_report_sha:
        raise AnalysisReviewError("HASH_MISMATCH", "safety render SHA differs")
    if safety_report.get("symbol") != symbol or safety_report.get("as_of") != as_of:
        raise AnalysisReviewError("SYMBOL_AS_OF_MISMATCH", "safety symbol/as_of differs")

    if analysis_entry.get("analysis_ready") is not True:
        raise AnalysisReviewError("READY_GATE_INVALID", "analysis snapshot entry is not ready")
    if snapshot.get("quality", {}).get("quality_ready") is not True:
        raise AnalysisReviewError("READY_GATE_INVALID", "quality snapshot entry is not ready")

    for payload, label in ((analysis, "analysis"), (analysis_report, "analysis_report")):
        if payload.get("symbol") != symbol or payload.get("as_of") != as_of:
            raise AnalysisReviewError("SYMBOL_AS_OF_MISMATCH", f"{label} symbol/as_of differs")
        if payload.get("decision_ready") is not False or payload.get("analysis_ready") is not True:
            raise AnalysisReviewError("READY_GATE_INVALID", f"{label} is not analysis-ready")
        if payload.get("status") != "ready":
            raise AnalysisReviewError("UPSTREAM_NOT_READY", f"{label} is not ready")
    _require(analysis, "analysis_version", ANALYSIS_REPORT_VERSION, label="analysis")
    _require(analysis_report, "analysis_version", ANALYSIS_REPORT_VERSION, label="analysis_report")
    _require_sha(analysis_report, "output_sha256", analysis_sha, label="analysis_report")
    if analysis_report.get("input_bundle_sha256") != bundle_sha:
        raise AnalysisReviewError("HASH_MISMATCH", "analysis bundle SHA differs")

    input_root = _safe_path(artifact_root, snapshot.get("input_root"), label="snapshot.input_root")
    bundle_path = entries["input_bundle"][1]
    bundle_from_report = _safe_path(
        input_root,
        analysis_report.get("input_bundle_path"),
        label="analysis_report.input_bundle_path",
    )
    if bundle_from_report != bundle_path.resolve():
        raise AnalysisReviewError("PATH_MISMATCH", "analysis bundle path differs")

    citations = analysis.get("citations")
    if not isinstance(citations, list):
        raise AnalysisReviewError("CITATIONS_INVALID", "analysis citations must be a list")
    citation_map: dict[str, dict[str, Any]] = {}
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            raise AnalysisReviewError("CITATION_INVALID", f"citations[{index}] is invalid")
        evidence_id = citation.get("evidence_id")
        if evidence_id not in EVIDENCE_IDS or evidence_id in citation_map:
            raise AnalysisReviewError("CITATION_INVALID", f"citations[{index}] evidence ID invalid")
        citation_map[evidence_id] = _validate_citation(
            citation, input_root=input_root, evidence_id=evidence_id
        )
    if set(citation_map) != set(EVIDENCE_IDS):
        raise AnalysisReviewError("EVIDENCE_COVERAGE", "all six evidence citations are required")

    sections = analysis.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(ANALYSIS_SECTIONS):
        raise AnalysisReviewError("SECTIONS_INVALID", "analysis sections are invalid")
    items: list[dict[str, Any]] = []
    section_counts: dict[str, int] = {}
    seen_review_ids: set[str] = set()
    risk_count = 0
    unknown_count = 0
    for section in ANALYSIS_SECTIONS:
        claims = sections[section]
        if not isinstance(claims, list):
            raise AnalysisReviewError("CLAIMS_INVALID", f"section {section} is invalid")
        section_counts[section] = len(claims)
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or set(claim) != CLAIM_FIELDS:
                raise AnalysisReviewError("CLAIM_INVALID", f"{section}[{index}] is invalid")
            claim_id = claim.get("claim_id")
            kind = claim.get("kind")
            text = claim.get("text")
            citation_ids = claim.get("citation_ids")
            observed_dates = claim.get("observed_dates")
            if (
                not isinstance(claim_id, str)
                or not claim_id
                or not isinstance(kind, str)
                or not isinstance(text, str)
                or not isinstance(citation_ids, list)
                or not citation_ids
                or not isinstance(observed_dates, list)
            ):
                raise AnalysisReviewError("CLAIM_INVALID", f"{section}[{index}] fields are invalid")
            if any(citation_id not in citation_map for citation_id in citation_ids):
                raise AnalysisReviewError(
                    "CITATION_UNKNOWN", f"{section}[{index}] citation unknown"
                )
            review_id = f"{section}:{claim_id}"
            if review_id in seen_review_ids:
                raise AnalysisReviewError("REVIEW_ID_DUPLICATE", f"duplicate review ID {review_id}")
            seen_review_ids.add(review_id)
            if kind == "risk":
                risk_count += 1
            if kind == "unknown":
                unknown_count += 1
            items.append(
                {
                    "citation_ids": list(citation_ids),
                    "citations": [citation_map[citation_id] for citation_id in citation_ids],
                    "claim_id": claim_id,
                    "kind": kind,
                    "observed_dates": list(observed_dates),
                    "review_id": review_id,
                    "review_notes": None,
                    "review_status": "pending",
                    "reviewed_at": None,
                    "section": section,
                    "text": text,
                }
            )
    if not items:
        raise AnalysisReviewError("CLAIM_COVERAGE", "analysis contains no review claims")

    return {
        "analysis_report_sha256": analysis_report_sha,
        "analysis_sha256": analysis_sha,
        "as_of": as_of,
        "claim_count": len(items),
        "decision_input_sha256": sha256_bytes(snapshot_raw),
        "items": items,
        "quality_report_sha256": quality_sha,
        "render_report_sha256": render_report_sha,
        "risk_claim_count": risk_count,
        "safety_report_sha256": safety_sha,
        "section_claim_counts": section_counts,
        "symbol": symbol,
        "unknown_claim_count": unknown_count,
    }


def build_analysis_review(
    *,
    decision_input_path: Path,
    decision_input_report_path: Path,
    safety_report_path: Path,
    analysis_path: Path,
    analysis_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a pending-only human review packet and its audit report."""

    try:
        details = _validate_inputs(
            decision_input_path=decision_input_path,
            decision_input_report_path=decision_input_report_path,
            safety_report_path=safety_report_path,
            analysis_path=analysis_path,
            analysis_report_path=analysis_report_path,
            artifact_root=artifact_root,
        )
        packet: dict[str, Any] = {
            "analysis_report_sha256": details["analysis_report_sha256"],
            "analysis_review_version": ANALYSIS_REVIEW_VERSION,
            "analysis_sha256": details["analysis_sha256"],
            "as_of": details["as_of"],
            "claim_count": details["claim_count"],
            "decision_input_ready": True,
            "decision_input_sha256": details["decision_input_sha256"],
            "decision_ready": False,
            "items": details["items"],
            "manual_review_required": True,
            "quality_report_sha256": details["quality_report_sha256"],
            "render_report_sha256": details["render_report_sha256"],
            "review_complete": False,
            "review_status": "pending",
            "review_packet_ready": True,
            "risk_claim_count": details["risk_claim_count"],
            "safety_report_sha256": details["safety_report_sha256"],
            "section_claim_counts": details["section_claim_counts"],
            "symbol": details["symbol"],
            "unknown_claim_count": details["unknown_claim_count"],
        }
    except AnalysisReviewError as exc:
        packet = {
            "analysis_review_version": ANALYSIS_REVIEW_VERSION,
            "decision_input_ready": False,
            "decision_ready": False,
            "issues": [{"code": exc.code, "message": str(exc)}],
            "manual_review_required": True,
            "review_complete": False,
            "review_packet_ready": False,
            "review_status": "invalid",
            "symbol": None,
            "as_of": None,
        }
    packet_bytes = _json_bytes(packet)
    write_atomic(output_dir / "analysis_review_packet.json", packet_bytes)
    report = {
        "analysis_review_version": ANALYSIS_REVIEW_VERSION,
        "as_of": packet.get("as_of"),
        "claim_count": packet.get("claim_count", 0),
        "decision_input_ready": packet.get("decision_input_ready") is True,
        "decision_ready": False,
        "issues": packet.get("issues", []),
        "manual_review_required": True,
        "output_sha256": sha256_bytes(packet_bytes),
        "review_complete": False,
        "review_packet_ready": packet.get("review_packet_ready") is True,
        "review_status": packet.get("review_status"),
        "risk_claim_count": packet.get("risk_claim_count", 0),
        "section_claim_counts": packet.get("section_claim_counts", {}),
        "status": packet.get("review_status"),
        "symbol": packet.get("symbol"),
        "unknown_claim_count": packet.get("unknown_claim_count", 0),
    }
    write_atomic(output_dir / "analysis_review_report.json", _json_bytes(report))
    return report
