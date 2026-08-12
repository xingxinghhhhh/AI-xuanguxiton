"""Independently audit the Node43 manifest Markdown render."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history_manifest import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION,
)
from .market_aware_session_history_manifest_audit import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_AUDIT_VERSION,
)
from .market_aware_session_history_manifest_renderer import (
    MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_AUDIT_VERSION = (
    "market-aware-session-history-manifest-render-audit-v1"
)
_ROLES = (
    "history",
    "history_report",
    "history_audit_report",
    "history_markdown",
    "history_render_report",
    "history_render_audit_report",
)


class MarketAwareSessionHistoryManifestRenderAuditError(ValueError):
    """A fail-closed manifest-render audit error."""

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
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative,
        "raw": raw,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = payload.get("output_sha256")
    if not isinstance(declared, str) or len(declared) != 64:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "PATH_INVALID", f"{label} must be relative"
        )
    if any(part == ".." for part in Path(value).parts):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "PATH_INVALID", f"{label} must stay within artifact root"
        )
    return value.replace("\\", "/")


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    manifest_report: Mapping[str, Any],
    audit_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
) -> dict[str, Any]:
    if manifest.get("manifest_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "VERSION_MISMATCH", "manifest version is invalid"
        )
    if manifest_report.get("manifest_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "VERSION_MISMATCH", "manifest report version is invalid"
        )
    if audit_report.get("audit_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_AUDIT_VERSION:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "VERSION_MISMATCH", "manifest audit version is invalid"
        )
    if render_report.get("render_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_VERSION:
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "VERSION_MISMATCH", "render report version is invalid"
        )
    for label, payload in (
        ("manifest", manifest),
        ("manifest_report", manifest_report),
        ("audit_report", audit_report),
        ("render_report", render_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    _validate_self_hash(manifest_report, label="manifest_report")
    _validate_self_hash(audit_report, label="audit_report")
    for source, role, field in (
        (manifest_report, "manifest", "manifest_sha256"),
        (audit_report, "manifest", "manifest_sha256"),
        (audit_report, "manifest_report", "manifest_report_sha256"),
        (render_report, "manifest", "manifest_sha256"),
        (render_report, "manifest_report", "manifest_report_sha256"),
        (render_report, "manifest_audit_report", "audit_report_sha256"),
        (render_report, "markdown", "output_sha256"),
    ):
        if source.get(field) != files[role]["sha256"]:
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "HASH_MISMATCH", f"{field} does not match {role}"
            )
    for source, role, field in (
        (render_report, "manifest", "manifest_path"),
        (render_report, "manifest_report", "manifest_report_path"),
        (render_report, "manifest_audit_report", "audit_report_path"),
    ):
        if _relative_path(source.get(field), label=field) != files[role]["relative_path"]:
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "CHAIN_MISMATCH", f"{field} is inconsistent"
            )
    for field in (
        "symbol",
        "package_count",
        "first_as_of",
        "last_as_of",
        "manifest_ready",
        "render_ready",
    ):
        if render_report.get(field) != manifest.get(field):
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "FIELD_MISMATCH", f"render report {field} differs"
            )
        if audit_report.get(field) != manifest.get(field):
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "FIELD_MISMATCH", f"audit report {field} differs"
            )
    if render_report.get("audit_ready") is not (audit_report.get("audit_ready") is True):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "FIELD_MISMATCH", "render report audit_ready differs"
        )
    if manifest_report.get("manifest_ready") is not (manifest.get("manifest_ready") is True):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "FIELD_MISMATCH", "manifest report manifest_ready differs"
        )
    _parse_datetime(manifest.get("first_as_of"), label="manifest.first_as_of")
    _parse_datetime(manifest.get("last_as_of"), label="manifest.last_as_of")
    artifacts = manifest.get("artifacts")
    audit_artifacts = audit_report.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_ROLES):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "ARTIFACT_INVALID", "manifest artifacts are incomplete"
        )
    if not isinstance(audit_artifacts, list) or len(audit_artifacts) != len(_ROLES):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "ARTIFACT_INVALID", "audit artifacts are incomplete"
        )
    manifest_by_role = {
        item.get("role"): item for item in artifacts if isinstance(item, Mapping)
    }
    audit_by_role = {
        item.get("role"): item for item in audit_artifacts if isinstance(item, Mapping)
    }
    if set(manifest_by_role) != set(_ROLES) or set(audit_by_role) != set(_ROLES):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "ARTIFACT_ROLE_INVALID", "artifact roles are incomplete"
        )
    for role in _ROLES:
        for field in ("path", "byte_count", "sha256"):
            if manifest_by_role[role].get(field) != audit_by_role[role].get(field):
                raise MarketAwareSessionHistoryManifestRenderAuditError(
                    "FIELD_MISMATCH", f"artifact {role} {field} differs"
                )
    if len({files[role]["path"] for role in files}) != len(files):
        raise MarketAwareSessionHistoryManifestRenderAuditError(
            "PATH_INVALID", "audit inputs must be distinct files"
        )
    return {
        "audit_ready": audit_report.get("audit_ready"),
        "first_as_of": manifest.get("first_as_of"),
        "last_as_of": manifest.get("last_as_of"),
        "manifest_ready": manifest.get("manifest_ready"),
        "package_count": manifest.get("package_count"),
        "render_ready": render_report.get("render_ready"),
        "symbol": manifest.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_AUDIT_VERSION,
        "decision_ready": False,
        "first_as_of": None,
        "issues": [],
        "last_as_of": None,
        "manifest_audit_report_path": None,
        "manifest_audit_report_sha256": None,
        "manifest_path": None,
        "manifest_report_path": None,
        "manifest_report_sha256": None,
        "manifest_sha256": None,
        "manifest_ready": False,
        "markdown_path": None,
        "markdown_sha256": None,
        "output_sha256": None,
        "package_count": 0,
        "render_ready": False,
        "render_report_path": None,
        "render_report_sha256": None,
        "status": "invalid",
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def audit_market_aware_session_history_manifest_render(
    *,
    manifest_path: Path,
    manifest_report_path: Path,
    manifest_audit_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit Node43 Markdown and render report without re-rendering."""

    report = _base_report()
    can_write_report = False
    audit_report_path = (
        output_dir / "market_aware_session_history_manifest_render_audit_report.json"
    )
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "manifest": _safe_file(manifest_path, root=root, label="manifest"),
            "manifest_report": _safe_file(
                manifest_report_path, root=root, label="manifest report"
            ),
            "manifest_audit_report": _safe_file(
                manifest_audit_report_path, root=root, label="manifest audit report"
            ),
            "markdown": _safe_file(markdown_path, root=root, label="Markdown"),
            "render_report": _safe_file(
                render_report_path, root=root, label="render report"
            ),
        }
        manifest = _read_json(files["manifest"]["raw"], label="manifest")
        manifest_report = _read_json(
            files["manifest_report"]["raw"], label="manifest report"
        )
        audit_report = _read_json(
            files["manifest_audit_report"]["raw"], label="manifest audit report"
        )
        render_report = _read_json(
            files["render_report"]["raw"], label="render report"
        )
        try:
            files["markdown"]["raw"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketAwareSessionHistoryManifestRenderAuditError(
                "MARKDOWN_INVALID", "Markdown is not valid UTF-8"
            ) from exc
        summary = _validate_chain(
            files=files,
            manifest=manifest,
            manifest_report=manifest_report,
            audit_report=audit_report,
            render_report=render_report,
        )
        report.update(
            {
                **summary,
                "audit_ready": True,
                "manifest_audit_report_path": files["manifest_audit_report"]["relative_path"],
                "manifest_audit_report_sha256": files["manifest_audit_report"]["sha256"],
                "manifest_path": files["manifest"]["relative_path"],
                "manifest_report_path": files["manifest_report"]["relative_path"],
                "manifest_report_sha256": files["manifest_report"]["sha256"],
                "manifest_sha256": files["manifest"]["sha256"],
                "markdown_path": files["markdown"]["relative_path"],
                "markdown_sha256": files["markdown"]["sha256"],
                "render_report_path": files["render_report"]["relative_path"],
                "render_report_sha256": files["render_report"]["sha256"],
                "status": "ready",
            }
        )
    except MarketAwareSessionHistoryManifestRenderAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
