"""Render the Node41 manifest and Node42 audit as safe deterministic Markdown."""

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

MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_VERSION = (
    "market-aware-session-history-manifest-render-v1"
)
_ROLES = (
    "history",
    "history_report",
    "history_audit_report",
    "history_markdown",
    "history_render_report",
    "history_render_audit_report",
)


class MarketAwareSessionHistoryManifestRenderError(ValueError):
    """A fail-closed manifest renderer error."""

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
        raise MarketAwareSessionHistoryManifestRenderError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryManifestRenderError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload, raw


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestRenderError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryManifestRenderError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryManifestRenderError(
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
        raise MarketAwareSessionHistoryManifestRenderError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryManifestRenderError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise MarketAwareSessionHistoryManifestRenderError(
            "FIELD_INVALID", f"{label} must be a string"
        )
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
        .replace("#", "\\#")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _safe_value(value: Any, *, label: str) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return _safe_text(value, label=label)


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryManifestRenderError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestRenderError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryManifestRenderError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryManifestRenderError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryManifestRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryManifestRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_inputs(
    *,
    files: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
    manifest_report: Mapping[str, Any],
    audit_report: Mapping[str, Any],
) -> dict[str, Any]:
    if manifest.get("manifest_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION:
        raise MarketAwareSessionHistoryManifestRenderError(
            "VERSION_MISMATCH", "manifest version is invalid"
        )
    if manifest_report.get("manifest_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION:
        raise MarketAwareSessionHistoryManifestRenderError(
            "VERSION_MISMATCH", "manifest report version is invalid"
        )
    if audit_report.get("audit_version") != MARKET_AWARE_SESSION_HISTORY_MANIFEST_AUDIT_VERSION:
        raise MarketAwareSessionHistoryManifestRenderError(
            "VERSION_MISMATCH", "manifest audit report version is invalid"
        )
    for label, payload in (
        ("manifest", manifest),
        ("manifest_report", manifest_report),
        ("audit_report", audit_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryManifestRenderError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    if manifest_report.get("manifest_sha256") != files["manifest"]["sha256"]:
        raise MarketAwareSessionHistoryManifestRenderError(
            "HASH_MISMATCH", "manifest report SHA does not match manifest"
        )
    if audit_report.get("manifest_sha256") != files["manifest"]["sha256"]:
        raise MarketAwareSessionHistoryManifestRenderError(
            "HASH_MISMATCH", "audit report manifest SHA does not match manifest"
        )
    if audit_report.get("manifest_report_sha256") != files["manifest_report"]["sha256"]:
        raise MarketAwareSessionHistoryManifestRenderError(
            "HASH_MISMATCH", "audit report manifest report SHA does not match report"
        )
    if manifest_report.get("manifest_path") != files["manifest"]["relative_path"]:
        raise MarketAwareSessionHistoryManifestRenderError(
            "CHAIN_MISMATCH", "manifest report path is inconsistent"
        )
    if audit_report.get("manifest_path") != files["manifest"]["relative_path"]:
        raise MarketAwareSessionHistoryManifestRenderError(
            "CHAIN_MISMATCH", "audit report manifest path is inconsistent"
        )
    if audit_report.get("manifest_report_path") != files["manifest_report"]["relative_path"]:
        raise MarketAwareSessionHistoryManifestRenderError(
            "CHAIN_MISMATCH", "audit report manifest report path is inconsistent"
        )
    _validate_self_hash(manifest_report, label="manifest_report")
    _validate_self_hash(audit_report, label="audit_report")
    if manifest_report.get("status") != "ready" or audit_report.get("status") != "ready":
        raise MarketAwareSessionHistoryManifestRenderError(
            "UPSTREAM_NOT_READY", "upstream status is not ready"
        )
    if audit_report.get("audit_ready") is not True:
        raise MarketAwareSessionHistoryManifestRenderError(
            "UPSTREAM_NOT_READY", "manifest audit is not ready"
        )
    if manifest.get("manifest_ready") is not True or manifest_report.get(
        "manifest_ready"
    ) is not True:
        raise MarketAwareSessionHistoryManifestRenderError(
            "UPSTREAM_NOT_READY", "manifest is not ready"
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
        if manifest.get(field) != audit_report.get(field):
            raise MarketAwareSessionHistoryManifestRenderError(
                "FIELD_MISMATCH", f"audit report {field} differs"
            )
    _parse_datetime(manifest.get("first_as_of"), label="manifest.first_as_of")
    _parse_datetime(manifest.get("last_as_of"), label="manifest.last_as_of")
    artifacts = manifest.get("artifacts")
    audit_artifacts = audit_report.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(_ROLES):
        raise MarketAwareSessionHistoryManifestRenderError(
            "ARTIFACT_INVALID", "manifest artifacts are incomplete"
        )
    if not isinstance(audit_artifacts, list) or len(audit_artifacts) != len(_ROLES):
        raise MarketAwareSessionHistoryManifestRenderError(
            "ARTIFACT_INVALID", "audit artifacts are incomplete"
        )
    manifest_by_role = {
        item.get("role"): item for item in artifacts if isinstance(item, Mapping)
    }
    audit_by_role = {
        item.get("role"): item for item in audit_artifacts if isinstance(item, Mapping)
    }
    if set(manifest_by_role) != set(_ROLES) or set(audit_by_role) != set(_ROLES):
        raise MarketAwareSessionHistoryManifestRenderError(
            "ARTIFACT_ROLE_INVALID", "artifact roles are incomplete"
        )
    for role in _ROLES:
        for field in ("path", "byte_count", "sha256"):
            if manifest_by_role[role].get(field) != audit_by_role[role].get(field):
                raise MarketAwareSessionHistoryManifestRenderError(
                    "FIELD_MISMATCH", f"artifact {role} {field} differs"
                )
    if len({files[role]["path"] for role in ("manifest", "manifest_report", "audit_report")}) != 3:
        raise MarketAwareSessionHistoryManifestRenderError(
            "PATH_INVALID", "render inputs must be distinct files"
        )
    return {
        "audit_report": audit_report,
        "files": files,
        "manifest": manifest,
        "manifest_report": manifest_report,
        "manifest_by_role": manifest_by_role,
    }


def _render_markdown(metadata: Mapping[str, Any]) -> bytes:
    manifest = metadata["manifest"]
    audit_report = metadata["audit_report"]
    manifest_version = _safe_text(manifest["manifest_version"], label="manifest_version")
    manifest_ready = _safe_value(manifest.get("manifest_ready"), label="manifest_ready")
    audit_ready = _safe_value(audit_report.get("audit_ready"), label="audit_ready")
    decision_ready = _safe_value(manifest.get("decision_ready"), label="decision_ready")
    lines = [
        "# Market-aware session history manifest",
        "",
        f"- manifest_version: `{manifest_version}`",
        f"- symbol: `{_safe_value(manifest.get('symbol'), label='symbol')}`",
        f"- package_count: `{_safe_value(manifest.get('package_count'), label='package_count')}`",
        f"- first_as_of: `{_safe_value(manifest.get('first_as_of'), label='first_as_of')}`",
        f"- last_as_of: `{_safe_value(manifest.get('last_as_of'), label='last_as_of')}`",
        f"- manifest_ready: `{manifest_ready}`",
        f"- audit_ready: `{audit_ready}`",
        f"- decision_ready: `{decision_ready}`",
        "",
        "## Evidence artifacts",
        "",
        "| role | path | byte_count | sha256 |",
        "| --- | --- | ---: | --- |",
    ]
    for role in _ROLES:
        item = metadata["manifest_by_role"][role]
        lines.append(
            f"| `{_safe_text(role, label='role')}` | "
            f"`{_safe_text(item['path'], label='path')}` | "
            f"`{_safe_value(item['byte_count'], label='byte_count')}` | "
            f"`{_safe_text(item['sha256'], label='sha256')}` |"
        )
    lines.extend(["", "## Audit issues", ""])
    issues = list(manifest.get("issues") or []) + list(audit_report.get("issues") or [])
    if issues:
        for issue in issues:
            lines.append(
                f"- `{_safe_text(issue['code'], label='issue.code')}`: "
                f"{_safe_text(issue['message'], label='issue.message')}"
            )
    else:
        lines.append("- No manifest or audit issues recorded.")
    lines.extend(
        [
            "",
            "This is a read-only evidence view. It does not infer market trends, "
            "research quality, returns, investment value, or trading authorization.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _base_report() -> dict[str, Any]:
    return {
        "audit_ready": False,
        "audit_report_path": None,
        "audit_report_sha256": None,
        "decision_ready": False,
        "first_as_of": None,
        "issues": [],
        "last_as_of": None,
        "manifest_path": None,
        "manifest_report_path": None,
        "manifest_report_sha256": None,
        "manifest_ready": False,
        "manifest_sha256": None,
        "output_sha256": None,
        "package_count": 0,
        "render_ready": False,
        "render_version": MARKET_AWARE_SESSION_HISTORY_MANIFEST_RENDER_VERSION,
        "status": "invalid",
        "symbol": None,
    }


def render_market_aware_session_history_manifest(
    *,
    manifest_path: Path,
    manifest_report_path: Path,
    audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render Node41/42 evidence as deterministic Markdown."""

    report = _base_report()
    can_write_report = False
    markdown_path = output_dir / "market_aware_session_history_manifest.md"
    render_report_path = output_dir / "market_aware_session_history_manifest_render_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryManifestRenderError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryManifestRenderError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "render output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "manifest": _safe_file(manifest_path, root=root, label="manifest"),
            "manifest_report": _safe_file(
                manifest_report_path, root=root, label="manifest report"
            ),
            "audit_report": _safe_file(audit_report_path, root=root, label="audit report"),
        }
        manifest, _ = _read_json(files["manifest"]["path"], label="manifest")
        manifest_report, _ = _read_json(
            files["manifest_report"]["path"], label="manifest report"
        )
        audit_report, _ = _read_json(files["audit_report"]["path"], label="audit report")
        metadata = _validate_inputs(
            files=files,
            manifest=manifest,
            manifest_report=manifest_report,
            audit_report=audit_report,
        )
        markdown = _render_markdown(metadata)
        write_atomic(markdown_path, markdown)
        report.update(
            {
                "audit_ready": audit_report.get("audit_ready"),
                "audit_report_path": files["audit_report"]["relative_path"],
                "audit_report_sha256": files["audit_report"]["sha256"],
                "decision_ready": False,
                "first_as_of": manifest.get("first_as_of"),
                "issues": list(manifest.get("issues") or [])
                + list(audit_report.get("issues") or []),
                "last_as_of": manifest.get("last_as_of"),
                "manifest_path": files["manifest"]["relative_path"],
                "manifest_report_path": files["manifest_report"]["relative_path"],
                "manifest_report_sha256": files["manifest_report"]["sha256"],
                "manifest_ready": manifest.get("manifest_ready"),
                "manifest_sha256": files["manifest"]["sha256"],
                "output_sha256": sha256_bytes(markdown),
                "package_count": manifest.get("package_count"),
                "render_ready": True,
                "status": "ready",
                "symbol": manifest.get("symbol"),
            }
        )
    except MarketAwareSessionHistoryManifestRenderError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(render_report_path, _json_bytes(report))
    return report
