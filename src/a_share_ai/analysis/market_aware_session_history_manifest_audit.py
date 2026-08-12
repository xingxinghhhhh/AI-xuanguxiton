"""Independently audit the Node41 market-aware session history manifest."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history_manifest import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_MANIFEST_AUDIT_VERSION = (
    "market-aware-session-history-manifest-audit-v1"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLES = (
    "history",
    "history_report",
    "history_audit_report",
    "history_markdown",
    "history_render_report",
    "history_render_audit_report",
)


class MarketAwareSessionHistoryManifestAuditError(ValueError):
    """A fail-closed manifest audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionHistoryManifestAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryManifestAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryManifestAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryManifestAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative,
        "raw": raw,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MarketAwareSessionHistoryManifestAuditError(
            "HASH_INVALID", f"{label} must be a SHA-256 string"
        )
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _validate_sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryManifestAuditError(
            "HASH_MISMATCH", f"{label}.output_sha256 does not match canonical report"
        )


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryManifestAuditError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryManifestAuditError(
            "TIME_INVALID", f"{label} must include timezone"
        )
    return parsed


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryManifestAuditError(
            "PATH_INVALID", f"{label} must be relative"
        )
    if any(part == ".." for part in Path(value).parts):
        raise MarketAwareSessionHistoryManifestAuditError(
            "PATH_INVALID", f"{label} must stay within artifact root"
        )
    return value.replace("\\", "/")


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryManifestAuditError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryManifestAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryManifestAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_chain(
    *,
    manifest_file: Mapping[str, Any],
    report_file: Mapping[str, Any],
    artifact_files: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    manifest_report: Mapping[str, Any],
) -> dict[str, Any]:
    if manifest.get("manifest_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION:
        raise MarketAwareSessionHistoryManifestAuditError(
            "VERSION_MISMATCH", "manifest version is invalid"
        )
    if manifest_report.get("manifest_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION:
        raise MarketAwareSessionHistoryManifestAuditError(
            "VERSION_MISMATCH", "manifest report version is invalid"
        )
    for label, payload in (("manifest", manifest), ("manifest_report", manifest_report)):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryManifestAuditError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    if manifest_report.get("manifest_sha256") != manifest_file["sha256"]:
        raise MarketAwareSessionHistoryManifestAuditError(
            "HASH_MISMATCH", "manifest report SHA does not match manifest"
        )
    _validate_sha(manifest_report.get("manifest_sha256"), label="manifest_report.manifest_sha256")
    _validate_self_hash(manifest_report, label="manifest_report")
    if manifest_report.get("manifest_path") != manifest_file["relative_path"]:
        raise MarketAwareSessionHistoryManifestAuditError(
            "CHAIN_MISMATCH", "manifest report path is inconsistent"
        )
    if manifest_report.get("manifest_ready") is not (manifest.get("manifest_ready") is True):
        raise MarketAwareSessionHistoryManifestAuditError(
            "FIELD_MISMATCH", "manifest_ready differs between manifest and report"
        )
    if manifest_report.get("status") not in {"ready", "invalid"}:
        raise MarketAwareSessionHistoryManifestAuditError(
            "FIELD_INVALID", "manifest report status is invalid"
        )
    if manifest.get("manifest_ready") is not True or manifest_report.get("status") != "ready":
        raise MarketAwareSessionHistoryManifestAuditError(
            "UPSTREAM_NOT_READY", "manifest is not ready"
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_ROLES):
        raise MarketAwareSessionHistoryManifestAuditError(
            "ARTIFACT_INVALID", "manifest must contain exactly six artifacts"
        )
    by_role: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(artifacts):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryManifestAuditError(
                "ARTIFACT_INVALID", f"artifacts[{index}] is invalid"
            )
        role = item.get("role")
        if role not in _ROLES or role in by_role:
            raise MarketAwareSessionHistoryManifestAuditError(
                "ARTIFACT_ROLE_INVALID", "artifact roles must be unique and known"
            )
        by_role[role] = item
        declared_path = _relative_path(item.get("path"), label=f"artifacts[{index}].path")
        if declared_path != artifact_files[role]["relative_path"]:
            raise MarketAwareSessionHistoryManifestAuditError(
                "CHAIN_MISMATCH", f"artifact {role} path is inconsistent"
            )
        declared_sha = _validate_sha(item.get("sha256"), label=f"artifacts[{index}].sha256")
        if declared_sha != artifact_files[role]["sha256"]:
            raise MarketAwareSessionHistoryManifestAuditError(
                "HASH_MISMATCH", f"artifact {role} SHA does not match file"
            )
        if item.get("byte_count") != artifact_files[role]["size_bytes"]:
            raise MarketAwareSessionHistoryManifestAuditError(
                "SIZE_MISMATCH", f"artifact {role} byte count does not match file"
            )
    if set(by_role) != set(_ROLES):
        raise MarketAwareSessionHistoryManifestAuditError(
            "ARTIFACT_ROLE_INVALID", "manifest roles are incomplete"
        )
    if len({artifact_files[role]["path"] for role in _ROLES}) != len(_ROLES):
        raise MarketAwareSessionHistoryManifestAuditError(
            "PATH_INVALID", "artifact files must be distinct"
        )
    for role in (
        "history",
        "history_report",
        "history_audit_report",
        "history_render_report",
        "history_render_audit_report",
    ):
        try:
            artifact_files[role]["raw"].decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MarketAwareSessionHistoryManifestAuditError(
                "INPUT_TEXT_INVALID", f"{role} is not valid UTF-8"
            ) from exc
    _parse_datetime(manifest.get("first_as_of"), label="manifest.first_as_of")
    _parse_datetime(manifest.get("last_as_of"), label="manifest.last_as_of")
    first = _parse_datetime(manifest.get("first_as_of"), label="manifest.first_as_of")
    last = _parse_datetime(manifest.get("last_as_of"), label="manifest.last_as_of")
    if last < first:
        raise MarketAwareSessionHistoryManifestAuditError(
            "TIME_ORDER_INVALID", "manifest time bounds are reversed"
        )
    if not isinstance(manifest.get("package_count"), int) or manifest.get("package_count") < 1:
        raise MarketAwareSessionHistoryManifestAuditError(
            "FIELD_INVALID", "manifest package_count is invalid"
        )
    for field in (
        "symbol",
        "package_count",
        "first_as_of",
        "last_as_of",
        "history_ready",
        "history_audit_ready",
        "render_ready",
        "render_audit_ready",
    ):
        if field not in manifest:
            raise MarketAwareSessionHistoryManifestAuditError(
                "FIELD_INVALID", f"manifest.{field} is missing"
            )
    return {
        "first_as_of": manifest.get("first_as_of"),
        "history_audit_ready": manifest.get("history_audit_ready"),
        "history_ready": manifest.get("history_ready"),
        "last_as_of": manifest.get("last_as_of"),
        "manifest_ready": manifest.get("manifest_ready"),
        "package_count": manifest.get("package_count"),
        "render_audit_ready": manifest.get("render_audit_ready"),
        "render_ready": manifest.get("render_ready"),
        "symbol": manifest.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_HISTORY_MANIFEST_AUDIT_VERSION,
        "artifacts": [],
        "decision_ready": False,
        "first_as_of": None,
        "history_audit_ready": False,
        "history_ready": False,
        "history_report_sha256": None,
        "history_render_audit_ready": False,
        "issues": [],
        "last_as_of": None,
        "manifest_path": None,
        "manifest_report_path": None,
        "manifest_report_sha256": None,
        "manifest_sha256": None,
        "manifest_ready": False,
        "output_sha256": None,
        "package_count": 0,
        "render_audit_ready": False,
        "render_ready": False,
        "status": "invalid",
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def audit_market_aware_session_history_manifest(
    *,
    manifest_path: Path,
    manifest_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit Node41 manifest and its six declared files without rebuilding."""

    report = _base_report()
    can_write_report = False
    audit_report_path = output_dir / "market_aware_session_history_manifest_audit_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryManifestAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryManifestAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
            ) from exc
        can_write_report = True
        manifest_file = _safe_file(manifest_path, root=root, label="manifest")
        report_file = _safe_file(manifest_report_path, root=root, label="manifest report")
        manifest = _read_json(manifest_file["raw"], label="manifest")
        manifest_report = _read_json(report_file["raw"], label="manifest report")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            raise MarketAwareSessionHistoryManifestAuditError(
                "ARTIFACT_INVALID", "manifest artifacts are required"
            )
        declared: dict[str, str] = {}
        for item in artifacts:
            if isinstance(item, Mapping) and isinstance(item.get("role"), str):
                role = item["role"]
                if role in _ROLES and isinstance(item.get("path"), str):
                    declared[role] = item["path"]
        missing_roles = [role for role in _ROLES if role not in declared]
        if missing_roles:
            raise MarketAwareSessionHistoryManifestAuditError(
                "ARTIFACT_ROLE_INVALID", "manifest roles are incomplete"
            )
        artifact_files = {
            role: _safe_file(root / declared[role], root=root, label=f"artifact {role}")
            for role in _ROLES
        }
        summary = _validate_chain(
            manifest_file=manifest_file,
            report_file=report_file,
            artifact_files=artifact_files,
            manifest=manifest,
            manifest_report=manifest_report,
        )
        manifest_by_role = {
            item["role"]: item for item in manifest["artifacts"] if isinstance(item, Mapping)
        }
        audit_artifacts = [
            {
                "actual_byte_count": artifact_files[role]["size_bytes"],
                "actual_sha256": artifact_files[role]["sha256"],
                "byte_count": manifest_by_role[role]["byte_count"],
                "path": artifact_files[role]["relative_path"],
                "role": role,
                "sha256": manifest_by_role[role]["sha256"],
            }
            for role in _ROLES
        ]
        report.update(
            {
                **summary,
                "artifacts": audit_artifacts,
                "audit_ready": True,
                "manifest_path": manifest_file["relative_path"],
                "manifest_report_path": report_file["relative_path"],
                "manifest_report_sha256": report_file["sha256"],
                "manifest_sha256": manifest_file["sha256"],
                "status": "ready",
            }
        )
    except MarketAwareSessionHistoryManifestAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
