"""Render the Node45 closure as a safe, deterministic Markdown view."""

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

MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION = (
    "market-aware-session-history-closure-render-v1"
)
_RENDERABLE_STATUSES = {"blocked", "ready", "stale"}
_INPUT_ROLES = (
    "manifest",
    "manifest_report",
    "manifest_audit_report",
    "render_report",
    "render_audit_report",
)


class MarketAwareSessionHistoryClosureRenderError(ValueError):
    """A fail-closed closure renderer error."""

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
        raise MarketAwareSessionHistoryClosureRenderError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryClosureRenderError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureRenderError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryClosureRenderError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryClosureRenderError(
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
        raise MarketAwareSessionHistoryClosureRenderError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryClosureRenderError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise MarketAwareSessionHistoryClosureRenderError(
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
        raise MarketAwareSessionHistoryClosureRenderError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureRenderError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryClosureRenderError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryClosureRenderError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryClosureRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_report_inputs(value: Any, *, root: Path) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_INPUT_ROLES):
        raise MarketAwareSessionHistoryClosureRenderError(
            "INPUTS_INVALID", "closure report inputs are incomplete"
        )
    for role in _INPUT_ROLES:
        entry = value[role]
        if not isinstance(entry, Mapping):
            raise MarketAwareSessionHistoryClosureRenderError(
                "INPUTS_INVALID", f"closure report input {role} is invalid"
            )
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise MarketAwareSessionHistoryClosureRenderError(
                "PATH_INVALID", f"closure report input {role} path is invalid"
            )
        try:
            (root / relative).resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureRenderError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", f"closure report input {role} escapes root"
            ) from exc
        if not isinstance(entry.get("byte_count"), int) or entry.get("byte_count") < 0:
            raise MarketAwareSessionHistoryClosureRenderError(
                "INPUTS_INVALID", f"closure report input {role} byte count is invalid"
            )
        sha = entry.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise MarketAwareSessionHistoryClosureRenderError(
                "INPUTS_INVALID", f"closure report input {role} SHA is invalid"
            )


def _validate_inputs(
    *,
    files: Mapping[str, Mapping[str, Any]],
    closure: Mapping[str, Any],
    closure_report: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    for label, payload in (("closure", closure), ("closure_report", closure_report)):
        if payload.get("closure_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION:
            raise MarketAwareSessionHistoryClosureRenderError(
                "VERSION_MISMATCH", f"{label} closure version is invalid"
            )
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryClosureRenderError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    _validate_self_hash(closure, label="closure")
    _validate_self_hash(closure_report, label="closure_report")
    if closure_report.get("closure_sha256") != files["closure"]["sha256"]:
        raise MarketAwareSessionHistoryClosureRenderError(
            "HASH_MISMATCH", "closure report SHA does not match closure"
        )
    if closure_report.get("status") != closure.get("status"):
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_MISMATCH", "closure/report status differs"
        )
    closure_ready = closure_report.get("closure_ready")
    if not isinstance(closure_ready, bool):
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_INVALID", "closure report closure_ready must be boolean"
        )
    if "closure_ready" in closure and closure.get("closure_ready") is not closure_ready:
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_MISMATCH", "closure/report readiness differs"
        )
    if closure_report.get("issues") != closure.get("issues"):
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_MISMATCH", "closure/report issues differ"
        )
    if closure_report.get("artifact_root") != ".":
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_MISMATCH", "closure report artifact root is invalid"
        )
    _validate_report_inputs(closure_report.get("inputs"), root=root)
    status = closure.get("status")
    if status not in _RENDERABLE_STATUSES:
        raise MarketAwareSessionHistoryClosureRenderError(
            "STATUS_INVALID", "closure status is not renderable"
        )
    if closure_ready is not (status == "ready"):
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_MISMATCH", "closure status and readiness differ"
        )
    if not isinstance(closure.get("symbol"), str) or not closure.get("symbol"):
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_INVALID", "closure.symbol is required"
        )
    if not isinstance(closure.get("package_count"), int) or closure.get("package_count") < 0:
        raise MarketAwareSessionHistoryClosureRenderError(
            "FIELD_INVALID", "closure.package_count is invalid"
        )
    _parse_datetime(closure.get("first_as_of"), label="closure.first_as_of")
    _parse_datetime(closure.get("last_as_of"), label="closure.last_as_of")
    for field in (
        "manifest_sha256",
        "manifest_audit_sha256",
        "render_report_sha256",
        "render_audit_sha256",
    ):
        value = closure.get(field)
        if not isinstance(value, str) or len(value) != 64:
            raise MarketAwareSessionHistoryClosureRenderError(
                "HASH_INVALID", f"closure.{field} is invalid"
            )
    return {
        "closure": closure,
        "closure_report": closure_report,
        "closure_ready": closure_ready,
        "status": status,
    }


