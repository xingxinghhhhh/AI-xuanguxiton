"""Compare two independently audited market-aware session packages."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_package_audit import audit_market_aware_session_package

MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION = "market-aware-session-package-diff-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ROLES = (
    "session",
    "session_report",
    "freshness_report",
    "session_markdown",
    "session_render_report",
)
_SESSION_COMPARE_FIELDS = (
    "as_of",
    "session_version",
    "time_policy",
    "evaluation_at",
    "reference_at",
    "status",
    "session_ready",
    "freshness_status",
    "freshness_ready",
    "research_release_ready",
    "review_complete",
    "review_gate_pass",
    "market_context_summary_version",
    "relative_strength_version",
    "decision_ready",
)
_PACKAGE_COMPARE_FIELDS = ("package_version", "package_ready", "decision_ready")


class MarketAwareSessionPackageDiffError(ValueError):
    """A fail-closed package comparison error."""

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
        payload = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionPackageDiffError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionPackageDiffError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return payload, raw


def _safe_file(path: Path, *, root: Path, label: str) -> tuple[Path, bytes, str]:
    candidate = path.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise MarketAwareSessionPackageDiffError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionPackageDiffError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionPackageDiffError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return candidate, raw, sha256_bytes(raw)


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionPackageDiffError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionPackageDiffError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionPackageDiffError("TIME_INVALID", f"{label} needs timezone")
    return parsed


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MarketAwareSessionPackageDiffError("HASH_INVALID", f"{label} is invalid")
    return value


def _load_side(
    *,
    package_path: Path,
    package_report_path: Path,
    artifact_root: Path,
    side: str,
) -> dict[str, Any]:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise MarketAwareSessionPackageDiffError(
            "ARTIFACT_ROOT_INVALID", f"{side} artifact root is invalid"
        )
    with tempfile.TemporaryDirectory(prefix=f".package-diff-{side}-", dir=root) as audit_dir:
        audit = audit_market_aware_session_package(
            package_path=package_path,
            package_report_path=package_report_path,
            artifact_root=root,
            output_dir=Path(audit_dir),
        )
    if audit.get("audit_ready") is not True:
        issue = audit.get("issues")
        detail = issue[0] if isinstance(issue, list) and issue else {}
        code = (
            detail.get("code", "PACKAGE_AUDIT_FAILED")
            if isinstance(detail, Mapping)
            else "PACKAGE_AUDIT_FAILED"
        )
        message = (
            detail.get("message", f"{side} package audit failed")
            if isinstance(detail, Mapping)
            else f"{side} package audit failed"
        )
        raise MarketAwareSessionPackageDiffError(
            "PACKAGE_AUDIT_FAILED", f"{side}: {code}: {message}"
        )

    package_file, package_raw, package_sha = _safe_file(
        package_path, root=root, label=f"{side} package"
    )
    package, _ = _read_json(package_file, label=f"{side} package")
    if package_sha != audit.get("package_sha256"):
        raise MarketAwareSessionPackageDiffError(
            "INPUT_CHANGED", f"{side} package changed during comparison"
        )
    package_report_file, _package_report_raw, package_report_sha = _safe_file(
        package_report_path, root=root, label=f"{side} package report"
    )
    _package_report, _ = _read_json(package_report_file, label=f"{side} package report")
    if package_report_sha != audit.get("package_report_sha256"):
        raise MarketAwareSessionPackageDiffError(
            "INPUT_CHANGED", f"{side} package report changed during comparison"
        )

    artifacts = package.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_ARTIFACT_ROLES):
        raise MarketAwareSessionPackageDiffError(
            "ARTIFACT_INVALID", f"{side} package artifact list is invalid"
        )
    artifact_map: dict[str, dict[str, Any]] = {}
    for entry in artifacts:
        if not isinstance(entry, Mapping) or entry.get("role") not in _ARTIFACT_ROLES:
            raise MarketAwareSessionPackageDiffError(
                "ARTIFACT_INVALID", f"{side} package artifact role is invalid"
            )
        artifact_map[str(entry["role"])] = dict(entry)
    if set(artifact_map) != set(_ARTIFACT_ROLES):
        raise MarketAwareSessionPackageDiffError(
            "ARTIFACT_INVALID", f"{side} package artifact roles are incomplete"
        )
    session_entry = artifact_map["session"]
    session_path = (root / str(session_entry.get("path"))).resolve()
    session_file, session_raw, session_sha = _safe_file(
        session_path, root=root, label=f"{side} session"
    )
    if session_sha != _validate_sha(session_entry.get("sha256"), label=f"{side} session SHA"):
        raise MarketAwareSessionPackageDiffError(
            "INPUT_CHANGED", f"{side} session changed during comparison"
        )
    session, _ = _read_json(session_file, label=f"{side} session")
    return {
        "artifact_map": artifact_map,
        "audit": audit,
        "package": package,
        "package_raw": package_raw,
        "package_report_sha256": package_report_sha,
        "package_sha256": package_sha,
        "root": root,
        "session": session,
        "session_raw": session_raw,
        "session_sha256": session_sha,
        "symbol": package.get("symbol"),
    }


def _side_values(side: Mapping[str, Any]) -> dict[str, Any]:
    package = side["package"]
    session = side["session"]
    values = {field: session.get(field) for field in _SESSION_COMPARE_FIELDS}
    values.update({field: package.get(field) for field in _PACKAGE_COMPARE_FIELDS})
    return values


def _change_view(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str], list[str]]:
    changes: dict[str, Any] = {}
    changed: list[str] = []
    unchanged: list[str] = []
    for field in (*_SESSION_COMPARE_FIELDS, *_PACKAGE_COMPARE_FIELDS):
        previous_value = previous.get(field)
        current_value = current.get(field)
        status = "unchanged" if previous_value == current_value else "changed"
        changes[field] = {
            "current": current_value,
            "previous": previous_value,
            "status": status,
        }
        (unchanged if status == "unchanged" else changed).append(field)
    return changes, changed, unchanged


def _artifact_change_view(
    previous: Mapping[str, Mapping[str, Any]], current: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], list[str], list[str]]:
    changes: dict[str, Any] = {}
    changed: list[str] = []
    unchanged: list[str] = []
    for role in _ARTIFACT_ROLES:
        previous_entry = {
            key: previous[role].get(key) for key in ("path", "size_bytes", "sha256")
        }
        current_entry = {
            key: current[role].get(key) for key in ("path", "size_bytes", "sha256")
        }
        status = "unchanged" if previous_entry == current_entry else "changed"
        changes[role] = {
            "current": current_entry,
            "previous": previous_entry,
            "status": status,
        }
        (unchanged if status == "unchanged" else changed).append(role)
    return changes, changed, unchanged


def _base_diff() -> dict[str, Any]:
    return {
        "artifact_changes": {},
        "as_of": {"current": None, "previous": None},
        "changed_artifacts": [],
        "changed_fields": [],
        "comparison_ready": False,
        "decision_ready": False,
        "diff_version": MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION,
        "field_changes": {},
        "issues": [],
        "output_sha256": None,
        "previous_as_of": None,
        "current_as_of": None,
        "symbol": None,
        "unchanged_artifacts": [],
        "unchanged_fields": [],
    }


def _base_report() -> dict[str, Any]:
    return {
        "changed_artifact_count": 0,
        "changed_field_count": 0,
        "comparison_ready": False,
        "current_as_of": None,
        "decision_ready": False,
        "diff_version": MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION,
        "issues": [],
        "output_sha256": None,
        "previous_as_of": None,
        "symbol": None,
    }


def _finalize_diff(diff: dict[str, Any]) -> bytes:
    payload = dict(diff)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    diff["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(diff)


def compare_market_aware_session_packages(
    *,
    previous_package_path: Path,
    previous_package_report_path: Path,
    previous_artifact_root: Path,
    current_package_path: Path,
    current_package_report_path: Path,
    current_artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare two independently audited session packages."""

    diff = _base_diff()
    report = _base_report()
    diff_path = output_dir / "market_aware_session_package_diff.json"
    report_path = output_dir / "market_aware_session_package_diff_report.json"
    try:
        previous = _load_side(
            package_path=previous_package_path,
            package_report_path=previous_package_report_path,
            artifact_root=previous_artifact_root,
            side="previous",
        )
        current = _load_side(
            package_path=current_package_path,
            package_report_path=current_package_report_path,
            artifact_root=current_artifact_root,
            side="current",
        )
        if previous["symbol"] != current["symbol"]:
            raise MarketAwareSessionPackageDiffError(
                "SYMBOL_MISMATCH", "previous and current symbols differ"
            )
        previous_as_of = _parse_datetime(previous["package"].get("as_of"), label="previous.as_of")
        current_as_of = _parse_datetime(current["package"].get("as_of"), label="current.as_of")
        if current_as_of.astimezone(UTC) <= previous_as_of.astimezone(UTC):
            raise MarketAwareSessionPackageDiffError(
                "AS_OF_ORDER_INVALID", "current.as_of must be later than previous.as_of"
            )
        field_changes, changed_fields, unchanged_fields = _change_view(
            _side_values(previous), _side_values(current)
        )
        artifact_changes, changed_artifacts, unchanged_artifacts = _artifact_change_view(
            previous["artifact_map"], current["artifact_map"]
        )
        diff.update(
            {
                "artifact_changes": artifact_changes,
                "as_of": {
                    "current": current["package"].get("as_of"),
                    "previous": previous["package"].get("as_of"),
                },
                "changed_artifacts": changed_artifacts,
                "changed_fields": changed_fields,
                "comparison_ready": True,
                "field_changes": field_changes,
                "previous_as_of": previous["package"].get("as_of"),
                "current_as_of": current["package"].get("as_of"),
                "symbol": previous["symbol"],
                "unchanged_artifacts": unchanged_artifacts,
                "unchanged_fields": unchanged_fields,
            }
        )
        report.update(
            {
                "changed_artifact_count": len(changed_artifacts),
                "changed_field_count": len(changed_fields),
                "comparison_ready": True,
                "current_as_of": current["package"].get("as_of"),
                "output_sha256": None,
                "previous_as_of": previous["package"].get("as_of"),
                "symbol": previous["symbol"],
            }
        )
    except MarketAwareSessionPackageDiffError as exc:
        diff["issues"] = [{"code": exc.code, "message": str(exc)}]
        report["issues"] = list(diff["issues"])
    except (OSError, UnicodeError) as exc:
        diff["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
        report["issues"] = list(diff["issues"])

    try:
        diff_raw = _finalize_diff(diff)
        report["output_sha256"] = sha256_bytes(diff_raw)
        output_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(diff_path, diff_raw)
        write_atomic(report_path, _json_bytes(report))
    except OSError as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
        report["comparison_ready"] = False
    return report
