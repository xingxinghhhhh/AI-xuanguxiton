"""Build an immutable evidence manifest for the market-aware history chain."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history import MARKET_AWARE_SESSION_HISTORY_VERSION
from .market_aware_session_history_audit import (
    MARKET_AWARE_SESSION_HISTORY_AUDIT_VERSION,
)
from .market_aware_session_history_render_audit import (
    MARKET_AWARE_SESSION_HISTORY_RENDER_AUDIT_VERSION,
)
from .market_aware_session_history_renderer import (
    MARKET_AWARE_SESSION_HISTORY_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION = (
    "market-aware-session-history-manifest-v1"
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


class MarketAwareSessionHistoryManifestError(ValueError):
    """A fail-closed history manifest error."""

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
        raise MarketAwareSessionHistoryManifestError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryManifestError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryManifestError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryManifestError(
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
        raise MarketAwareSessionHistoryManifestError(
            "HASH_INVALID", f"{label} must be a SHA-256 string"
        )
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _validate_sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryManifestError(
            "HASH_MISMATCH", f"{label}.output_sha256 does not match canonical report"
        )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryManifestError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryManifestError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryManifestError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryManifestError(
            "PATH_INVALID", f"{label} must be relative"
        )
    if any(part == ".." for part in Path(value).parts):
        raise MarketAwareSessionHistoryManifestError(
            "PATH_INVALID", f"{label} must stay within artifact root"
        )
    return value.replace("\\", "/")


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryManifestError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryManifestError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryManifestError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    history: Mapping[str, Any],
    history_report: Mapping[str, Any],
    history_audit_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    render_audit_report: Mapping[str, Any],
) -> dict[str, Any]:
    versions = (
        (history, "history_version", MARKET_AWARE_SESSION_HISTORY_VERSION, "history"),
        (
            history_report,
            "history_version",
            MARKET_AWARE_SESSION_HISTORY_VERSION,
            "history report",
        ),
        (
            history_audit_report,
            "audit_version",
            MARKET_AWARE_SESSION_HISTORY_AUDIT_VERSION,
            "history audit report",
        ),
        (
            render_report,
            "render_version",
            MARKET_AWARE_SESSION_HISTORY_RENDER_VERSION,
            "render report",
        ),
        (
            render_audit_report,
            "audit_version",
            MARKET_AWARE_SESSION_HISTORY_RENDER_AUDIT_VERSION,
            "render audit report",
        ),
    )
    for payload, field, expected, label in versions:
        if payload.get(field) != expected:
            raise MarketAwareSessionHistoryManifestError(
                "VERSION_MISMATCH", f"{label} version is invalid"
            )
    for label, payload in (
        ("history", history),
        ("history_report", history_report),
        ("history_audit_report", history_audit_report),
        ("render_report", render_report),
        ("render_audit_report", render_audit_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryManifestError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    _validate_self_hash(history_report, label="history_report")
    _validate_self_hash(history_audit_report, label="history_audit_report")
    _validate_self_hash(render_audit_report, label="render_audit_report")
    if render_audit_report.get("audit_ready") is not True:
        raise MarketAwareSessionHistoryManifestError(
            "UPSTREAM_NOT_READY", "render audit report is not audit_ready"
        )

    bindings = (
        (history_audit_report, "history", "history_sha256", "history_size_bytes"),
        (
            history_audit_report,
            "history_report",
            "history_report_sha256",
            "history_report_size_bytes",
        ),
        (render_report, "history", "history_sha256", "history_size_bytes"),
        (render_report, "history_report", "history_report_sha256", "history_report_size_bytes"),
        (render_report, "history_markdown", "output_sha256", None),
        (render_audit_report, "history", "history_sha256", "history_size_bytes"),
        (
            render_audit_report,
            "history_report",
            "history_report_sha256",
            "history_report_size_bytes",
        ),
        (
            render_audit_report,
            "history_audit_report",
            "history_audit_report_sha256",
            "history_audit_report_size_bytes",
        ),
        (render_audit_report, "history_markdown", "markdown_sha256", "markdown_size_bytes"),
        (
            render_audit_report,
            "history_render_report",
            "render_report_sha256",
            "render_report_size_bytes",
        ),
    )
    for source, role, sha_field, size_field in bindings:
        if _validate_sha(source.get(sha_field), label=f"{role}.{sha_field}") != files[role][
            "sha256"
        ]:
            raise MarketAwareSessionHistoryManifestError(
                "HASH_MISMATCH", f"{role} SHA binding is invalid"
            )
        if size_field is not None and source.get(size_field) != files[role]["size_bytes"]:
            raise MarketAwareSessionHistoryManifestError(
                "SIZE_MISMATCH", f"{role} byte count binding is invalid"
            )

    path_bindings = (
        (history_audit_report, "history", "history_path"),
        (history_audit_report, "history_report", "history_report_path"),
        (render_report, "history", "history_path"),
        (render_report, "history_report", "history_report_path"),
        (render_audit_report, "history", "history_path"),
        (render_audit_report, "history_report", "history_report_path"),
        (render_audit_report, "history_audit_report", "history_audit_report_path"),
        (render_audit_report, "history_markdown", "markdown_path"),
        (render_audit_report, "history_render_report", "render_report_path"),
    )
    for source, role, field in path_bindings:
        if _relative_path(source.get(field), label=field) != files[role]["relative_path"]:
            raise MarketAwareSessionHistoryManifestError(
                "CHAIN_MISMATCH", f"{field} binding is invalid"
            )
    if len({files[role]["path"] for role in _ROLES}) != len(_ROLES):
        raise MarketAwareSessionHistoryManifestError(
            "PATH_INVALID", "manifest inputs must be distinct files"
        )
    if history.get("issues") != history_report.get("issues"):
        raise MarketAwareSessionHistoryManifestError(
            "FIELD_MISMATCH", "history/report issues differ"
        )
    for field in ("history_ready", "first_as_of", "last_as_of", "package_count", "symbol"):
        for payload, label in (
            (history_report, "history report"),
            (render_report, "render report"),
            (render_audit_report, "render audit report"),
        ):
            if payload.get(field) != history.get(field):
                raise MarketAwareSessionHistoryManifestError(
                    "FIELD_MISMATCH", f"{label} {field} differs"
                )
    for field in ("first_as_of", "last_as_of", "package_count", "symbol"):
        if history_audit_report.get(field) != history.get(field):
            raise MarketAwareSessionHistoryManifestError(
                "FIELD_MISMATCH", f"history audit report {field} differs"
            )
    for label, payload in (
        ("history", history),
        ("history audit report", history_audit_report),
        ("render report", render_report),
        ("render audit report", render_audit_report),
    ):
        for field in ("first_as_of", "last_as_of"):
            _parse_datetime(payload.get(field), label=f"{label}.{field}")
    if render_report.get("history_audit_ready") is not (
        history_audit_report.get("audit_ready") is True
    ):
        raise MarketAwareSessionHistoryManifestError(
            "FIELD_MISMATCH", "render history_audit_ready differs"
        )
    if render_report.get("render_ready") is not (
        history.get("history_ready") is True
        and history_audit_report.get("audit_ready") is True
    ):
        raise MarketAwareSessionHistoryManifestError(
            "FIELD_MISMATCH", "render_ready differs from upstream state"
        )
    if render_audit_report.get("history_audit_ready") is not (
        history_audit_report.get("audit_ready") is True
    ) or render_audit_report.get("render_ready") is not render_report.get("render_ready"):
        raise MarketAwareSessionHistoryManifestError(
            "FIELD_MISMATCH", "render audit readiness differs"
        )
    return {
        "first_as_of": history.get("first_as_of"),
        "history_audit_ready": history_audit_report.get("audit_ready") is True,
        "history_ready": history.get("history_ready") is True,
        "last_as_of": history.get("last_as_of"),
        "package_count": history.get("package_count"),
        "render_ready": render_report.get("render_ready") is True,
        "render_audit_ready": render_audit_report.get("audit_ready") is True,
        "symbol": history.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "artifact_root": ".",
        "decision_ready": False,
        "issues": [],
        "manifest_path": None,
        "manifest_ready": False,
        "manifest_sha256": None,
        "manifest_version": MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION,
        "output_sha256": None,
        "status": "invalid",
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def build_market_aware_session_history_manifest(
    *,
    history_path: Path,
    history_report_path: Path,
    history_audit_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    render_audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a literal, immutable evidence manifest for Node37-40 outputs."""

    report = _base_report()
    can_write_report = False
    manifest_path = output_dir / "market_aware_session_history_manifest.json"
    report_path = output_dir / "market_aware_session_history_manifest_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryManifestError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryManifestError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "manifest output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "history": _safe_file(history_path, root=root, label="history"),
            "history_report": _safe_file(
                history_report_path, root=root, label="history report"
            ),
            "history_audit_report": _safe_file(
                history_audit_report_path, root=root, label="history audit report"
            ),
            "history_markdown": _safe_file(markdown_path, root=root, label="Markdown"),
            "history_render_report": _safe_file(
                render_report_path, root=root, label="render report"
            ),
            "history_render_audit_report": _safe_file(
                render_audit_report_path, root=root, label="render audit report"
            ),
        }
        history = _read_json(files["history"]["raw"], label="history")
        history_report = _read_json(
            files["history_report"]["raw"], label="history report"
        )
        history_audit_report = _read_json(
            files["history_audit_report"]["raw"], label="history audit report"
        )
        render_report = _read_json(
            files["history_render_report"]["raw"], label="render report"
        )
        render_audit_report = _read_json(
            files["history_render_audit_report"]["raw"], label="render audit report"
        )
        try:
            files["history_markdown"]["raw"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketAwareSessionHistoryManifestError(
                "MARKDOWN_INVALID", "Markdown is not valid UTF-8"
            ) from exc
        summary = _validate_chain(
            files=files,
            history=history,
            history_report=history_report,
            history_audit_report=history_audit_report,
            render_report=render_report,
            render_audit_report=render_audit_report,
        )
        artifacts = [
            {
                "byte_count": files[role]["size_bytes"],
                "path": files[role]["relative_path"],
                "role": role,
                "sha256": files[role]["sha256"],
            }
            for role in _ROLES
        ]
        manifest = {
            "artifacts": artifacts,
            "decision_ready": False,
            **summary,
            "issues": [],
            "manifest_ready": True,
            "manifest_version": MARKET_AWARE_SESSION_HISTORY_MANIFEST_VERSION,
        }
        manifest_raw = _json_bytes(manifest)
        write_atomic(manifest_path, manifest_raw)
        report.update(
            {
                "issues": [],
                "manifest_path": manifest_path.resolve().relative_to(root).as_posix(),
                "manifest_ready": True,
                "manifest_sha256": sha256_bytes(manifest_raw),
                "status": "ready",
            }
        )
    except MarketAwareSessionHistoryManifestError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