def _render_markdown(metadata: Mapping[str, Any]) -> bytes:
    closure = metadata["closure"]
    history_audit_ready = _safe_value(
        closure.get("history_audit_ready"), label="history_audit_ready"
    )
    lines = [
        "# Market-aware session history closure",
        "",
        f"- closure_version: `{_safe_text(closure['closure_version'], label='closure_version')}`",
        f"- status: `{_safe_value(closure.get('status'), label='status')}`",
        f"- symbol: `{_safe_value(closure.get('symbol'), label='symbol')}`",
        f"- package_count: `{_safe_value(closure.get('package_count'), label='package_count')}`",
        f"- first_as_of: `{_safe_value(closure.get('first_as_of'), label='first_as_of')}`",
        f"- last_as_of: `{_safe_value(closure.get('last_as_of'), label='last_as_of')}`",
        f"- closure_ready: `{_safe_value(metadata['closure_ready'], label='closure_ready')}`",
        f"- history_ready: `{_safe_value(closure.get('history_ready'), label='history_ready')}`",
        f"- history_audit_ready: `{history_audit_ready}`",
        f"- render_ready: `{_safe_value(closure.get('render_ready'), label='render_ready')}`",
        f"- audit_ready: `{_safe_value(closure.get('audit_ready'), label='audit_ready')}`",
        f"- decision_ready: `{_safe_value(closure.get('decision_ready'), label='decision_ready')}`",
        "",
        "## Evidence-chain hashes",
        "",
        "| field | sha256 |",
        "| --- | --- |",
    ]
    for field in (
        "manifest_sha256",
        "manifest_audit_sha256",
        "render_report_sha256",
        "render_audit_sha256",
    ):
        lines.append(
            f"| `{_safe_text(field, label='hash field')}` | "
            f"`{_safe_text(closure[field], label=field)}` |"
        )
    lines.extend(["", "## Issues", ""])
    issues = list(closure.get("issues") or [])
    if issues:
        for issue in issues:
            lines.append(
                f"- `{_safe_text(issue['code'], label='issue.code')}`: "
                f"{_safe_text(issue['message'], label='issue.message')}"
            )
    else:
        lines.append("- No closure issues recorded.")
    lines.extend(
        [
            "",
            "This is a read-only evidence-chain view. It does not infer market "
            "trends, research quality, returns, investment value, or trading authorization.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _base_report() -> dict[str, Any]:
    return {
        "closure_path": None,
        "closure_report_path": None,
        "closure_report_sha256": None,
        "closure_ready": False,
        "closure_sha256": None,
        "decision_ready": False,
        "first_as_of": None,
        "issues": [],
        "last_as_of": None,
        "output_sha256": None,
        "package_count": 0,
        "render_ready": False,
        "render_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION,
        "status": "invalid",
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(payload))
    return _json_bytes(report)


def render_market_aware_session_history_closure(
    *,
    closure_path: Path,
    closure_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render a Node45 closure and report as deterministic Markdown."""

    report = _base_report()
    can_write_report = False
    markdown_path = output_dir / "market_aware_session_history_closure.md"
    render_report_path = output_dir / "market_aware_session_history_closure_render_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryClosureRenderError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureRenderError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "render output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "closure": _safe_file(closure_path, root=root, label="closure"),
            "closure_report": _safe_file(
                closure_report_path, root=root, label="closure report"
            ),
        }
        closure = _read_json(files["closure"]["raw"], label="closure")
        closure_report = _read_json(
            files["closure_report"]["raw"], label="closure report"
        )
        metadata = _validate_inputs(
            files=files,
            closure=closure,
            closure_report=closure_report,
            root=root,
        )
        markdown = _render_markdown(metadata)
        write_atomic(markdown_path, markdown)
        report.update(
            {
                "closure_path": files["closure"]["relative_path"],
                "closure_report_path": files["closure_report"]["relative_path"],
                "closure_report_sha256": files["closure_report"]["sha256"],
                "closure_ready": metadata["closure_ready"],
                "closure_sha256": files["closure"]["sha256"],
                "decision_ready": False,
                "first_as_of": closure.get("first_as_of"),
                "issues": list(closure.get("issues") or []),
                "last_as_of": closure.get("last_as_of"),
                "markdown_sha256": sha256_bytes(markdown),
                "package_count": closure.get("package_count"),
                "render_ready": metadata["closure_ready"],
                "status": metadata["status"],
                "symbol": closure.get("symbol"),
            }
        )
    except MarketAwareSessionHistoryClosureRenderError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(render_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
