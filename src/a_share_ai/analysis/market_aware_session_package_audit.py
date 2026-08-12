"""Independently audit a market-aware session package and its artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_package import MARKET_AWARE_SESSION_PACKAGE_VERSION
from .market_aware_session_renderer import (
    FRESHNESS_REPORT_NAME,
    MARKET_AWARE_SESSION_RENDER_VERSION,
    MARKET_AWARE_SESSION_VERSION,
    TIME_POLICY,
)

MARKET_AWARE_SESSION_PACKAGE_AUDIT_VERSION = "market-aware-session-package-audit-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ROLES = (
    "session",
    "session_report",
    "freshness_report",
    "session_markdown",
    "session_render_report",
)
_SESSION_FIELDS = (
    "session_version",
    "symbol",
    "as_of",
    "evaluation_at",
    "reference_at",
    "time_policy",
    "status",
    "session_ready",
    "freshness_status",
    "freshness_ready",
    "decision_ready",
    "issues",
)
_SUMMARY_FIELDS = (
    "as_of",
    "evaluation_at",
    "freshness_status",
    "reference_at",
    "session_ready",
    "status",
    "symbol",
)


class MarketAwareSessionPackageAuditError(ValueError):
    """A fail-closed package audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionPackageAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise MarketAwareSessionPackageAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return value


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise MarketAwareSessionPackageAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionPackageAuditError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionPackageAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative.as_posix(),
        "raw": raw,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _read_json_file(path: Path, *, root: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    file_info = _safe_file(path, root=root, label=label)
    return _read_json(file_info["raw"], label=label), file_info


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MarketAwareSessionPackageAuditError(
            "HASH_INVALID", f"{label} must be a SHA-256 string"
        )
    return value


def _parse_datetime(value: Any, *, label: str, allow_none: bool = False) -> datetime | None:
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionPackageAuditError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionPackageAuditError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionPackageAuditError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _relative_manifest_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionPackageAuditError("PATH_INVALID", f"{label} path is invalid")
    path = Path(value)
    if any(part == ".." for part in path.parts):
        raise MarketAwareSessionPackageAuditError(
            "PATH_INVALID", f"{label} path must stay relative"
        )
    return value.replace("\\", "/")


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionPackageAuditError("ISSUES_INVALID", f"{label} must be a list")
    for index, issue in enumerate(value):
        if not isinstance(issue, Mapping):
            raise MarketAwareSessionPackageAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(issue.get("code"), str) or not isinstance(issue.get("message"), str):
            raise MarketAwareSessionPackageAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _same(left: Mapping[str, Any], right: Mapping[str, Any], field: str, *, label: str) -> Any:
    if left.get(field) != right.get(field):
        raise MarketAwareSessionPackageAuditError("FIELD_MISMATCH", f"{label}.{field} differs")
    return left.get(field)


def _validate_summary(package: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    if package.get("package_version") != MARKET_AWARE_SESSION_PACKAGE_VERSION:
        raise MarketAwareSessionPackageAuditError("VERSION_MISMATCH", "package version is invalid")
    if report.get("package_version") != MARKET_AWARE_SESSION_PACKAGE_VERSION:
        raise MarketAwareSessionPackageAuditError(
            "VERSION_MISMATCH", "package report version is invalid"
        )
    if package.get("decision_ready") is not False or report.get("decision_ready") is not False:
        raise MarketAwareSessionPackageAuditError(
            "DECISION_GATE_INVALID", "package and package report decision_ready must be false"
        )
    for field in (*_SUMMARY_FIELDS, "decision_ready", "package_version"):
        _same(package, report, field, label="package/report")
    if report.get("package_ready") is not True:
        raise MarketAwareSessionPackageAuditError(
            "PACKAGE_STATUS_INVALID", "package report package_ready must be true"
        )
    if not isinstance(package.get("session_ready"), bool):
        raise MarketAwareSessionPackageAuditError("FIELD_INVALID", "session_ready must be boolean")
    if not isinstance(package.get("status"), str) or not package.get("status"):
        raise MarketAwareSessionPackageAuditError("FIELD_INVALID", "status must be non-empty")
    if not isinstance(package.get("symbol"), str) or not package.get("symbol").strip():
        raise MarketAwareSessionPackageAuditError("FIELD_INVALID", "symbol must be non-empty")
    _parse_datetime(package.get("evaluation_at"), label="evaluation_at")
    reference_at = _parse_datetime(package.get("reference_at"), label="reference_at")
    _parse_datetime(package.get("as_of"), label="as_of", allow_none=True)
    evaluation_at = _parse_datetime(package.get("evaluation_at"), label="evaluation_at")
    if reference_at is not None and evaluation_at is not None and evaluation_at > reference_at:
        raise MarketAwareSessionPackageAuditError(
            "TIME_IN_FUTURE", "evaluation_at is after reference_at"
        )
    return {field: package.get(field) for field in _SUMMARY_FIELDS}


def _load_artifacts(
    package: Mapping[str, Any], *, root: Path
) -> dict[str, dict[str, Any]]:
    artifacts = package.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_ARTIFACT_ROLES):
        raise MarketAwareSessionPackageAuditError(
            "ARTIFACT_INVALID", "package artifacts must contain five entries"
        )
    resolved: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for index, entry in enumerate(artifacts):
        if not isinstance(entry, Mapping):
            raise MarketAwareSessionPackageAuditError(
                "ARTIFACT_INVALID", f"package artifacts[{index}] is invalid"
            )
        role = entry.get("role")
        if role not in _ARTIFACT_ROLES or role in seen:
            raise MarketAwareSessionPackageAuditError(
                "ARTIFACT_INVALID", f"package artifacts[{index}] role is invalid"
            )
        seen.add(role)
        path_value = _relative_manifest_path(entry.get("path"), label=f"artifacts.{role}")
        expected_sha = _validate_sha(entry.get("sha256"), label=f"artifacts.{role}.sha256")
        size = entry.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise MarketAwareSessionPackageAuditError(
                "SIZE_INVALID", f"artifacts.{role}.size_bytes is invalid"
            )
        file_info = _safe_file(root / path_value, root=root, label=f"artifacts.{role}")
        if file_info["relative_path"] != path_value:
            raise MarketAwareSessionPackageAuditError(
                "PATH_INVALID", f"artifacts.{role}.path is not normalized"
            )
        if file_info["size_bytes"] != size:
            raise MarketAwareSessionPackageAuditError(
                "SIZE_MISMATCH", f"artifacts.{role} size does not match package"
            )
        if file_info["sha256"] != expected_sha:
            raise MarketAwareSessionPackageAuditError(
                "HASH_MISMATCH", f"artifacts.{role} SHA does not match package"
            )
        resolved[role] = {**file_info, "role": role, "declared": dict(entry)}
    if tuple(resolved) != _ARTIFACT_ROLES:
        raise MarketAwareSessionPackageAuditError(
            "ARTIFACT_INVALID", "package artifact roles are incomplete or out of order"
        )
    return resolved


def _validate_artifact_chain(
    *,
    package: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> None:
    session = _read_json(artifacts["session"]["raw"], label="market_aware_session")
    session_report = _read_json(
        artifacts["session_report"]["raw"], label="market_aware_session_report"
    )
    freshness = _read_json(artifacts["freshness_report"]["raw"], label="research_freshness_report")
    render_report = _read_json(
        artifacts["session_render_report"]["raw"],
        label="market_aware_session_render_report",
    )

    for field in _SESSION_FIELDS:
        _same(session, session_report, field, label="session/report")
    if session.get("session_version") != MARKET_AWARE_SESSION_VERSION:
        raise MarketAwareSessionPackageAuditError("VERSION_MISMATCH", "session version is invalid")
    if session.get("time_policy") != TIME_POLICY:
        raise MarketAwareSessionPackageAuditError(
            "TIME_POLICY_INVALID", "session time policy is invalid"
        )
    _parse_datetime(session.get("evaluation_at"), label="session.evaluation_at")
    _parse_datetime(session.get("reference_at"), label="session.reference_at")
    _parse_datetime(session.get("as_of"), label="session.as_of", allow_none=True)
    if session.get("decision_ready") is not False:
        raise MarketAwareSessionPackageAuditError(
            "DECISION_GATE_INVALID", "session decision_ready must be false"
        )
    _validate_issues(session.get("issues"), label="session.issues")
    for field in _SUMMARY_FIELDS:
        if session.get(field) != summary.get(field):
            raise MarketAwareSessionPackageAuditError(
                "FIELD_MISMATCH", f"session {field} differs from package"
            )
    if session_report.get("output_sha256") != artifacts["session"]["sha256"]:
        raise MarketAwareSessionPackageAuditError(
            "HASH_MISMATCH", "session report output SHA does not match session"
        )

    if artifacts["session"]["path"].parent != artifacts["session_report"]["path"].parent:
        raise MarketAwareSessionPackageAuditError(
            "CHAIN_MISMATCH", "session and session report must share a directory"
        )
    if artifacts["freshness_report"]["path"] != (
        artifacts["session"]["path"].parent / FRESHNESS_REPORT_NAME
    ):
        raise MarketAwareSessionPackageAuditError(
            "CHAIN_MISMATCH", "freshness report must be next to session"
        )
    freshness_sha = _validate_sha(
        session.get("freshness_report_sha256"), label="session.freshness_report_sha256"
    )
    if freshness_sha != artifacts["freshness_report"]["sha256"]:
        raise MarketAwareSessionPackageAuditError(
            "HASH_MISMATCH", "freshness report SHA does not match session"
        )
    if freshness.get("research_freshness_version") != "research-freshness-v1":
        raise MarketAwareSessionPackageAuditError(
            "VERSION_MISMATCH", "freshness report version is invalid"
        )
    if freshness.get("decision_ready") is not False:
        raise MarketAwareSessionPackageAuditError(
            "DECISION_GATE_INVALID", "freshness decision_ready must be false"
        )
    for field in ("evaluation_at", "freshness_status", "freshness_ready"):
        if freshness.get(field) != session.get(field):
            raise MarketAwareSessionPackageAuditError(
                "FIELD_MISMATCH", f"freshness {field} differs from session"
            )

    if render_report.get("render_version") != MARKET_AWARE_SESSION_RENDER_VERSION:
        raise MarketAwareSessionPackageAuditError(
            "VERSION_MISMATCH", "session render report version is invalid"
        )
    for field, role in (
        ("session_sha256", "session"),
        ("session_report_sha256", "session_report"),
        ("freshness_report_sha256", "freshness_report"),
    ):
        if render_report.get(field) != artifacts[role]["sha256"]:
            raise MarketAwareSessionPackageAuditError(
                "HASH_MISMATCH", f"render report {field} does not match artifact"
            )
    if render_report.get("output_sha256") != artifacts["session_markdown"]["sha256"]:
        raise MarketAwareSessionPackageAuditError(
            "HASH_MISMATCH", "render report output SHA does not match Markdown"
        )
    for field in (
        "symbol",
        "as_of",
        "evaluation_at",
        "reference_at",
        "status",
        "session_ready",
        "freshness_status",
        "freshness_ready",
        "decision_ready",
    ):
        if render_report.get(field) != session.get(field):
            raise MarketAwareSessionPackageAuditError(
                "FIELD_MISMATCH", f"render report {field} differs from session"
            )
    if render_report.get("session_render_ready") is not (session.get("session_ready") is True):
        raise MarketAwareSessionPackageAuditError(
            "FIELD_MISMATCH", "render report session_render_ready differs from session"
        )
    for field, role in (
        ("session_path", "session"),
        ("session_report_path", "session_report"),
        ("freshness_report_path", "freshness_report"),
    ):
        path_value = _relative_manifest_path(
            render_report.get(field), label=f"render report {field}"
        )
        if path_value != artifacts[role]["relative_path"]:
            raise MarketAwareSessionPackageAuditError(
                "CHAIN_MISMATCH", f"render report {field} is inconsistent"
            )
    if package.get("artifacts") != [artifacts[role]["declared"] for role in _ARTIFACT_ROLES]:
        raise MarketAwareSessionPackageAuditError(
            "ARTIFACT_INVALID", "package artifact ordering is inconsistent"
        )


def _base_report() -> dict[str, Any]:
    return {
        "artifact_count": 0,
        "artifacts": [],
        "as_of": None,
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_PACKAGE_AUDIT_VERSION,
        "decision_ready": False,
        "evaluation_at": None,
        "freshness_status": None,
        "issues": [],
        "output_sha256": None,
        "package_ready": False,
        "package_report_sha256": None,
        "package_sha256": None,
        "package_version": MARKET_AWARE_SESSION_PACKAGE_VERSION,
        "reference_at": None,
        "session_ready": False,
        "status": "invalid",
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def audit_market_aware_session_package(
    *,
    package_path: Path,
    package_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit a package independently and write its deterministic audit report."""

    report = _base_report()
    can_write_report = False
    report_path = output_dir / "market_aware_session_package_audit_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionPackageAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        output_resolved = output_dir.resolve()
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionPackageAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
            ) from exc
        can_write_report = True

        package, package_file = _read_json_file(
            package_path, root=root, label="market_aware_session_package"
        )
        package_report, package_report_file = _read_json_file(
            package_report_path,
            root=root,
            label="market_aware_session_package_report",
        )
        report["package_sha256"] = package_file["sha256"]
        report["package_report_sha256"] = package_report_file["sha256"]
        if package_report.get("output_sha256") != package_file["sha256"]:
            raise MarketAwareSessionPackageAuditError(
                "HASH_MISMATCH", "package report output SHA does not match package manifest"
            )
        summary = _validate_summary(package, package_report)
        artifacts = _load_artifacts(package, root=root)
        _validate_artifact_chain(package=package, artifacts=artifacts, summary=summary)
        manifest_artifacts = [artifacts[role]["declared"] for role in _ARTIFACT_ROLES]
        if package_report.get("artifacts") != manifest_artifacts:
            raise MarketAwareSessionPackageAuditError(
                "ARTIFACT_INVALID", "package report artifacts differ from manifest"
            )
        if package_report.get("artifact_count") != len(_ARTIFACT_ROLES):
            raise MarketAwareSessionPackageAuditError(
                "ARTIFACT_INVALID", "package report artifact_count is invalid"
            )
        report.update(
            {
                **summary,
                "artifact_count": len(_ARTIFACT_ROLES),
                "artifacts": manifest_artifacts,
                "audit_ready": True,
                "package_ready": package_report["package_ready"],
            }
        )
    except MarketAwareSessionPackageAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
