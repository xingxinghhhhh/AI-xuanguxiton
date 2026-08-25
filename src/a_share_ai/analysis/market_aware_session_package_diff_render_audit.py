"""Independently audit the artifacts emitted by the Node35 diff renderer."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_package_diff import MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION
from .market_aware_session_package_diff_renderer import (
    MARKET_AWARE_SESSION_PACKAGE_DIFF_RENDER_VERSION,
)

MARKET_AWARE_SESSION_PACKAGE_DIFF_RENDER_AUDIT_VERSION = (
    "market-aware-session-package-diff-render-audit-v1"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLES = ("diff", "diff_report", "markdown", "render_report")


class MarketAwareSessionPackageDiffRenderAuditError(ValueError):
    """A fail-closed diff render audit error."""

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
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionPackageDiffRenderAuditError(
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
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "HASH_INVALID", f"{label} must be a SHA-256 string"
        )
    return value


def _parse_datetime(value: Any, *, label: str, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _relative_declared_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "PATH_INVALID", f"{label} must be relative"
        )
    if any(part == ".." for part in Path(value).parts):
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "PATH_INVALID", f"{label} must stay within artifact root"
        )
    return value.replace("\\", "/")


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    diff: Mapping[str, Any],
    diff_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
) -> dict[str, Any]:
    if diff.get("diff_version") != MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "VERSION_MISMATCH", "diff version is invalid"
        )
    if diff_report.get("diff_version") != MARKET_AWARE_SESSION_PACKAGE_DIFF_VERSION:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "VERSION_MISMATCH", "diff report version is invalid"
        )
    if render_report.get("render_version") != MARKET_AWARE_SESSION_PACKAGE_DIFF_RENDER_VERSION:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "VERSION_MISMATCH", "render report version is invalid"
        )
    for label, payload in (
        ("diff", diff),
        ("diff_report", diff_report),
        ("render_report", render_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
    if diff_report.get("output_sha256") != files["diff"]["sha256"]:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "HASH_MISMATCH", "diff report output SHA does not match diff"
        )
    if render_report.get("diff_sha256") != files["diff"]["sha256"]:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "HASH_MISMATCH", "render report diff SHA does not match diff"
        )
    if render_report.get("diff_report_sha256") != files["diff_report"]["sha256"]:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "HASH_MISMATCH", "render report diff report SHA does not match report"
        )
    if render_report.get("output_sha256") != files["markdown"]["sha256"]:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "HASH_MISMATCH", "render report output SHA does not match Markdown"
        )
    _validate_sha(render_report.get("diff_sha256"), label="render_report.diff_sha256")
    _validate_sha(
        render_report.get("diff_report_sha256"), label="render_report.diff_report_sha256"
    )
    _validate_sha(render_report.get("output_sha256"), label="render_report.output_sha256")
    _validate_issues(diff.get("issues"), label="diff.issues")
    _validate_issues(diff_report.get("issues"), label="diff_report.issues")
    _validate_issues(render_report.get("issues"), label="render_report.issues")
    if diff_report.get("issues") != diff.get("issues"):
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "FIELD_MISMATCH", "diff/report issues differ"
        )
    if render_report.get("comparison_ready") != diff.get("comparison_ready"):
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "FIELD_MISMATCH", "render report comparison_ready differs"
        )
    if render_report.get("render_ready") is not (
        diff.get("comparison_ready") is True
    ):
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "FIELD_MISMATCH", "render report render_ready differs"
        )
    for field in ("symbol", "previous_as_of", "current_as_of"):
        if render_report.get(field) != diff.get(field):
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "FIELD_MISMATCH", f"render report {field} differs from diff"
            )
    if diff.get("comparison_ready") is True:
        if not isinstance(diff.get("symbol"), str) or not diff.get("symbol").strip():
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "FIELD_INVALID", "diff symbol is invalid"
            )
        _parse_datetime(diff.get("previous_as_of"), label="previous_as_of")
        _parse_datetime(diff.get("current_as_of"), label="current_as_of")
    else:
        _parse_datetime(diff.get("previous_as_of"), label="previous_as_of", allow_none=True)
        _parse_datetime(diff.get("current_as_of"), label="current_as_of", allow_none=True)
    expected_markdown_path = Path(files["diff"]["relative_path"]).with_name(
        "market_aware_session_package_diff.md"
    ).as_posix()
    declared_paths = {
        "diff": _relative_declared_path(render_report.get("diff_path"), label="diff_path"),
        "diff_report": _relative_declared_path(
            render_report.get("diff_report_path"), label="diff_report_path"
        ),
        "markdown": expected_markdown_path,
    }
    if declared_paths["diff"] != files["diff"]["relative_path"]:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "CHAIN_MISMATCH", "render report diff_path is inconsistent"
        )
    if declared_paths["diff_report"] != files["diff_report"]["relative_path"]:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "CHAIN_MISMATCH", "render report diff_report_path is inconsistent"
        )
    if files["render_report"]["path"].parent / "market_aware_session_package_diff.md" != files[
        "markdown"
    ]["path"]:
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "CHAIN_MISMATCH", "Markdown must be next to diff JSON"
        )
    if len({files[role]["path"] for role in _ROLES}) != len(_ROLES):
        raise MarketAwareSessionPackageDiffRenderAuditError(
            "PATH_INVALID", "audit inputs must be distinct files"
        )
    return {
        "as_of": diff.get("current_as_of"),
        "comparison_ready": diff.get("comparison_ready"),
        "current_as_of": diff.get("current_as_of"),
        "previous_as_of": diff.get("previous_as_of"),
        "symbol": diff.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_PACKAGE_DIFF_RENDER_AUDIT_VERSION,
        "comparison_ready": False,
        "current_as_of": None,
        "decision_ready": False,
        "diff_path": None,
        "diff_report_path": None,
        "diff_report_sha256": None,
        "diff_report_size_bytes": 0,
        "diff_sha256": None,
        "diff_size_bytes": 0,
        "issues": [],
        "markdown_path": None,
        "markdown_sha256": None,
        "markdown_size_bytes": 0,
        "output_sha256": None,
        "previous_as_of": None,
        "render_ready": False,
        "render_report_path": None,
        "render_report_sha256": None,
        "render_report_size_bytes": 0,
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    raw = _json_bytes(payload)
    report["output_sha256"] = sha256_bytes(raw)
    return _json_bytes(report)


def audit_market_aware_session_package_diff_render(
    *,
    diff_path: Path,
    diff_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit Node35 diff-render artifacts without recreating them."""

    report = _base_report()
    can_write_report = False
    audit_report_path = output_dir / "market_aware_session_package_diff_render_audit_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        output_resolved = output_dir.resolve()
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "diff": _safe_file(diff_path, root=root, label="diff"),
            "diff_report": _safe_file(diff_report_path, root=root, label="diff_report"),
            "markdown": _safe_file(markdown_path, root=root, label="Markdown"),
            "render_report": _safe_file(render_report_path, root=root, label="render_report"),
        }
        diff = _read_json(files["diff"]["raw"], label="diff")
        diff_report = _read_json(files["diff_report"]["raw"], label="diff_report")
        render_report = _read_json(files["render_report"]["raw"], label="render_report")
        try:
            files["markdown"]["raw"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketAwareSessionPackageDiffRenderAuditError(
                "MARKDOWN_INVALID", "Markdown is not valid UTF-8"
            ) from exc
        summary = _validate_chain(
            files=files,
            diff=diff,
            diff_report=diff_report,
            render_report=render_report,
        )
        report.update(
            {
                **summary,
                "audit_ready": True,
                "diff_path": files["diff"]["relative_path"],
                "diff_report_path": files["diff_report"]["relative_path"],
                "diff_report_sha256": files["diff_report"]["sha256"],
                "diff_report_size_bytes": files["diff_report"]["size_bytes"],
                "diff_sha256": files["diff"]["sha256"],
                "diff_size_bytes": files["diff"]["size_bytes"],
                "markdown_path": files["markdown"]["relative_path"],
                "markdown_sha256": files["markdown"]["sha256"],
                "markdown_size_bytes": files["markdown"]["size_bytes"],
                "render_ready": render_report.get("render_ready"),
                "render_report_path": files["render_report"]["relative_path"],
                "render_report_sha256": files["render_report"]["sha256"],
                "render_report_size_bytes": files["render_report"]["size_bytes"],
            }
        )
    except MarketAwareSessionPackageDiffRenderAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
