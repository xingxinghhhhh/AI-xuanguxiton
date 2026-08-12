"""Audit the Node48 admission summary and its evidence-chain bindings."""

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
from .market_aware_session_history_closure_admission import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION,
)
from .market_aware_session_history_closure_render_audit import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_AUDIT_VERSION,
)
from .market_aware_session_history_closure_renderer import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_AUDIT_VERSION = (
    "market-aware-session-history-closure-admission-audit-v1"
)
_ROLES = (
    "admission",
    "admission_report",
    "closure",
    "closure_report",
    "closure_markdown",
    "closure_render_report",
    "closure_render_audit_report",
)
_ADMISSION_INPUT_ROLES = (
    "closure",
    "closure_report",
    "markdown",
    "render_report",
    "render_audit_report",
)
_STATUSES = {"blocked", "ready", "stale"}


class MarketAwareSessionHistoryClosureAdmissionAuditError(ValueError):
    """A fail-closed admission audit error."""

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
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
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
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_path(value: Any, *, expected: str, label: str) -> None:
    if value != expected:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "CHAIN_MISMATCH", f"{label} is inconsistent"
        )


def _validate_input_manifest(
    value: Any, *, files: Mapping[str, Mapping[str, Any]]
) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_ADMISSION_INPUT_ROLES):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "INPUTS_INVALID", "admission report inputs are incomplete"
        )
    for role in _ADMISSION_INPUT_ROLES:
        item = value[role]
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "INPUTS_INVALID", f"admission input {role} is invalid"
            )
        source_role = {
            "closure": "closure",
            "closure_report": "closure_report",
            "markdown": "closure_markdown",
            "render_report": "closure_render_report",
            "render_audit_report": "closure_render_audit_report",
        }[role]
        if item.get("path") != files[source_role]["relative_path"]:
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "CHAIN_MISMATCH", f"admission input {role} path is inconsistent"
            )
        if item.get("byte_count") != files[source_role]["size_bytes"]:
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "SIZE_MISMATCH", f"admission input {role} byte count is inconsistent"
            )
        if item.get("sha256") != files[source_role]["sha256"]:
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "HASH_MISMATCH", f"admission input {role} SHA is inconsistent"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    admission: Mapping[str, Any],
    admission_report: Mapping[str, Any],
    closure: Mapping[str, Any],
    closure_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    render_audit_report: Mapping[str, Any],
) -> dict[str, Any]:
    if admission.get("admission_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "VERSION_MISMATCH", "admission version is invalid"
        )
    if admission_report.get("admission_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "VERSION_MISMATCH", "admission report version is invalid"
        )
    if closure.get("closure_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "VERSION_MISMATCH", "closure version is invalid"
        )
    if closure_report.get("closure_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "VERSION_MISMATCH", "closure report version is invalid"
        )
    if render_report.get("render_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "VERSION_MISMATCH", "render report version is invalid"
        )
    if render_audit_report.get("audit_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_AUDIT_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "VERSION_MISMATCH", "render audit report version is invalid"
        )
    for label, payload in (
        ("admission", admission),
        ("admission_report", admission_report),
        ("closure", closure),
        ("closure_report", closure_report),
        ("render_report", render_report),
        ("render_audit_report", render_audit_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    for label, payload in (
        ("admission", admission),
        ("admission_report", admission_report),
        ("closure", closure),
        ("closure_report", closure_report),
        ("render_report", render_report),
        ("render_audit_report", render_audit_report),
    ):
        _validate_self_hash(payload, label=label)
    if admission_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", "admission report SHA does not match admission"
        )
    if admission_report.get("status") != admission.get("status"):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "admission/report status differs"
        )
    _validate_input_manifest(admission_report.get("inputs"), files=files)
    if closure_report.get("closure_sha256") != files["closure"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", "closure report SHA does not match closure"
        )
    for payload, label in (
        (render_report, "render report"),
        (render_audit_report, "render audit report"),
    ):
        if payload.get("closure_sha256") != files["closure"]["sha256"]:
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "HASH_MISMATCH", f"{label} closure SHA is inconsistent"
            )
    if render_report.get("closure_report_sha256") != files["closure_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", "render report closure report SHA is inconsistent"
        )
    if render_report.get("markdown_sha256") != files["closure_markdown"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", "render report Markdown SHA is inconsistent"
        )
    if render_audit_report.get("closure_report_sha256") != files["closure_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", "render audit closure report SHA is inconsistent"
        )
    if render_audit_report.get("markdown_sha256") != files["closure_markdown"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", "render audit Markdown SHA is inconsistent"
        )
    if render_audit_report.get("render_report_sha256") != files["closure_render_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "HASH_MISMATCH", "render audit render report SHA is inconsistent"
        )
    _validate_path(
        render_report.get("closure_path"),
        expected=files["closure"]["relative_path"],
        label="render report closure_path",
    )
    _validate_path(
        render_report.get("closure_report_path"),
        expected=files["closure_report"]["relative_path"],
        label="render report closure_report_path",
    )
    for field, role in (
        ("closure_path", "closure"),
        ("closure_report_path", "closure_report"),
        ("markdown_path", "closure_markdown"),
        ("render_report_path", "closure_render_report"),
    ):
        _validate_path(
            render_audit_report.get(field),
            expected=files[role]["relative_path"],
            label=f"render audit {field}",
        )
    status = closure.get("status")
    if status not in _STATUSES:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "STATUS_INVALID", "closure status is invalid"
        )
    closure_ready = closure_report.get("closure_ready")
    if not isinstance(closure_ready, bool) or closure_ready is not (status == "ready"):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "closure status and closure_ready differ"
        )
    render_ready = render_report.get("render_ready")
    audit_ready = render_audit_report.get("audit_ready")
    admission_ready = admission.get("admission_ready")
    if admission.get("closure_status") != status:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "admission closure status differs"
        )
    expected_admission_ready = status == "ready" and closure_ready is True and audit_ready is True
    if admission_ready is not expected_admission_ready:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "admission_ready is inconsistent"
        )
    if admission.get("closure_ready") is not closure_ready:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "admission closure_ready is inconsistent"
        )
    if admission.get("render_ready") is not render_ready or render_ready is not closure_ready:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "render readiness is inconsistent"
        )
    if admission.get("audit_ready") is not audit_ready:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "audit readiness is inconsistent"
        )
    for payload, label in (
        (closure_report, "closure report"),
        (render_report, "render report"),
        (render_audit_report, "render audit report"),
        (admission, "admission"),
        (admission_report, "admission report"),
    ):
        if payload.get("status") != status and label not in {"render audit report"}:
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "FIELD_MISMATCH", f"{label} status differs"
            )
    if render_audit_report.get("closure_status") != status:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "render audit closure status differs"
        )
    if admission.get("issues") != admission_report.get("issues"):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_MISMATCH", "admission issues differ"
        )
    for field in ("symbol", "package_count", "first_as_of", "last_as_of"):
        for payload, label in (
            (closure, "closure"),
            (render_report, "render report"),
            (render_audit_report, "render audit report"),
            (admission, "admission"),
            (admission_report, "admission report"),
        ):
            if payload.get(field) != closure.get(field):
                raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                    "FIELD_MISMATCH", f"{label}.{field} differs"
                )
    _parse_datetime(closure.get("first_as_of"), label="closure.first_as_of")
    _parse_datetime(closure.get("last_as_of"), label="closure.last_as_of")
    if not isinstance(closure.get("symbol"), str) or not closure.get("symbol"):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_INVALID", "closure.symbol is required"
        )
    if not isinstance(closure.get("package_count"), int) or closure.get("package_count") < 0:
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "FIELD_INVALID", "closure.package_count is invalid"
        )
    if len({item["path"] for item in files.values()}) != len(files):
        raise MarketAwareSessionHistoryClosureAdmissionAuditError(
            "PATH_INVALID", "audit inputs must be distinct files"
        )
    return {
        "admission_ready": admission_ready,
        "audit_ready": audit_ready,
        "closure_ready": closure_ready,
        "closure_status": status,
        "first_as_of": closure.get("first_as_of"),
        "last_as_of": closure.get("last_as_of"),
        "package_count": closure.get("package_count"),
        "render_ready": render_ready,
        "symbol": closure.get("symbol"),
    }


