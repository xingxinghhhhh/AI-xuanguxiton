"""Compare two reviewed research release manifests without semantic judgement."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .contracts import EVIDENCE_IDS
from .research_release import (
    ANALYSIS_REVIEW_RECORD_VERSION,
    ANALYSIS_REVIEW_VERSION,
    RESEARCH_RELEASE_VERSION,
)

RESEARCH_RELEASE_DIFF_VERSION = "research-release-diff-v1"
REQUIRED_ARTIFACTS = {
    "analysis_review_packet",
    "analysis_review_result",
    "analysis_review_result_report",
    "decision_input_report",
    "decision_input_snapshot",
    "safety_report",
}
CLAIM_COMPARE_FIELDS = ("kind", "text", "citation_ids", "observed_dates")
FORBIDDEN_FIELDS = {
    "action",
    "authorization",
    "buy",
    "decision",
    "decision_type",
    "entry_price",
    "hold",
    "order",
    "position",
    "recommendation",
    "sell",
    "side",
    "stop_loss",
    "take_profit",
    "target_price",
    "trade_decision",
    "quantity",
}


class ResearchReleaseDiffError(ValueError):
    """A fail-closed release comparison error."""

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
        raise ResearchReleaseDiffError("INPUT_JSON_INVALID", f"{label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResearchReleaseDiffError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _require(payload: dict[str, Any], field: str, expected: Any, *, label: str) -> None:
    if payload.get(field) != expected:
        raise ResearchReleaseDiffError("FIELD_MISMATCH", f"{label}.{field} is inconsistent")


def _safe_file(path: Path, *, root: Path, label: str) -> tuple[str, bytes, str]:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ResearchReleaseDiffError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not path.is_file():
        raise ResearchReleaseDiffError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ResearchReleaseDiffError("INPUT_UNAVAILABLE", f"{label}: {exc}") from exc
    return relative.as_posix(), raw, sha256_bytes(raw)


def _validate_reference(
    value: Any, expected_sha: Any, *, root: Path, label: str
) -> None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ResearchReleaseDiffError("PATH_INVALID", f"{label} path is invalid")
    path = (root / value).resolve()
    _relative, _raw, digest = _safe_file(path, root=root, label=label)
    if digest != expected_sha:
        raise ResearchReleaseDiffError("HASH_MISMATCH", f"{label} SHA mismatch")


def _parse_as_of(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ResearchReleaseDiffError("AS_OF_INVALID", f"{label}.as_of is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchReleaseDiffError("AS_OF_INVALID", f"{label}.as_of is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchReleaseDiffError("AS_OF_INVALID", f"{label}.as_of needs timezone")
    if parsed.astimezone(UTC) > datetime.now(UTC):
        raise ResearchReleaseDiffError("AS_OF_IN_FUTURE", f"{label}.as_of is in the future")
    return parsed


def _scan_forbidden_fields(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in FORBIDDEN_FIELDS:
                raise ResearchReleaseDiffError(
                    "FORBIDDEN_DECISION_FIELD", f"{path}.{key} is not allowed"
                )
            _scan_forbidden_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden_fields(child, path=f"{path}[{index}]")


def _claim_view(item: dict[str, Any]) -> dict[str, Any]:
    required = ("review_id", "section", "claim_id", *CLAIM_COMPARE_FIELDS, "citations")
    if any(field not in item for field in required):
        raise ResearchReleaseDiffError("CLAIM_INVALID", "review packet claim fields are incomplete")
    return {
        field: item[field]
        for field in ("review_id", "section", "claim_id", *CLAIM_COMPARE_FIELDS)
    }


def _load_packet_claims(
    packet: dict[str, Any], *, label: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    _require(packet, "analysis_review_version", ANALYSIS_REVIEW_VERSION, label=label)
    _require(packet, "review_packet_ready", True, label=label)
    _require(packet, "review_status", "pending", label=label)
    _require(packet, "review_complete", False, label=label)
    _require(packet, "decision_ready", False, label=label)
    items = packet.get("items")
    if not isinstance(items, list) or not items:
        raise ResearchReleaseDiffError("CLAIM_INVALID", f"{label}.items is required")
    claims: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            raise ResearchReleaseDiffError("CLAIM_INVALID", f"{label}.items[{index}] is invalid")
        item = _claim_view(raw_item)
        review_id = item["review_id"]
        if not isinstance(review_id, str) or not review_id or review_id in claims:
            raise ResearchReleaseDiffError("CLAIM_INVALID", f"{label} review_id is invalid")
        claims[review_id] = item
        citations = raw_item["citations"]
        if not isinstance(citations, list) or not citations:
            raise ResearchReleaseDiffError("EVIDENCE_INVALID", f"{review_id} citations missing")
        for citation in citations:
            if not isinstance(citation, dict):
                raise ResearchReleaseDiffError("EVIDENCE_INVALID", f"{review_id} citation invalid")
            evidence_id = citation.get("evidence_id")
            if evidence_id not in EVIDENCE_IDS:
                raise ResearchReleaseDiffError(
                    "EVIDENCE_INVALID", f"{review_id} evidence ID invalid"
                )
            evidence[evidence_id] = {
                "artifact_paths": citation.get("artifact_paths"),
                "artifact_sha256": citation.get("artifact_sha256"),
                "evidence_id": evidence_id,
                "report_path": citation.get("report_path"),
                "report_sha256": citation.get("report_sha256"),
                "status": "referenced",
            }
    if set(evidence) != set(EVIDENCE_IDS):
        raise ResearchReleaseDiffError("EVIDENCE_INVALID", f"{label} must reference all evidence")
    return claims, evidence


def _validate_release(
    *, manifest_path: Path, report_path: Path, artifact_root: Path, label: str
) -> dict[str, Any]:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise ResearchReleaseDiffError("ARTIFACT_ROOT_INVALID", f"{label} root is not a directory")
    _manifest_relative, manifest_raw, _manifest_sha = _safe_file(
        manifest_path, root=root, label=f"{label} manifest"
    )
    _report_relative, report_raw, _report_sha = _safe_file(
        report_path, root=root, label=f"{label} report"
    )
    manifest, _ = _read_json(manifest_path, label=f"{label} manifest")
    report, _ = _read_json(report_path, label=f"{label} report")
    _scan_forbidden_fields(manifest, path=f"{label}.manifest")
    _scan_forbidden_fields(report, path=f"{label}.report")
    _require(manifest, "research_release_version", RESEARCH_RELEASE_VERSION, label=label)
    _require(report, "research_release_version", RESEARCH_RELEASE_VERSION, label=f"{label} report")
    _require(manifest, "research_release_ready", True, label=label)
    _require(report, "research_release_ready", True, label=f"{label} report")
    _require(manifest, "status", "ready", label=label)
    _require(report, "status", "ready", label=f"{label} report")
    _require(manifest, "decision_ready", False, label=label)
    _require(report, "decision_ready", False, label=f"{label} report")
    if report.get("output_sha256") != sha256_bytes(manifest_raw):
        raise ResearchReleaseDiffError("HASH_MISMATCH", f"{label} report does not match manifest")
    symbol = manifest.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip() or report.get("symbol") != symbol:
        raise ResearchReleaseDiffError("CHAIN_MISMATCH", f"{label} symbol is inconsistent")
    as_of = manifest.get("as_of")
    parsed_as_of = _parse_as_of(as_of, label=label)
    if report.get("as_of") != as_of:
        raise ResearchReleaseDiffError("CHAIN_MISMATCH", f"{label} as_of is inconsistent")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != REQUIRED_ARTIFACTS:
        raise ResearchReleaseDiffError("ARTIFACT_INVALID", f"{label} artifacts are incomplete")
    resolved: dict[str, Path] = {}
    expected_artifacts = {
        "analysis_review_packet": (ANALYSIS_REVIEW_VERSION, "pending"),
        "analysis_review_result": (ANALYSIS_REVIEW_RECORD_VERSION, "ready"),
        "analysis_review_result_report": (ANALYSIS_REVIEW_RECORD_VERSION, "ready"),
        "decision_input_report": ("decision-input-v1", "ready"),
        "decision_input_snapshot": ("decision-input-v1", "ready"),
        "safety_report": ("analysis-safety-v1", "pass"),
    }
    for name, entry in artifacts.items():
        if not isinstance(entry, dict):
            raise ResearchReleaseDiffError("ARTIFACT_INVALID", f"{label}.{name} is invalid")
        path_value = entry.get("path")
        if not isinstance(path_value, str) or not path_value or Path(path_value).is_absolute():
            raise ResearchReleaseDiffError("PATH_INVALID", f"{label}.{name}.path is invalid")
        path = (root / path_value).resolve()
        _relative, raw, digest = _safe_file(path, root=root, label=f"{label}.{name}")
        if entry.get("sha256") != digest:
            raise ResearchReleaseDiffError("HASH_MISMATCH", f"{label}.{name} SHA mismatch")
        expected_version, expected_status = expected_artifacts[name]
        if entry.get("version") != expected_version or entry.get("status") != expected_status:
            raise ResearchReleaseDiffError("FIELD_MISMATCH", f"{label}.{name} metadata is invalid")
        resolved[name] = path
        _scan_forbidden_fields(entry, path=f"{label}.artifacts.{name}")

    snapshot, snapshot_raw = _read_json(
        resolved["decision_input_snapshot"], label=f"{label} decision input"
    )
    snapshot_report, _ = _read_json(
        resolved["decision_input_report"], label=f"{label} decision input report"
    )
    safety, _ = _read_json(resolved["safety_report"], label=f"{label} safety report")
    for name, payload in (
        ("decision_input_snapshot", snapshot),
        ("decision_input_report", snapshot_report),
        ("safety_report", safety),
    ):
        _scan_forbidden_fields(payload, path=f"{label}.{name}")
    _require(snapshot, "decision_input_ready", True, label=f"{label} decision input")
    _require(snapshot_report, "decision_input_ready", True, label=f"{label} decision input report")
    _require(snapshot, "decision_ready", False, label=f"{label} decision input")
    _require(snapshot_report, "decision_ready", False, label=f"{label} decision input report")
    _require(snapshot, "status", "ready", label=f"{label} decision input")
    _require(snapshot_report, "status", "ready", label=f"{label} decision input report")
    if snapshot_report.get("output_sha256") != sha256_bytes(snapshot_raw):
        raise ResearchReleaseDiffError(
            "HASH_MISMATCH", f"{label} decision input report SHA mismatch"
        )
    _require(safety, "safety_ready", True, label=f"{label} safety report")
    _require(safety, "status", "pass", label=f"{label} safety report")
    _require(safety, "decision_ready", False, label=f"{label} safety report")
    input_root_value = snapshot.get("input_root")
    if not isinstance(input_root_value, str) or not input_root_value:
        raise ResearchReleaseDiffError("PATH_INVALID", f"{label} input root is invalid")
    evidence_root = (root / input_root_value).resolve()
    try:
        evidence_root.relative_to(root)
    except ValueError as exc:
        raise ResearchReleaseDiffError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} input root is outside artifact root"
        ) from exc
    if not evidence_root.is_dir():
        raise ResearchReleaseDiffError("INPUT_UNAVAILABLE", f"{label} input root is unavailable")

    packet, packet_raw = _read_json(resolved["analysis_review_packet"], label=f"{label} packet")
    result, result_raw = _read_json(resolved["analysis_review_result"], label=f"{label} result")
    result_report, _ = _read_json(
        resolved["analysis_review_result_report"], label=f"{label} result report"
    )
    _scan_forbidden_fields(packet, path=f"{label}.packet")
    _scan_forbidden_fields(result, path=f"{label}.result")
    _scan_forbidden_fields(result_report, path=f"{label}.result_report")
    claims, evidence = _load_packet_claims(packet, label=f"{label} packet")
    for evidence_id, citation in evidence.items():
        _validate_reference(
            citation["report_path"],
            citation["report_sha256"],
            root=evidence_root,
            label=f"{label}.{evidence_id}.report",
        )
        artifact_paths = citation["artifact_paths"]
        artifact_shas = citation["artifact_sha256"]
        if not isinstance(artifact_paths, list) or not isinstance(artifact_shas, list):
            raise ResearchReleaseDiffError(
                "EVIDENCE_INVALID", f"{label}.{evidence_id} artifacts invalid"
            )
        if len(artifact_paths) != len(artifact_shas):
            raise ResearchReleaseDiffError(
                "EVIDENCE_INVALID", f"{label}.{evidence_id} artifact count mismatch"
            )
        for index, (artifact_path, artifact_sha) in enumerate(zip(artifact_paths, artifact_shas)):
            _validate_reference(
                artifact_path,
                artifact_sha,
                root=evidence_root,
                label=f"{label}.{evidence_id}.artifact[{index}]",
            )
    _require(
        result,
        "analysis_review_record_version",
        ANALYSIS_REVIEW_RECORD_VERSION,
        label=f"{label} result",
    )
    _require(result, "analysis_review_version", ANALYSIS_REVIEW_VERSION, label=f"{label} result")
    _require(result, "status", "ready", label=f"{label} result")
    _require(result, "review_status", "complete", label=f"{label} result")
    _require(result, "review_complete", True, label=f"{label} result")
    _require(result, "review_gate_pass", True, label=f"{label} result")
    _require(result, "decision_ready", False, label=f"{label} result")
    result_items = result.get("items")
    if not isinstance(result_items, list) or len(result_items) != len(claims):
        raise ResearchReleaseDiffError("REVIEW_INCOMPLETE", f"{label} review item count mismatch")
    result_ids: set[str] = set()
    for item in result_items:
        if not isinstance(item, dict) or item.get("status") != "confirmed":
            raise ResearchReleaseDiffError(
                "REVIEW_GATE_FAILED", f"{label} review item is not confirmed"
            )
        review_id = item.get("review_id")
        if not isinstance(review_id, str) or review_id in result_ids or review_id not in claims:
            raise ResearchReleaseDiffError(
                "REVIEW_INCOMPLETE", f"{label} review IDs are inconsistent"
            )
        result_ids.add(review_id)
    if result_ids != set(claims):
        raise ResearchReleaseDiffError("REVIEW_INCOMPLETE", f"{label} review IDs are incomplete")
    _require(
        result_report,
        "analysis_review_record_version",
        ANALYSIS_REVIEW_RECORD_VERSION,
        label=f"{label} result report",
    )
    _require(
        result_report,
        "analysis_review_version",
        ANALYSIS_REVIEW_VERSION,
        label=f"{label} result report",
    )
    _require(result_report, "status", "ready", label=f"{label} result report")
    _require(result_report, "review_status", "complete", label=f"{label} result report")
    _require(result_report, "review_complete", True, label=f"{label} result report")
    _require(result_report, "review_gate_pass", True, label=f"{label} result report")
    _require(result_report, "decision_ready", False, label=f"{label} result report")
    if result.get("review_packet_sha256") != sha256_bytes(packet_raw):
        raise ResearchReleaseDiffError("HASH_MISMATCH", f"{label} result packet SHA mismatch")
    if result_report.get("output_sha256") != sha256_bytes(result_raw):
        raise ResearchReleaseDiffError("HASH_MISMATCH", f"{label} result report SHA mismatch")
    if result_report.get("review_packet_sha256") != sha256_bytes(packet_raw):
        raise ResearchReleaseDiffError(
            "HASH_MISMATCH", f"{label} result report packet SHA mismatch"
        )
    if result_report.get("item_count") != len(result_items):
        raise ResearchReleaseDiffError("FIELD_MISMATCH", f"{label} review item count is invalid")
    chain = [snapshot, snapshot_report, safety, packet, result, result_report]
    for field in ("symbol", "as_of"):
        if any(payload.get(field) != manifest.get(field) for payload in chain):
            raise ResearchReleaseDiffError("CHAIN_MISMATCH", f"{label} {field} is inconsistent")
    return {
        "as_of": as_of,
        "parsed_as_of": parsed_as_of,
        "claims": claims,
        "evidence": evidence,
        "manifest": manifest,
        "report": report,
        "symbol": symbol,
    }


def _claim_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
    changes = {
        field: {"current": current[field], "previous": previous[field]}
        for field in CLAIM_COMPARE_FIELDS
        if previous[field] != current[field]
    }
    if not changes:
        return None
    return {
        "claim_id": current["claim_id"],
        "changes": changes,
        "review_id": current["review_id"],
        "section": current["section"],
    }


def _mapping_diff(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key in sorted(set(previous) | set(current)):
        if key not in previous:
            changes.append({"change": "added", "current": current[key], "key": key})
        elif key not in current:
            changes.append({"change": "removed", "key": key, "previous": previous[key]})
        elif previous[key] != current[key]:
            changes.append(
                {
                    "change": "changed",
                    "current": current[key],
                    "key": key,
                    "previous": previous[key],
                }
            )
        else:
            changes.append({"change": "unchanged", "key": key, "value": current[key]})
    return changes


def _changed_count(changes: list[dict[str, Any]]) -> int:
    return sum(change.get("change") != "unchanged" for change in changes)


def _build_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous_claims = previous["claims"]
    current_claims = current["claims"]
    claim_diffs: dict[str, list[dict[str, Any]]] = {
        "added": [],
        "changed": [],
        "removed": [],
        "unchanged": [],
    }
    for review_id in sorted(set(previous_claims) | set(current_claims)):
        if review_id not in previous_claims:
            claim_diffs["added"].append(current_claims[review_id])
        elif review_id not in current_claims:
            claim_diffs["removed"].append(previous_claims[review_id])
        else:
            changed = _claim_diff(previous_claims[review_id], current_claims[review_id])
            if changed is None:
                claim_diffs["unchanged"].append(review_id)
            else:
                claim_diffs["changed"].append(changed)

    evidence_changes = _mapping_diff(previous["evidence"], current["evidence"])
    artifact_changes = _mapping_diff(
        previous["manifest"]["artifacts"], current["manifest"]["artifacts"]
    )
    risk_ids = {
        key for key, value in current_claims.items() if value["kind"] == "risk"
    } | {key for key, value in previous_claims.items() if value["kind"] == "risk"}
    unknown_ids = {
        key for key, value in current_claims.items() if value["kind"] == "unknown"
    } | {key for key, value in previous_claims.items() if value["kind"] == "unknown"}
    risk_current = sorted(key for key in risk_ids if key in current_claims)
    risk_previous = sorted(key for key in risk_ids if key in previous_claims)
    unknown_current = sorted(key for key in unknown_ids if key in current_claims)
    unknown_previous = sorted(key for key in unknown_ids if key in previous_claims)
    return {
        "as_of": {"current": current["as_of"], "previous": previous["as_of"]},
        "artifact_changes": artifact_changes,
        "claim_changes": claim_diffs,
        "decision_ready": False,
        "evidence_changes": evidence_changes,
        "issues": [],
        "research_release_diff_version": RESEARCH_RELEASE_DIFF_VERSION,
        "risk_changes": {
            "added": sorted(set(risk_current) - set(risk_previous)),
            "current": risk_current,
            "previous": risk_previous,
            "removed": sorted(set(risk_previous) - set(risk_current)),
        },
        "status": "ready",
        "symbol": current["symbol"],
        "unknown_changes": {
            "added": sorted(set(unknown_current) - set(unknown_previous)),
            "current": unknown_current,
            "previous": unknown_previous,
            "removed": sorted(set(unknown_previous) - set(unknown_current)),
        },
        "comparison_ready": True,
    }


def compare_research_releases(
    *,
    previous_manifest_path: Path,
    previous_report_path: Path,
    current_manifest_path: Path,
    current_report_path: Path,
    previous_artifact_root: Path,
    current_artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare two ready research releases using literal structure only."""

    try:
        previous = _validate_release(
            manifest_path=previous_manifest_path,
            report_path=previous_report_path,
            artifact_root=previous_artifact_root,
            label="previous",
        )
        current = _validate_release(
            manifest_path=current_manifest_path,
            report_path=current_report_path,
            artifact_root=current_artifact_root,
            label="current",
        )
        if current["symbol"] != previous["symbol"]:
            raise ResearchReleaseDiffError("SYMBOL_MISMATCH", "release symbols differ")
        if current["parsed_as_of"] <= previous["parsed_as_of"]:
            raise ResearchReleaseDiffError("AS_OF_ORDER_INVALID", "current as_of must be later")
        diff = _build_diff(previous, current)
    except ResearchReleaseDiffError as exc:
        diff = {
            "as_of": {"current": None, "previous": None},
            "artifact_changes": [],
            "claim_changes": {"added": [], "changed": [], "removed": [], "unchanged": []},
            "comparison_ready": False,
            "decision_ready": False,
            "evidence_changes": [],
            "issues": [{"code": exc.code, "message": str(exc)}],
            "research_release_diff_version": RESEARCH_RELEASE_DIFF_VERSION,
            "risk_changes": {"added": [], "current": [], "previous": [], "removed": []},
            "status": "invalid",
            "symbol": None,
            "unknown_changes": {"added": [], "current": [], "previous": [], "removed": []},
        }
    diff_bytes = _json_bytes(diff)
    write_atomic(output_dir / "research_release_diff.json", diff_bytes)
    report = {
        "as_of": diff.get("as_of"),
        "artifact_change_count": _changed_count(diff.get("artifact_changes", [])),
        "claim_added_count": len(diff.get("claim_changes", {}).get("added", [])),
        "claim_changed_count": len(diff.get("claim_changes", {}).get("changed", [])),
        "claim_removed_count": len(diff.get("claim_changes", {}).get("removed", [])),
        "claim_unchanged_count": len(diff.get("claim_changes", {}).get("unchanged", [])),
        "comparison_ready": diff.get("comparison_ready") is True,
        "decision_ready": False,
        "evidence_change_count": _changed_count(diff.get("evidence_changes", [])),
        "issues": diff.get("issues", []),
        "output_sha256": sha256_bytes(diff_bytes),
        "research_release_diff_version": RESEARCH_RELEASE_DIFF_VERSION,
        "status": diff.get("status"),
        "symbol": diff.get("symbol"),
    }
    write_atomic(output_dir / "research_release_diff_report.json", _json_bytes(report))
    return report
