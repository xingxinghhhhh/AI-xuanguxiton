"""Audit the Node50 admission Markdown render and its input bindings."""

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
from .market_aware_session_history_closure_admission_renderer import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_AUDIT_VERSION = (
    "market-aware-session-history-closure-admission-render-audit-v1"
)
_STATUSES = {"blocked", "ready", "stale"}
_ADMISSION_INPUT_ROLES = {
    "closure",
    "closure_report",
    "markdown",
    "render_report",
    "render_audit_report",
}


class MarketAwareSessionHistoryClosureAdmissionRenderAuditError(ValueError):
    """A fail-closed admission render audit error."""

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
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
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
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(
            item.get("message"), str
        ):
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_relative_path(value: Any, *, root: Path, label: str) -> None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "PATH_INVALID", f"{label} is invalid"
        )
    try:
        (root / value).resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc


def _validate_input_manifest(value: Any, *, root: Path) -> None:
    if not isinstance(value, Mapping) or set(value) != _ADMISSION_INPUT_ROLES:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "INPUTS_INVALID", "admission report inputs are incomplete"
        )
    paths: set[str] = set()
    for role in sorted(_ADMISSION_INPUT_ROLES):
        item = value[role]
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "INPUTS_INVALID", f"admission input {role} is invalid"
            )
        relative = item.get("path")
        _validate_relative_path(relative, root=root, label=f"admission input {role} path")
        if relative in paths:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "PATH_INVALID", "admission report inputs are not distinct"
            )
        paths.add(relative)
        if not isinstance(item.get("byte_count"), int) or item["byte_count"] < 0:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "INPUTS_INVALID", f"admission input {role} byte count is invalid"
            )
        sha = item.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "INPUTS_INVALID", f"admission input {role} SHA is invalid"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    admission: Mapping[str, Any],
    admission_report: Mapping[str, Any],
    admission_audit_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    if admission.get("admission_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "VERSION_MISMATCH", "admission version is invalid"
        )
    if admission_report.get("admission_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "VERSION_MISMATCH", "admission report version is invalid"
        )
    if admission_audit_report.get("audit_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_AUDIT_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "VERSION_MISMATCH", "admission audit report version is invalid"
        )
    if render_report.get("render_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "VERSION_MISMATCH", "render report version is invalid"
        )
    for label, payload in (
        ("admission", admission),
        ("admission_report", admission_report),
        ("admission_audit_report", admission_audit_report),
        ("render_report", render_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
        _validate_self_hash(payload, label=label)

    if admission_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "HASH_MISMATCH", "admission report SHA does not match admission"
        )
    if admission_audit_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "HASH_MISMATCH", "admission audit SHA does not match admission"
        )
    if admission_audit_report.get("admission_report_sha256") != files["admission_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "HASH_MISMATCH", "admission audit SHA does not match admission report"
        )
    file_roles = {
        "admission_sha256": "admission",
        "admission_report_sha256": "admission_report",
        "admission_audit_report_sha256": "admission_audit_report",
    }
    for field in (
        "admission_sha256",
        "admission_report_sha256",
        "admission_audit_report_sha256",
    ):
        if render_report.get(field) != files[file_roles[field]]["sha256"]:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "HASH_MISMATCH", f"render report {field} is inconsistent"
            )
    if render_report.get("markdown_sha256") != files["markdown"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "HASH_MISMATCH", "render report Markdown SHA does not match Markdown"
        )
    _validate_input_manifest(admission_report.get("inputs"), root=root)

    for field, role in (
        ("admission_path", "admission"),
        ("admission_report_path", "admission_report"),
        ("admission_audit_report_path", "admission_audit_report"),
    ):
        if render_report.get(field) != files[role]["relative_path"]:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "CHAIN_MISMATCH", f"render report {field} is inconsistent"
            )
    if admission_audit_report.get("admission_path") != files["admission"]["relative_path"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "CHAIN_MISMATCH", "admission audit report admission path is inconsistent"
        )
    if admission_audit_report.get("admission_report_path") != files[
        "admission_report"
    ]["relative_path"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "CHAIN_MISMATCH", "admission audit report admission report path is inconsistent"
        )

    if len({item["path"] for item in files.values()}) != len(files):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "PATH_INVALID", "render audit inputs must be distinct files"
        )
    status = admission.get("status")
    if status not in _STATUSES:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "STATUS_INVALID", "admission status is invalid"
        )
    if any(
        payload.get("status") != status
        for payload in (
            admission_report,
            admission_audit_report,
            render_report,
        )
    ):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_MISMATCH", "admission/report/audit/render status differs"
        )

    for field in ("admission_ready", "audit_ready", "render_ready"):
        admission_value = admission.get(field)
        report_value = admission_report.get(field)
        render_value = render_report.get(field)
        if not isinstance(admission_value, bool):
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "FIELD_INVALID", f"admission.{field} must be boolean"
            )
        if report_value is not admission_value or render_value is not admission_value:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "FIELD_MISMATCH", f"{field} readiness differs"
            )
    audit_ready = admission_audit_report.get("audit_ready")
    if audit_ready is not True:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_MISMATCH", "admission audit is not ready"
        )
    if admission_audit_report.get("admission_ready") is not admission["admission_ready"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_MISMATCH", "admission audit admission_ready differs"
        )
    if admission_audit_report.get("render_ready") is not admission["render_ready"]:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_MISMATCH", "admission audit render_ready differs"
        )
    if admission.get("issues") != admission_report.get("issues"):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_MISMATCH", "admission/report issues differ"
        )
    if render_report.get("issues") != admission.get("issues"):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_MISMATCH", "render report issues differ"
        )
    for field in ("symbol", "package_count", "first_as_of", "last_as_of"):
        for payload, label in (
            (admission_report, "admission report"),
            (admission_audit_report, "admission audit report"),
            (render_report, "render report"),
        ):
            if payload.get(field) != admission.get(field):
                raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                    "FIELD_MISMATCH", f"{label}.{field} differs"
                )
    if not isinstance(admission.get("symbol"), str) or not admission.get("symbol"):
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_INVALID", "admission.symbol is required"
        )
    if not isinstance(admission.get("package_count"), int) or admission.get("package_count") < 0:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "FIELD_INVALID", "admission.package_count is invalid"
        )
    _parse_datetime(admission.get("first_as_of"), label="admission.first_as_of")
    _parse_datetime(admission.get("last_as_of"), label="admission.last_as_of")
    try:
        files["markdown"]["raw"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
            "MARKDOWN_INVALID", "Markdown is not valid UTF-8"
        ) from exc
    return {
        "admission_audit_ready": audit_ready,
        "admission_ready": admission["admission_ready"],
        "first_as_of": admission["first_as_of"],
        "last_as_of": admission["last_as_of"],
        "package_count": admission["package_count"],
        "render_audit_ready": audit_ready,
        "render_ready": admission["render_ready"],
        "status": status,
        "symbol": admission["symbol"],
    }


