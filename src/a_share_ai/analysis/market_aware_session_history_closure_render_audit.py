"""Audit the Node46 closure Markdown and render report offline."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history_closure import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION,
)
from .market_aware_session_history_closure_renderer import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_AUDIT_VERSION = (
    "market-aware-session-history-closure-render-audit-v1"
)
_STATUSES = {"blocked", "ready", "stale"}


class MarketAwareSessionHistoryClosureRenderAuditError(ValueError):
    """A fail-closed closure-render audit error."""

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
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
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
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
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
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryClosureRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _markdown_markers(
    *, closure: Mapping[str, Any], closure_ready: bool
) -> list[str]:
    history_audit_ready = _safe_value(
        closure.get("history_audit_ready"), label="history_audit_ready"
    )
    markers = [
        "# Market-aware session history closure",
        f"- closure_version: `{_safe_text(closure['closure_version'], label='closure_version')}`",
        f"- status: `{_safe_value(closure.get('status'), label='status')}`",
        f"- symbol: `{_safe_value(closure.get('symbol'), label='symbol')}`",
        f"- package_count: `{_safe_value(closure.get('package_count'), label='package_count')}`",
        f"- first_as_of: `{_safe_value(closure.get('first_as_of'), label='first_as_of')}`",
        f"- last_as_of: `{_safe_value(closure.get('last_as_of'), label='last_as_of')}`",
        f"- closure_ready: `{_safe_value(closure_ready, label='closure_ready')}`",
        f"- history_ready: `{_safe_value(closure.get('history_ready'), label='history_ready')}`",
        f"- history_audit_ready: `{history_audit_ready}`",
        f"- render_ready: `{_safe_value(closure.get('render_ready'), label='render_ready')}`",
        f"- audit_ready: `{_safe_value(closure.get('audit_ready'), label='audit_ready')}`",
        f"- decision_ready: `{_safe_value(closure.get('decision_ready'), label='decision_ready')}`",
        "## Evidence-chain hashes",
        "| field | sha256 |",
        "| --- | --- |",
    ]
    for field in (
        "manifest_sha256",
        "manifest_audit_sha256",
        "render_report_sha256",
        "render_audit_sha256",
    ):
        markers.append(
            f"| `{_safe_text(field, label='hash field')}` | "
            f"`{_safe_text(closure[field], label=field)}` |"
        )
    markers.extend(
        [
            "## Issues",
            "This is a read-only evidence-chain view. It does not infer market "
            "trends, research quality, returns, investment value, or trading authorization.",
        ]
    )
    issues = list(closure.get("issues") or [])
    if issues:
        markers.extend(
            f"- `{_safe_text(issue['code'], label='issue.code')}`: "
            f"{_safe_text(issue['message'], label='issue.message')}"
            for issue in issues
        )
    else:
        markers.append("- No closure issues recorded.")
    return markers


def _validate_markdown(
    raw: bytes, *, closure: Mapping[str, Any], closure_ready: bool
) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "MARKDOWN_INVALID", "Markdown is not valid UTF-8"
        ) from exc
    for marker in _markdown_markers(closure=closure, closure_ready=closure_ready):
        if marker not in text:
            raise MarketAwareSessionHistoryClosureRenderAuditError(
                "MARKDOWN_MISMATCH", "Markdown does not contain the declared closure view"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    closure: Mapping[str, Any],
    closure_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
) -> dict[str, Any]:
    if closure.get("closure_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "VERSION_MISMATCH", "closure version is invalid"
        )
    if closure_report.get("closure_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "VERSION_MISMATCH", "closure report version is invalid"
        )
    if render_report.get("render_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "VERSION_MISMATCH", "closure render report version is invalid"
        )
    for label, payload in (
        ("closure", closure),
        ("closure_report", closure_report),
        ("render_report", render_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryClosureRenderAuditError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    _validate_self_hash(closure, label="closure")
    _validate_self_hash(closure_report, label="closure_report")
    _validate_self_hash(render_report, label="render_report")
    if closure_report.get("closure_sha256") != files["closure"]["sha256"]:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "HASH_MISMATCH", "closure report SHA does not match closure"
        )
    if render_report.get("closure_sha256") != files["closure"]["sha256"]:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "HASH_MISMATCH", "render report closure SHA does not match closure"
        )
    if render_report.get("closure_report_sha256") != files["closure_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "HASH_MISMATCH", "render report closure report SHA does not match report"
        )
    if render_report.get("markdown_sha256") != files["markdown"]["sha256"]:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "HASH_MISMATCH", "render report Markdown SHA does not match Markdown"
        )
    if render_report.get("closure_path") != files["closure"]["relative_path"]:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "CHAIN_MISMATCH", "render report closure path is inconsistent"
        )
    if render_report.get("closure_report_path") != files["closure_report"]["relative_path"]:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "CHAIN_MISMATCH", "render report closure report path is inconsistent"
        )
    if len({item["path"] for item in files.values()}) != len(files):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "PATH_INVALID", "audit inputs must be distinct files"
        )
    status = closure.get("status")
    if status not in _STATUSES:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "STATUS_INVALID", "closure status is invalid"
        )
    closure_ready = closure_report.get("closure_ready")
    if not isinstance(closure_ready, bool) or closure_ready is not (status == "ready"):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_MISMATCH", "closure status and report readiness differ"
        )
    if closure_report.get("status") != status:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_MISMATCH", "closure/report status differs"
        )
    if render_report.get("status") != status:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_MISMATCH", "render report status differs"
        )
    if render_report.get("closure_ready") is not closure_ready:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_MISMATCH", "render report closure readiness differs"
        )
    if render_report.get("render_ready") is not closure_ready:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_MISMATCH", "render report render readiness differs"
        )
    if closure_report.get("issues") != closure.get("issues"):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_MISMATCH", "closure report issues differ"
        )
    if render_report.get("issues") != closure.get("issues"):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_MISMATCH", "render report issues differ"
        )
    if not isinstance(closure.get("symbol"), str) or not closure.get("symbol"):
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_INVALID", "closure.symbol is required"
        )
    for field in ("symbol", "package_count", "first_as_of", "last_as_of"):
        if render_report.get(field) != closure.get(field):
            raise MarketAwareSessionHistoryClosureRenderAuditError(
                "FIELD_MISMATCH", f"render report {field} differs"
            )
    if not isinstance(closure.get("package_count"), int) or closure.get("package_count") < 0:
        raise MarketAwareSessionHistoryClosureRenderAuditError(
            "FIELD_INVALID", "closure.package_count is invalid"
        )
    _parse_datetime(closure.get("first_as_of"), label="closure.first_as_of")
    _parse_datetime(closure.get("last_as_of"), label="closure.last_as_of")
    _validate_markdown(
        files["markdown"]["raw"], closure=closure, closure_ready=closure_ready
    )
    return {
        "closure_ready": closure_ready,
        "closure_status": status,
        "first_as_of": closure.get("first_as_of"),
        "last_as_of": closure.get("last_as_of"),
        "package_count": closure.get("package_count"),
        "render_ready": render_report.get("render_ready"),
        "symbol": closure.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_AUDIT_VERSION,
        "closure_path": None,
        "closure_ready": False,
        "closure_report_path": None,
        "closure_report_sha256": None,
        "closure_sha256": None,
        "closure_status": "invalid",
        "decision_ready": False,
        "first_as_of": None,
        "issues": [],
        "last_as_of": None,
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
    report["output_sha256"] = sha256_bytes(_json_bytes(payload))
    return _json_bytes(report)


def audit_market_aware_session_history_closure_render(
    *,
    closure_path: Path,
    closure_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit Node46 closure Markdown and render report without rerendering."""

    report = _base_report()
    can_write_report = False
    audit_report_path = (
        output_dir / "market_aware_session_history_closure_render_audit_report.json"
    )
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryClosureRenderAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureRenderAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "closure": _safe_file(closure_path, root=root, label="closure"),
            "closure_report": _safe_file(
                closure_report_path, root=root, label="closure report"
            ),
            "markdown": _safe_file(markdown_path, root=root, label="Markdown"),
            "render_report": _safe_file(
                render_report_path, root=root, label="render report"
            ),
        }
        closure = _read_json(files["closure"]["raw"], label="closure")
        closure_report = _read_json(
            files["closure_report"]["raw"], label="closure report"
        )
        render_report = _read_json(
            files["render_report"]["raw"], label="render report"
        )
        summary = _validate_chain(
            files=files,
            closure=closure,
            closure_report=closure_report,
            render_report=render_report,
        )
        report.update(
            {
                **summary,
                "audit_ready": True,
                "closure_path": files["closure"]["relative_path"],
                "closure_report_path": files["closure_report"]["relative_path"],
                "closure_report_sha256": files["closure_report"]["sha256"],
                "closure_sha256": files["closure"]["sha256"],
                "markdown_path": files["markdown"]["relative_path"],
                "markdown_sha256": files["markdown"]["sha256"],
                "render_report_path": files["render_report"]["relative_path"],
                "render_report_sha256": files["render_report"]["sha256"],
                "status": summary["closure_status"],
            }
        )
    except MarketAwareSessionHistoryClosureRenderAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
