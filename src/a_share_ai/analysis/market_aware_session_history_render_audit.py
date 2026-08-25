"""Independently audit the Node39 market-aware session history render."""

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
from .market_aware_session_history_renderer import (
    MARKET_AWARE_SESSION_HISTORY_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_RENDER_AUDIT_VERSION = (
    "market-aware-session-history-render-audit-v1"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLES = ("history", "history_report", "history_audit_report", "markdown", "render_report")


class MarketAwareSessionHistoryRenderAuditError(ValueError):
    """A fail-closed rendered-history audit error."""

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
        raise MarketAwareSessionHistoryRenderAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryRenderAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryRenderAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryRenderAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryRenderAuditError(
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
        raise MarketAwareSessionHistoryRenderAuditError(
            "HASH_INVALID", f"{label} must be a SHA-256 string"
        )
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _validate_sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryRenderAuditError(
            "HASH_MISMATCH", f"{label}.output_sha256 does not match canonical report"
        )


def _parse_datetime(value: Any, *, label: str, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryRenderAuditError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryRenderAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryRenderAuditError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _relative_declared_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryRenderAuditError(
            "PATH_INVALID", f"{label} must be relative"
        )
    if any(part == ".." for part in Path(value).parts):
        raise MarketAwareSessionHistoryRenderAuditError(
            "PATH_INVALID", f"{label} must stay within artifact root"
        )
    return value.replace("\\", "/")


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryRenderAuditError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_packages(history: Mapping[str, Any]) -> None:
    packages = history.get("packages")
    if not isinstance(packages, list) or not packages:
        raise MarketAwareSessionHistoryRenderAuditError(
            "PACKAGE_INVALID", "history packages are required"
        )
    if history.get("package_count") != len(packages):
        raise MarketAwareSessionHistoryRenderAuditError(
            "FIELD_MISMATCH", "history package_count differs from packages"
        )
    previous: datetime | None = None
    for index, package in enumerate(packages):
        if not isinstance(package, Mapping):
            raise MarketAwareSessionHistoryRenderAuditError(
                "PACKAGE_INVALID", f"history.packages[{index}] is invalid"
            )
        as_of = package.get("as_of")
        _parse_datetime(as_of, label=f"history.packages[{index}].as_of")
        current = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if previous is not None and current <= previous:
            raise MarketAwareSessionHistoryRenderAuditError(
                "AS_OF_ORDER_INVALID", "history package as_of values are not increasing"
            )
        previous = current
        _parse_datetime(
            package.get("evaluation_at"), label=f"history.packages[{index}].evaluation_at"
        )
        _parse_datetime(
            package.get("reference_at"), label=f"history.packages[{index}].reference_at"
        )
        for field in ("manifest_path", "report_path", "artifact_root"):
            _relative_declared_path(
                package.get(field), label=f"history.packages[{index}].{field}"
            )
    if history.get("first_as_of") != packages[0].get("as_of"):
        raise MarketAwareSessionHistoryRenderAuditError(
            "FIELD_MISMATCH", "history first_as_of differs from first package"
        )
    if history.get("last_as_of") != packages[-1].get("as_of"):
        raise MarketAwareSessionHistoryRenderAuditError(
            "FIELD_MISMATCH", "history last_as_of differs from last package"
        )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    history: Mapping[str, Any],
    history_report: Mapping[str, Any],
    history_audit_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
) -> dict[str, Any]:
    if history.get("history_version") != MARKET_AWARE_SESSION_HISTORY_VERSION:
        raise MarketAwareSessionHistoryRenderAuditError(
            "VERSION_MISMATCH", "history version is invalid"
        )
    if history_report.get("history_version") != MARKET_AWARE_SESSION_HISTORY_VERSION:
        raise MarketAwareSessionHistoryRenderAuditError(
            "VERSION_MISMATCH", "history report version is invalid"
        )
    if history_audit_report.get("audit_version") != MARKET_AWARE_SESSION_HISTORY_AUDIT_VERSION:
        raise MarketAwareSessionHistoryRenderAuditError(
            "VERSION_MISMATCH", "history audit report version is invalid"
        )
    if render_report.get("render_version") != MARKET_AWARE_SESSION_HISTORY_RENDER_VERSION:
        raise MarketAwareSessionHistoryRenderAuditError(
            "VERSION_MISMATCH", "render report version is invalid"
        )
    for label, payload in (
        ("history", history),
        ("history_report", history_report),
        ("history_audit_report", history_audit_report),
        ("render_report", render_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryRenderAuditError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    if history.get("issues") != history_report.get("issues"):
        raise MarketAwareSessionHistoryRenderAuditError(
            "FIELD_MISMATCH", "history/report issues differ"
        )
    _validate_self_hash(history_report, label="history_report")
    _validate_self_hash(history_audit_report, label="history_audit_report")

    for source, role, field in (
        (history_audit_report, "history", "history_sha256"),
        (history_audit_report, "history_report", "history_report_sha256"),
        (render_report, "history", "history_sha256"),
        (render_report, "history_report", "history_report_sha256"),
        (render_report, "markdown", "output_sha256"),
    ):
        declared = _validate_sha(source.get(field), label=f"{field}")
        if declared != files[role]["sha256"]:
            raise MarketAwareSessionHistoryRenderAuditError(
                "HASH_MISMATCH", f"{field} does not match {role}"
            )
    for source, role, field in (
        (history_audit_report, "history", "history_size_bytes"),
        (history_audit_report, "history_report", "history_report_size_bytes"),
        (render_report, "history", "history_size_bytes"),
        (render_report, "history_report", "history_report_size_bytes"),
    ):
        if source.get(field) != files[role]["size_bytes"]:
            raise MarketAwareSessionHistoryRenderAuditError(
                "SIZE_MISMATCH", f"{field} does not match {role}"
            )

    for source, label, field, role in (
        (history_audit_report, "history audit report", "history_path", "history"),
        (history_audit_report, "history audit report", "history_report_path", "history_report"),
        (render_report, "render report", "history_path", "history"),
        (render_report, "render report", "history_report_path", "history_report"),
    ):
        declared = _relative_declared_path(source.get(field), label=f"{label}.{field}")
        if declared != files[role]["relative_path"]:
            raise MarketAwareSessionHistoryRenderAuditError(
                "CHAIN_MISMATCH", f"{label} {field} is inconsistent"
            )

    if files["render_report"]["path"].parent / "market_aware_session_history.md" != files[
        "markdown"
    ]["path"]:
        raise MarketAwareSessionHistoryRenderAuditError(
            "CHAIN_MISMATCH", "Markdown must be next to render report"
        )
    if len({files[role]["path"] for role in _ROLES}) != len(_ROLES):
        raise MarketAwareSessionHistoryRenderAuditError(
            "PATH_INVALID", "audit inputs must be distinct files"
        )

    _validate_packages(history)
    for field in (
        "history_ready",
        "first_as_of",
        "last_as_of",
        "package_count",
        "symbol",
    ):
        if history.get(field) != history_report.get(field):
            raise MarketAwareSessionHistoryRenderAuditError(
                "FIELD_MISMATCH", f"history/report.{field} differs"
            )
        if render_report.get(field) != history.get(field):
            raise MarketAwareSessionHistoryRenderAuditError(
                "FIELD_MISMATCH", f"render report {field} differs"
            )
    if history_audit_report.get("audit_ready") is True:
        for field in ("first_as_of", "last_as_of", "package_count", "symbol"):
            if history_audit_report.get(field) != history.get(field):
                raise MarketAwareSessionHistoryRenderAuditError(
                    "FIELD_MISMATCH", f"history audit report {field} differs"
                )
        if history_audit_report.get("packages") != history.get("packages"):
            raise MarketAwareSessionHistoryRenderAuditError(
                "FIELD_MISMATCH", "history audit packages differ"
            )
    if render_report.get("history_audit_ready") is not (
        history_audit_report.get("audit_ready") is True
    ):
        raise MarketAwareSessionHistoryRenderAuditError(
            "FIELD_MISMATCH", "render report history_audit_ready differs"
        )
    if render_report.get("render_ready") is not (
        history.get("history_ready") is True
        and history_audit_report.get("audit_ready") is True
    ):
        raise MarketAwareSessionHistoryRenderAuditError(
            "FIELD_MISMATCH", "render report render_ready differs"
        )
    expected_issues = list(history.get("issues") or []) + list(
        history_audit_report.get("issues") or []
    )
    if render_report.get("issues") != expected_issues:
        raise MarketAwareSessionHistoryRenderAuditError(
            "FIELD_MISMATCH", "render report issues differ from history chain"
        )
    return {
        "first_as_of": history.get("first_as_of"),
        "history_audit_ready": history_audit_report.get("audit_ready"),
        "history_ready": history.get("history_ready"),
        "last_as_of": history.get("last_as_of"),
        "package_count": history.get("package_count"),
        "render_ready": render_report.get("render_ready"),
        "symbol": history.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_HISTORY_RENDER_AUDIT_VERSION,
        "decision_ready": False,
        "first_as_of": None,
        "history_audit_report_path": None,
        "history_audit_report_sha256": None,
        "history_audit_report_size_bytes": 0,
        "history_audit_ready": False,
        "history_path": None,
        "history_report_path": None,
        "history_report_sha256": None,
        "history_report_size_bytes": 0,
        "history_ready": False,
        "history_sha256": None,
        "history_size_bytes": 0,
        "issues": [],
        "last_as_of": None,
        "markdown_path": None,
        "markdown_sha256": None,
        "markdown_size_bytes": 0,
        "output_sha256": None,
        "package_count": 0,
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


def audit_market_aware_session_history_render(
    *,
    history_path: Path,
    history_report_path: Path,
    history_audit_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit Node39 rendered history artifacts without recreating them."""

    report = _base_report()
    can_write_report = False
    audit_report_path = (
        output_dir / "market_aware_session_history_render_audit_report.json"
    )
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryRenderAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        output_resolved = output_dir.resolve()
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryRenderAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
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
            "markdown": _safe_file(markdown_path, root=root, label="Markdown"),
            "render_report": _safe_file(
                render_report_path, root=root, label="render report"
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
            files["render_report"]["raw"], label="render report"
        )
        try:
            files["markdown"]["raw"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketAwareSessionHistoryRenderAuditError(
                "MARKDOWN_INVALID", "Markdown is not valid UTF-8"
            ) from exc
        summary = _validate_chain(
            files=files,
            history=history,
            history_report=history_report,
            history_audit_report=history_audit_report,
            render_report=render_report,
        )
        report.update(
            {
                **summary,
                "audit_ready": True,
                "history_audit_report_path": files["history_audit_report"]["relative_path"],
                "history_audit_report_sha256": files["history_audit_report"]["sha256"],
                "history_audit_report_size_bytes": files["history_audit_report"]["size_bytes"],
                "history_path": files["history"]["relative_path"],
                "history_report_path": files["history_report"]["relative_path"],
                "history_report_sha256": files["history_report"]["sha256"],
                "history_report_size_bytes": files["history_report"]["size_bytes"],
                "history_sha256": files["history"]["sha256"],
                "history_size_bytes": files["history"]["size_bytes"],
                "markdown_path": files["markdown"]["relative_path"],
                "markdown_sha256": files["markdown"]["sha256"],
                "markdown_size_bytes": files["markdown"]["size_bytes"],
                "render_report_path": files["render_report"]["relative_path"],
                "render_report_sha256": files["render_report"]["sha256"],
                "render_report_size_bytes": files["render_report"]["size_bytes"],
            }
        )
    except MarketAwareSessionHistoryRenderAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