def _base_report() -> dict[str, Any]:
    return {
        "admission_ready": False,
        "admission_report_path": None,
        "admission_report_sha256": None,
        "admission_sha256": None,
        "admission_path": None,
        "audit_ready": False,
        "audit_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_AUDIT_VERSION,
        "closure_ready": False,
        "closure_status": "invalid",
        "decision_ready": False,
        "issues": [],
        "output_sha256": None,
        "render_ready": False,
        "status": "invalid",
    }


def _finalize_report(report: dict[str, Any]) -> bytes:
    payload = dict(report)
    payload["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(payload))
    return _json_bytes(report)


def audit_market_aware_session_history_closure_admission(
    *,
    admission_path: Path,
    admission_report_path: Path,
    closure_path: Path,
    closure_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    render_audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Audit Node48 admission and its declared evidence chain."""

    report = _base_report()
    can_write_report = False
    audit_report_path = (
        output_dir / "market_aware_session_history_closure_admission_audit_report.json"
    )
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureAdmissionAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "audit output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "admission": _safe_file(admission_path, root=root, label="admission"),
            "admission_report": _safe_file(
                admission_report_path, root=root, label="admission report"
            ),
            "closure": _safe_file(closure_path, root=root, label="closure"),
            "closure_report": _safe_file(
                closure_report_path, root=root, label="closure report"
            ),
            "closure_markdown": _safe_file(
                markdown_path, root=root, label="closure Markdown"
            ),
            "closure_render_report": _safe_file(
                render_report_path, root=root, label="closure render report"
            ),
            "closure_render_audit_report": _safe_file(
                render_audit_report_path,
                root=root,
                label="closure render audit report",
            ),
        }
        payloads = {
            role: _read_json(files[role]["raw"], label=role)
            for role in (
                "admission",
                "admission_report",
                "closure",
                "closure_report",
            )
        }
        payloads["render_report"] = _read_json(
            files["closure_render_report"]["raw"], label="render report"
        )
        payloads["render_audit_report"] = _read_json(
            files["closure_render_audit_report"]["raw"], label="render audit report"
        )
        files["closure_markdown"]["raw"].decode("utf-8")
        summary = _validate_chain(files=files, **payloads)
        report.update(
            {
                **summary,
                "admission_path": files["admission"]["relative_path"],
                "admission_report_path": files["admission_report"]["relative_path"],
                "admission_report_sha256": files["admission_report"]["sha256"],
                "admission_sha256": files["admission"]["sha256"],
                "artifacts": [
                    {
                        "byte_count": files[role]["size_bytes"],
                        "path": files[role]["relative_path"],
                        "role": role,
                        "sha256": files[role]["sha256"],
                    }
                    for role in _ROLES
                ],
                "status": summary["closure_status"],
            }
        )
    except MarketAwareSessionHistoryClosureAdmissionAuditError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(audit_report_path, _finalize_report(report))
    else:
        _finalize_report(report)
    return report
