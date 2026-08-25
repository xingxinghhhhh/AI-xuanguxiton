"""Render the Node48 admission and Node49 audit as safe Markdown."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .market_aware_session_history_closure_admission import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION,
)
from .market_aware_session_history_closure_admission_audit import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_AUDIT_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_VERSION = (
    "market-aware-session-history-closure-admission-render-v1"
)
_STATUSES = {"blocked", "ready", "stale"}
_ADMISSION_INPUT_ROLES = {
    "closure",
    "closure_report",
    "markdown",
    "render_report",
    "render_audit_report",
}


class MarketAwareSessionHistoryClosureAdmissionRenderError(ValueError):
    """A fail-closed admission renderer error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
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
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(
            item.get("message"), str
        ):
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_relative_path(value: Any, *, root: Path, label: str) -> None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "PATH_INVALID", f"{label} is invalid"
        )
    try:
        (root / value).resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc


def _validate_input_manifest(value: Any, *, root: Path) -> None:
    if not isinstance(value, Mapping) or set(value) != _ADMISSION_INPUT_ROLES:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "INPUTS_INVALID", "admission report inputs are incomplete"
        )
    paths: set[str] = set()
    for role in sorted(_ADMISSION_INPUT_ROLES):
        item = value[role]
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "INPUTS_INVALID", f"admission input {role} is invalid"
            )
        relative = item.get("path")
        _validate_relative_path(relative, root=root, label=f"admission input {role} path")
        if relative in paths:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "PATH_INVALID", "admission report inputs are not distinct"
            )
        paths.add(relative)
        if not isinstance(item.get("byte_count"), int) or item["byte_count"] < 0:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "INPUTS_INVALID", f"admission input {role} byte count is invalid"
            )
        sha = item.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "INPUTS_INVALID", f"admission input {role} SHA is invalid"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    admission: Mapping[str, Any],
    admission_report: Mapping[str, Any],
    audit_report: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    if admission.get("admission_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "VERSION_MISMATCH", "admission version is invalid"
        )
    if admission_report.get("admission_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "VERSION_MISMATCH", "admission report version is invalid"
        )
    if audit_report.get("audit_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_AUDIT_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "VERSION_MISMATCH", "admission audit report version is invalid"
        )
    for label, payload in (
        ("admission", admission),
        ("admission_report", admission_report),
        ("admission_audit_report", audit_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
        _validate_self_hash(payload, label=label)

    if admission_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "HASH_MISMATCH", "admission report SHA does not match admission"
        )
    if audit_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "HASH_MISMATCH", "audit report admission SHA does not match admission"
        )
    if audit_report.get("admission_report_sha256") != files["admission_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "HASH_MISMATCH", "audit report admission report SHA does not match report"
        )
    _validate_input_manifest(admission_report.get("inputs"), root=root)

    if len({item["path"] for item in files.values()}) != len(files):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "PATH_INVALID", "admission render inputs must be distinct files"
        )
    status = admission.get("status")
    if status not in _STATUSES:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "STATUS_INVALID", "admission status is invalid"
        )
    if admission_report.get("status") != status or audit_report.get("status") != status:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "FIELD_MISMATCH", "admission/report/audit status differs"
        )
    if audit_report.get("admission_path") != files["admission"]["relative_path"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "CHAIN_MISMATCH", "audit report admission path is inconsistent"
        )
    if audit_report.get("admission_report_path") != files["admission_report"]["relative_path"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "CHAIN_MISMATCH", "audit report admission report path is inconsistent"
        )

    for field in ("admission_ready", "audit_ready", "render_ready"):
        admission_value = admission.get(field)
        report_value = admission_report.get(field)
        audit_value = audit_report.get(field)
        if not isinstance(admission_value, bool):
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "FIELD_INVALID", f"admission.{field} must be boolean"
            )
        if report_value is not admission_value:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "FIELD_MISMATCH", f"admission/report {field} differs"
            )
        if field == "audit_ready" and audit_value is not admission_value:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "FIELD_MISMATCH", f"admission/audit {field} differs"
            )
        if field == "render_ready" and audit_value is not admission_value:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "FIELD_MISMATCH", f"admission/audit {field} differs"
            )

    if audit_report.get("audit_ready") is not True:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "FIELD_MISMATCH", "admission audit is not ready"
        )
    expected_render_ready = (
        admission.get("admission_ready") is True
        and audit_report.get("audit_ready") is True
    )
    if admission.get("render_ready") is not expected_render_ready:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "FIELD_MISMATCH", "render readiness is inconsistent"
        )
    if admission.get("issues") != admission_report.get("issues"):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "FIELD_MISMATCH", "admission/report issues differ"
        )
    for field in ("symbol", "package_count", "first_as_of", "last_as_of"):
        for payload, label in (
            (admission, "admission"),
            (admission_report, "admission report"),
            (audit_report, "admission audit report"),
        ):
            if payload.get(field) != admission.get(field):
                raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                    "FIELD_MISMATCH", f"{label}.{field} differs"
                )
    if not isinstance(admission.get("symbol"), str) or not admission.get("symbol"):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "FIELD_INVALID", "admission.symbol is required"
        )
    if not isinstance(admission.get("package_count"), int) or admission.get("package_count") < 0:
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
            "FIELD_INVALID", "admission.package_count is invalid"
        )
    _parse_datetime(admission.get("first_as_of"), label="admission.first_as_of")
    _parse_datetime(admission.get("last_as_of"), label="admission.last_as_of")
    return {
        "admission_ready": admission["admission_ready"],
        "audit_issues": list(audit_report["issues"]),
        "audit_ready": audit_report["audit_ready"],
        "first_as_of": admission["first_as_of"],
        "last_as_of": admission["last_as_of"],
        "package_count": admission["package_count"],
        "render_ready": admission["render_ready"],
        "status": status,
        "symbol": admission["symbol"],
    }