def _base_report() -> dict[str, Any]:
    return {
        "admission_audit_report_path": None,
        "admission_audit_report_sha256": None,
        "admission_audit_ready": False,
        "admission_path": None,
        "admission_report_path": None,
        "admission_report_sha256": None,
        "admission_ready": False,
        "admission_sha256": None,
        "audit_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_AUDIT_VERSION,
        "decision_ready": False,
        "first_as_of": None,
        "issues": [],
        "last_as_of": None,
        "markdown_path": None,
        "markdown_sha256": None,
        "output_sha256": None,
        "package_count": 0,
        "render_audit_ready": False,
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


def audit_market_aware_session_history_closure_admission_render(
    *,
    admission_path: Path,
    admission_report_path: Path,
    admission_audit_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit the Node50 admission Markdown render without rebuilding it."""

    report = _base_report()
    can_write_report = False
    audit_report_path = (
        output_dir
        / "market_aware_session_history_closure_admission_render_audit_report.json"
    )
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureAdmissionRenderAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "admission": _safe_file(admission_path, root=root, label="admission"),
            "admission_report": _safe_file(
                admission_report_path, root=root, label="admission report"
            ),
            "admission_audit_report": _safe_file(
                admission_audit_report_path,
                root=root,
                label="admission audit report",
            ),
            "markdown": _safe_file(markdown_path, root=root, label="admission Markdown"),
            "render_report": _safe_file(
                render_report_path, root=root, label="admission render report"
            ),
        }
        payloads = {
            "admission": _read_json(files["admission"]["raw"], label="admission"),
            "admission_report": _read_json(
                files["admission_report"]["raw"], label="admission report"
            ),
            "admission_audit_report": _read_json(
                files["admission_audit_report"]["raw"],
                label="admission audit report",
            ),
            "render_report": _read_json(
                files["render_report"]["raw"], label="admission render report"
            ),
        }
        summary = _validate_chain(files=files, root=root, **payloads)
        report.update(
            {
                **summary,
                "admission_audit_report_path": files["admission_audit_report"][
                    "relative_path"
                ],
                "admission_audit_report_sha256": files["admission_audit_report"][
                    "sha256"
                ],
                "admission_path": files["admission"]["relative_path"],
                "admission_report_path": files["admission_report"]["relative_path"],
                "admission_report_sha256": files["admission_report"]["sha256"],
                "admission_sha256": files["admission"]["sha256"],
                "decision_ready": False,
                "issues": list(payloads["admission"].get("issues") or []),
                "markdown_path": files["markdown"]["relative_path"],
                "markdown_sha256": files["markdown"]["sha256"],
                "render_report_path": files["render_report"]["relative_path"],
                "render_report_sha256": files["render_report"]["sha256"],
            }
        )
    except MarketAwareSessionHistoryClosureAdmissionRenderAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
