"""Render a market-aware session history as safe deterministic Markdown."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history import MARKET_AWARE_SESSION_HISTORY_VERSION
from .market_aware_session_history_audit import (
    MARKET_AWARE_SESSION_HISTORY_AUDIT_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_RENDER_VERSION = "market-aware-session-history-render-v1"


class MarketAwareSessionHistoryRenderError(ValueError):
    """A fail-closed history renderer error."""

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
        raise MarketAwareSessionHistoryRenderError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryRenderError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload, raw


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryRenderError(
            "PATH_OUTSIDE_HISTORY_ROOT", f"{label} is outside history root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryRenderError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryRenderError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative,
        "raw": raw,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise MarketAwareSessionHistoryRenderError(
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


def _parse_datetime(value: Any, *, label: str, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryRenderError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryRenderError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryRenderError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryRenderError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, issue in enumerate(value):
        if not isinstance(issue, Mapping):
            raise MarketAwareSessionHistoryRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(issue.get("code"), str) or not isinstance(issue.get("message"), str):
            raise MarketAwareSessionHistoryRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_inputs(
    *,
    history_path: Path,
    history_report_path: Path,
    history_audit_report_path: Path,
    root: Path,
) -> dict[str, Any]:
    files = {
        "history": _safe_file(history_path, root=root, label="history"),
        "history_report": _safe_file(
            history_report_path, root=root, label="history report"
        ),
        "history_audit_report": _safe_file(
            history_audit_report_path, root=root, label="history audit report"
        ),
    }
    history, _ = _read_json(files["history"]["path"], label="history")
    history_report, _ = _read_json(
        files["history_report"]["path"], label="history report"
    )
    audit_report, _ = _read_json(
        files["history_audit_report"]["path"], label="history audit report"
    )
    if history.get("history_version") != MARKET_AWARE_SESSION_HISTORY_VERSION:
        raise MarketAwareSessionHistoryRenderError(
            "VERSION_MISMATCH", "history version is invalid"
        )
    if history_report.get("history_version") != MARKET_AWARE_SESSION_HISTORY_VERSION:
        raise MarketAwareSessionHistoryRenderError(
            "VERSION_MISMATCH", "history report version is invalid"
        )
    if audit_report.get("audit_version") != MARKET_AWARE_SESSION_HISTORY_AUDIT_VERSION:
        raise MarketAwareSessionHistoryRenderError(
            "VERSION_MISMATCH", "history audit report version is invalid"
        )
    for label, payload in (
        ("history", history),
        ("history_report", history_report),
        ("history_audit_report", audit_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryRenderError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
    _validate_issues(history.get("issues"), label="history.issues")
    _validate_issues(history_report.get("issues"), label="history_report.issues")
    _validate_issues(audit_report.get("issues"), label="history_audit_report.issues")
    if history.get("issues") != history_report.get("issues"):
        raise MarketAwareSessionHistoryRenderError(
            "FIELD_MISMATCH", "history/report issues differ"
        )
    if audit_report.get("history_sha256") != files["history"]["sha256"]:
        raise MarketAwareSessionHistoryRenderError(
            "HASH_MISMATCH", "audit report history SHA does not match history"
        )
    if audit_report.get("history_report_sha256") != files["history_report"]["sha256"]:
        raise MarketAwareSessionHistoryRenderError(
            "HASH_MISMATCH", "audit report history report SHA does not match report"
        )
    for field in (
        "history_ready",
        "first_as_of",
        "last_as_of",
        "package_count",
        "symbol",
    ):
        if history.get(field) != history_report.get(field):
            raise MarketAwareSessionHistoryRenderError(
                "FIELD_MISMATCH", f"history/report.{field} differs"
            )
    if audit_report.get("audit_ready") is True:
        if audit_report.get("package_count") != history.get("package_count"):
            raise MarketAwareSessionHistoryRenderError(
                "FIELD_MISMATCH", "audit package_count differs from history"
            )
        if audit_report.get("symbol") != history.get("symbol"):
            raise MarketAwareSessionHistoryRenderError(
                "FIELD_MISMATCH", "audit symbol differs from history"
            )
    for label, field in (
        ("history_path", "history"),
        ("history_report_path", "history_report"),
    ):
        value = audit_report.get(label)
        if not isinstance(value, str) or value != files[field]["relative_path"]:
            raise MarketAwareSessionHistoryRenderError(
                "CHAIN_MISMATCH", f"audit report {label} is inconsistent"
            )
    packages = history.get("packages")
    if not isinstance(packages, list) or not packages:
        raise MarketAwareSessionHistoryRenderError(
            "PACKAGE_INVALID", "history packages are required"
        )
    for index, entry in enumerate(packages):
        if not isinstance(entry, Mapping):
            raise MarketAwareSessionHistoryRenderError(
                "PACKAGE_INVALID", f"history.packages[{index}] is invalid"
            )
        _parse_datetime(entry.get("as_of"), label=f"packages[{index}].as_of")
        _parse_datetime(entry.get("evaluation_at"), label=f"packages[{index}].evaluation_at")
        _parse_datetime(entry.get("reference_at"), label=f"packages[{index}].reference_at")
        for field in ("manifest_path", "report_path", "artifact_root"):
            value = entry.get(field)
            if not isinstance(value, str) or Path(value).is_absolute() or any(
                part == ".." for part in Path(value).parts
            ):
                raise MarketAwareSessionHistoryRenderError(
                    "PATH_INVALID", f"packages[{index}].{field} is invalid"
                )
    return {
        "audit_report": audit_report,
        "files": files,
        "history": history,
        "history_report": history_report,
    }


def _render_markdown(metadata: Mapping[str, Any]) -> bytes:
    history = metadata["history"]
    audit_report = metadata["audit_report"]
    history_audit_ready = audit_report.get("audit_ready") is True
    render_ready = history.get("history_ready") is True and history_audit_ready
    lines = [
        "# Market-aware session history",
        "",
        f"- history_version: `{_safe_text(history['history_version'], label='history_version')}`",
        f"- symbol: `{_safe_value(history.get('symbol'), label='symbol')}`",
        f"- package_count: `{_safe_value(history.get('package_count'), label='package_count')}`",
        f"- first_as_of: `{_safe_value(history.get('first_as_of'), label='first_as_of')}`",
        f"- last_as_of: `{_safe_value(history.get('last_as_of'), label='last_as_of')}`",
        f"- history_ready: `{_safe_value(history.get('history_ready'), label='history_ready')}`",
        f"- history_audit_ready: `{_safe_value(history_audit_ready, label='history_audit_ready')}`",
        f"- render_ready: `{_safe_value(render_ready, label='render_ready')}`",
        f"- decision_ready: `{_safe_value(history.get('decision_ready'), label='decision_ready')}`",
        "",
        "## Session timeline",
        "",
        "| # | as_of | evaluation_at | reference_at | status | session_ready | "
        "freshness | freshness_ready | package SHA |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, entry in enumerate(history.get("packages") or [], start=1):
        lines.append(
            f"| {index} | `{_safe_value(entry.get('as_of'), label='as_of')}` | "
            f"`{_safe_value(entry.get('evaluation_at'), label='evaluation_at')}` | "
            f"`{_safe_value(entry.get('reference_at'), label='reference_at')}` | "
            f"`{_safe_value(entry.get('status'), label='status')}` | "
            f"`{_safe_value(entry.get('session_ready'), label='session_ready')}` | "
            f"`{_safe_value(entry.get('freshness_status'), label='freshness_status')}` | "
            f"`{_safe_value(entry.get('freshness_ready'), label='freshness_ready')}` | "
            f"`{_safe_value(entry.get('package_sha256'), label='package_sha256')}` |"
        )
    lines.extend(["", "## Package references", ""])
    for index, entry in enumerate(history.get("packages") or [], start=1):
        lines.extend(
            [
                f"### Package {index}",
                "",
                f"- manifest_path: `{_safe_text(entry['manifest_path'], label='manifest_path')}`",
                f"- report_path: `{_safe_text(entry['report_path'], label='report_path')}`",
                f"- artifact_root: `{_safe_text(entry['artifact_root'], label='artifact_root')}`",
                "- package_ready: `"
                f"{_safe_value(entry.get('package_ready'), label='package_ready')}`",
                f"- audit_ready: `{_safe_value(entry.get('audit_ready'), label='audit_ready')}`",
                "",
            ]
        )
    lines.extend(["## Issues", ""])
    issues = list(history.get("issues") or []) + list(audit_report.get("issues") or [])
    if issues:
        for issue in issues:
            lines.append(
                f"- `{_safe_text(issue['code'], label='issue.code')}`: "
                f"{_safe_text(issue['message'], label='issue.message')}"
            )
    else:
        lines.append("- No history or audit issues recorded.")
    lines.extend(
        [
            "",
            "This is a read-only chronological status view. It does not infer "
            "market trends, research quality, returns, investment value, or "
            "trading authorization.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _base_report() -> dict[str, Any]:
    return {
        "decision_ready": False,
        "first_as_of": None,
        "history_audit_ready": False,
        "history_path": None,
        "history_report_path": None,
        "history_report_sha256": None,
        "history_report_size_bytes": 0,
        "history_sha256": None,
        "history_size_bytes": 0,
        "history_ready": False,
        "issues": [],
        "last_as_of": None,
        "output_sha256": None,
        "package_count": 0,
        "render_ready": False,
        "render_version": MARKET_AWARE_SESSION_HISTORY_RENDER_VERSION,
        "symbol": None,
    }


def render_market_aware_session_history(
    *,
    history_path: Path,
    history_report_path: Path,
    history_audit_report_path: Path,
    history_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render a literal session history and audit summary as Markdown."""

    report = _base_report()
    can_write_report = False
    render_report_path = output_dir / "market_aware_session_history_render_report.json"
    try:
        root = history_root.resolve()
        output_resolved = output_dir.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryRenderError(
                "HISTORY_ROOT_INVALID", "history root must be a directory"
            )
        try:
            output_resolved.relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryRenderError(
                "PATH_OUTSIDE_HISTORY_ROOT", "render output is outside history root"
            ) from exc
        can_write_report = True
        metadata = _validate_inputs(
            history_path=history_path,
            history_report_path=history_report_path,
            history_audit_report_path=history_audit_report_path,
            root=root,
        )
        markdown = _render_markdown(metadata)
        write_atomic(output_dir / "market_aware_session_history.md", markdown)
        history = metadata["history"]
        audit_report = metadata["audit_report"]
        render_ready = history.get("history_ready") is True and audit_report.get(
            "audit_ready"
        ) is True
        report.update(
            {
                "first_as_of": history.get("first_as_of"),
                "history_audit_ready": audit_report.get("audit_ready"),
                "history_path": metadata["files"]["history"]["relative_path"],
                "history_report_path": metadata["files"]["history_report"]["relative_path"],
                "history_report_sha256": metadata["files"]["history_report"]["sha256"],
                "history_report_size_bytes": metadata["files"]["history_report"]["size_bytes"],
                "history_sha256": metadata["files"]["history"]["sha256"],
                "history_size_bytes": metadata["files"]["history"]["size_bytes"],
                "history_ready": history.get("history_ready"),
                "issues": list(history.get("issues") or [])
                + list(audit_report.get("issues") or []),
                "last_as_of": history.get("last_as_of"),
                "output_sha256": sha256_bytes(markdown),
                "package_count": history.get("package_count"),
                "render_ready": render_ready,
                "symbol": history.get("symbol"),
            }
        )
    except MarketAwareSessionHistoryRenderError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(render_report_path, _json_bytes(report))
    return report