def _safe_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise MarketAwareSessionHistoryClosureAdmissionRenderError(
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


def _render_issues(
    lines: list[str], issues: list[Mapping[str, Any]], *, empty_message: str
) -> None:
    if not issues:
        lines.append(f"- {empty_message}")
        return
    for issue in issues:
        lines.append(
            f"- `{_safe_text(issue['code'], label='issue.code')}`: "
            f"{_safe_text(issue['message'], label='issue.message')}"
        )


def _render_markdown(
    *,
    admission: Mapping[str, Any],
    audit_report: Mapping[str, Any],
    files: Mapping[str, Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> bytes:
    lines = [
        "# Market-aware session history closure admission",
        "",
        "- admission_version: `"
        f"{_safe_text(admission['admission_version'], label='admission_version')}`",
        f"- status: `{_safe_value(metadata['status'], label='status')}`",
        f"- symbol: `{_safe_value(metadata['symbol'], label='symbol')}`",
        f"- package_count: `{_safe_value(metadata['package_count'], label='package_count')}`",
        f"- first_as_of: `{_safe_value(metadata['first_as_of'], label='first_as_of')}`",
        f"- last_as_of: `{_safe_value(metadata['last_as_of'], label='last_as_of')}`",
        f"- admission_ready: `{_safe_value(metadata['admission_ready'], label='admission_ready')}`",
        f"- audit_ready: `{_safe_value(metadata['audit_ready'], label='audit_ready')}`",
        f"- render_ready: `{_safe_value(metadata['render_ready'], label='render_ready')}`",
        f"- decision_ready: `{_safe_value(admission['decision_ready'], label='decision_ready')}`",
        "",
        "## Evidence-chain hashes",
        "",
        "| field | sha256 |",
        "| --- | --- |",
        f"| `admission_sha256` | `{files['admission']['sha256']}` |",
        f"| `admission_report_sha256` | `{files['admission_report']['sha256']}` |",
        f"| `admission_audit_report_sha256` | `{files['audit_report']['sha256']}` |",
        "",
        "## Input paths",
        "",
        "- admission: `"
        f"{_safe_text(files['admission']['relative_path'], label='admission path')}`",
        "- admission_report: `"
        f"{_safe_text(files['admission_report']['relative_path'], label='admission report path')}`",
        "- admission_audit_report: `"
        f"{_safe_text(files['audit_report']['relative_path'], label='audit report path')}`",
        "",
        "## Admission issues",
        "",
    ]
    _render_issues(
        lines,
        list(admission.get("issues") or []),
        empty_message="No admission issues recorded.",
    )
    lines.extend(["", "## Audit issues", ""])
    _render_issues(
        lines,
        list(audit_report.get("issues") or []),
        empty_message="No admission audit issues recorded.",
    )
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
        "admission_audit_report_path": None,
        "admission_audit_report_sha256": None,
        "admission_path": None,
        "admission_report_path": None,
        "admission_report_sha256": None,
        "admission_ready": False,
        "admission_sha256": None,
        "audit_ready": False,
        "decision_ready": False,
        "first_as_of": None,
        "issues": [],
        "last_as_of": None,
        "markdown_sha256": None,
        "output_sha256": None,
        "package_count": 0,
        "render_ready": False,
        "render_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_VERSION,
        "status": "invalid",
        "symbol": None,
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(payload))
    return _json_bytes(report)


def render_market_aware_session_history_closure_admission(
    *,
    admission_path: Path,
    admission_report_path: Path,
    admission_audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Render Node48 admission and Node49 audit as deterministic Markdown."""

    report = _base_report()
    can_write_report = False
    markdown_path = output_dir / "market_aware_session_history_closure_admission.md"
    render_report_path = (
        output_dir / "market_aware_session_history_closure_admission_render_report.json"
    )
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureAdmissionRenderError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "render output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "admission": _safe_file(admission_path, root=root, label="admission"),
            "admission_report": _safe_file(
                admission_report_path, root=root, label="admission report"
            ),
            "audit_report": _safe_file(
                admission_audit_report_path,
                root=root,
                label="admission audit report",
            ),
        }
        payloads = {
            "admission": _read_json(files["admission"]["raw"], label="admission"),
            "admission_report": _read_json(
                files["admission_report"]["raw"], label="admission report"
            ),
            "audit_report": _read_json(
                files["audit_report"]["raw"], label="admission audit report"
            ),
        }
        metadata = _validate_chain(files=files, root=root, **payloads)
        markdown = _render_markdown(
            admission=payloads["admission"],
            audit_report=payloads["audit_report"],
            files=files,
            metadata=metadata,
        )
        write_atomic(markdown_path, markdown)
        report.update(
            {
                "admission_audit_report_path": files["audit_report"]["relative_path"],
                "admission_audit_report_sha256": files["audit_report"]["sha256"],
                "admission_path": files["admission"]["relative_path"],
                "admission_report_path": files["admission_report"]["relative_path"],
                "admission_report_sha256": files["admission_report"]["sha256"],
                "admission_ready": metadata["admission_ready"],
                "admission_sha256": files["admission"]["sha256"],
                "audit_ready": metadata["audit_ready"],
                "decision_ready": False,
                "first_as_of": metadata["first_as_of"],
                "issues": list(payloads["admission"].get("issues") or []),
                "last_as_of": metadata["last_as_of"],
                "markdown_sha256": sha256_bytes(markdown),
                "package_count": metadata["package_count"],
                "render_ready": metadata["render_ready"],
                "status": metadata["status"],
                "symbol": metadata["symbol"],
            }
        )
    except MarketAwareSessionHistoryClosureAdmissionRenderError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(render_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
