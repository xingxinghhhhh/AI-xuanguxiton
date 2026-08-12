"""Build the final deterministic receipt for the session-history evidence chain."""

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
from .market_aware_session_history_closure_admission_render_audit import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_AUDIT_VERSION,
)
from .market_aware_session_history_closure_admission_renderer import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION = (
    "market-aware-session-history-final-receipt-v1"
)
_STATUSES = {"blocked", "ready", "stale"}
_INPUT_ROLES = (
    "admission",
    "admission_report",
    "render_report",
    "render_audit_report",
)
_ADMISSION_INPUT_ROLES = {
    "closure",
    "closure_report",
    "markdown",
    "render_report",
    "render_audit_report",
}


class MarketAwareSessionHistoryFinalReceiptError(ValueError):
    """A fail-closed final receipt error."""

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
        raise MarketAwareSessionHistoryFinalReceiptError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryFinalReceiptError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryFinalReceiptError(
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
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryFinalReceiptError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(
            item.get("message"), str
        ):
            raise MarketAwareSessionHistoryFinalReceiptError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryFinalReceiptError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_relative_path(value: Any, *, root: Path, label: str) -> None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise MarketAwareSessionHistoryFinalReceiptError(
            "PATH_INVALID", f"{label} is invalid"
        )
    try:
        (root / value).resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc


def _validate_admission_inputs(value: Any, *, root: Path) -> None:
    if not isinstance(value, Mapping) or set(value) != _ADMISSION_INPUT_ROLES:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "INPUTS_INVALID", "admission report inputs are incomplete"
        )
    paths: set[str] = set()
    for role in sorted(_ADMISSION_INPUT_ROLES):
        item = value[role]
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryFinalReceiptError(
                "INPUTS_INVALID", f"admission input {role} is invalid"
            )
        relative = item.get("path")
        _validate_relative_path(relative, root=root, label=f"admission input {role} path")
        if relative in paths:
            raise MarketAwareSessionHistoryFinalReceiptError(
                "PATH_INVALID", "admission report inputs are not distinct"
            )
        paths.add(relative)
        if not isinstance(item.get("byte_count"), int) or item["byte_count"] < 0:
            raise MarketAwareSessionHistoryFinalReceiptError(
                "INPUTS_INVALID", f"admission input {role} byte count is invalid"
            )
        if not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64:
            raise MarketAwareSessionHistoryFinalReceiptError(
                "INPUTS_INVALID", f"admission input {role} SHA is invalid"
            )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    admission: Mapping[str, Any],
    admission_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    render_audit_report: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    versions = (
        (
            "admission",
            admission,
            "admission_version",
            MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION,
        ),
        (
            "admission_report",
            admission_report,
            "admission_version",
            MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION,
        ),
        (
            "render_report",
            render_report,
            "render_version",
            MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_VERSION,
        ),
        (
            "render_audit_report",
            render_audit_report,
            "audit_version",
            MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_RENDER_AUDIT_VERSION,
        ),
    )
    for label, payload, field, expected in versions:
        if payload.get(field) != expected:
            raise MarketAwareSessionHistoryFinalReceiptError(
                "VERSION_MISMATCH", f"{label} version is invalid"
            )
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryFinalReceiptError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
        _validate_self_hash(payload, label=label)

    if admission_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", "admission report SHA does not match admission"
        )
    if render_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", "render report admission SHA does not match admission"
        )
    if render_report.get("admission_report_sha256") != files["admission_report"]["sha256"]:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", "render report admission report SHA does not match report"
        )
    if render_audit_report.get("admission_sha256") != files["admission"]["sha256"]:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", "render audit admission SHA does not match admission"
        )
    if render_audit_report.get("admission_report_sha256") != files["admission_report"]["sha256"]:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", "render audit admission report SHA is inconsistent"
        )
    if render_audit_report.get("render_report_sha256") != files["render_report"]["sha256"]:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", "render audit render report SHA is inconsistent"
        )
    if render_report.get("admission_audit_report_sha256") != render_audit_report.get(
        "admission_audit_report_sha256"
    ):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "HASH_MISMATCH", "Node50/Node51 admission audit SHA declarations differ"
        )
    _validate_admission_inputs(admission_report.get("inputs"), root=root)

    path_bindings = (
        (render_report, "admission_path", "admission"),
        (render_report, "admission_report_path", "admission_report"),
        (render_audit_report, "admission_path", "admission"),
        (render_audit_report, "admission_report_path", "admission_report"),
        (render_audit_report, "render_report_path", "render_report"),
    )
    for payload, field, role in path_bindings:
        if payload.get(field) != files[role]["relative_path"]:
            raise MarketAwareSessionHistoryFinalReceiptError(
                "CHAIN_MISMATCH", f"{field} is inconsistent"
            )
    _validate_relative_path(
        render_audit_report.get("markdown_path"),
        root=root,
        label="render audit markdown_path",
    )
    if len({item["path"] for item in files.values()}) != len(files):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "PATH_INVALID", "receipt inputs must be distinct files"
        )

    status = admission.get("status")
    if status not in _STATUSES:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "STATUS_INVALID", "admission status is invalid"
        )
    if any(
        payload.get("status") != status
        for payload in (admission_report, render_report, render_audit_report)
    ):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "FIELD_MISMATCH", "receipt input statuses differ"
        )
    for field in ("admission_ready", "render_ready"):
        expected = admission.get(field)
        if not isinstance(expected, bool):
            raise MarketAwareSessionHistoryFinalReceiptError(
                "FIELD_INVALID", f"admission.{field} must be boolean"
            )
        if any(
            payload.get(field) is not expected
            for payload in (admission_report, render_report, render_audit_report)
        ):
            raise MarketAwareSessionHistoryFinalReceiptError(
                "FIELD_MISMATCH", f"{field} readiness differs"
            )
    audit_ready = render_audit_report.get("render_audit_ready")
    if audit_ready is not True or render_audit_report.get("admission_audit_ready") is not True:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "FIELD_MISMATCH", "render audit readiness is invalid"
        )
    if admission.get("issues") != admission_report.get("issues"):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "FIELD_MISMATCH", "admission/report issues differ"
        )
    if render_report.get("issues") != admission.get("issues"):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "FIELD_MISMATCH", "render report issues differ"
        )
    if render_audit_report.get("issues") != admission.get("issues"):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "FIELD_MISMATCH", "render audit issues differ"
        )
    if render_report.get("admission_audit_report_path") != render_audit_report.get(
        "admission_audit_report_path"
    ):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "CHAIN_MISMATCH", "Node50/Node51 admission audit paths differ"
        )
    for field in ("symbol", "package_count", "first_as_of", "last_as_of"):
        for payload, label in (
            (admission_report, "admission report"),
            (render_report, "render report"),
            (render_audit_report, "render audit report"),
        ):
            if payload.get(field) != admission.get(field):
                raise MarketAwareSessionHistoryFinalReceiptError(
                    "FIELD_MISMATCH", f"{label}.{field} differs"
                )
    if not isinstance(admission.get("symbol"), str) or not admission.get("symbol"):
        raise MarketAwareSessionHistoryFinalReceiptError(
            "FIELD_INVALID", "admission.symbol is required"
        )
    if not isinstance(admission.get("package_count"), int) or admission.get("package_count") < 0:
        raise MarketAwareSessionHistoryFinalReceiptError(
            "FIELD_INVALID", "admission.package_count is invalid"
        )
    _parse_datetime(admission.get("first_as_of"), label="admission.first_as_of")
    _parse_datetime(admission.get("last_as_of"), label="admission.last_as_of")
    receipt_ready = (
        admission.get("admission_ready") is True
        and render_report.get("render_ready") is True
        and audit_ready is True
    )
    return {
        "admission_ready": admission["admission_ready"],
        "audit_ready": audit_ready,
        "first_as_of": admission["first_as_of"],
        "last_as_of": admission["last_as_of"],
        "package_count": admission["package_count"],
        "receipt_ready": receipt_ready,
        "render_ready": render_report["render_ready"],
        "status": status,
        "symbol": admission["symbol"],
    }


def _base_report() -> dict[str, Any]:
    return {
        "admission_ready": False,
        "artifact_root": ".",
        "audit_ready": False,
        "decision_ready": False,
        "first_as_of": None,
        "inputs": {},
        "issues": [],
        "last_as_of": None,
        "output_sha256": None,
        "package_count": 0,
        "receipt_ready": False,
        "receipt_sha256": None,
        "receipt_version": MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
        "render_ready": False,
        "status": "invalid",
        "symbol": None,
    }


def _finalize(payload: dict[str, Any]) -> bytes:
    canonical = dict(payload)
    canonical["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    return _json_bytes(payload)


def build_market_aware_session_history_final_receipt(
    *,
    admission_path: Path,
    admission_report_path: Path,
    render_report_path: Path,
    render_audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic final receipt from Nodes48, 50, and 51."""

    report = _base_report()
    can_write_report = False
    receipt_path = output_dir / "market_aware_session_history_final_receipt.json"
    report_path = output_dir / "market_aware_session_history_final_receipt_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryFinalReceiptError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryFinalReceiptError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "receipt output is outside artifact root"
            ) from exc
        can_write_report = True
        files = {
            "admission": _safe_file(admission_path, root=root, label="admission"),
            "admission_report": _safe_file(
                admission_report_path, root=root, label="admission report"
            ),
            "render_report": _safe_file(
                render_report_path, root=root, label="render report"
            ),
            "render_audit_report": _safe_file(
                render_audit_report_path,
                root=root,
                label="render audit report",
            ),
        }
        payloads = {
            role: _read_json(files[role]["raw"], label=role.replace("_", " "))
            for role in _INPUT_ROLES
        }
        summary = _validate_chain(files=files, root=root, **payloads)
        receipt = {
            "admission_ready": summary["admission_ready"],
            "admission_report_sha256": files["admission_report"]["sha256"],
            "admission_sha256": files["admission"]["sha256"],
            "audit_ready": summary["audit_ready"],
            "decision_ready": False,
            "first_as_of": summary["first_as_of"],
            "issues": list(payloads["admission"].get("issues") or []),
            "last_as_of": summary["last_as_of"],
            "output_sha256": None,
            "package_count": summary["package_count"],
            "receipt_ready": summary["receipt_ready"],
            "receipt_version": MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
            "render_audit_report_sha256": files["render_audit_report"]["sha256"],
            "render_ready": summary["render_ready"],
            "render_report_sha256": files["render_report"]["sha256"],
            "status": summary["status"],
            "symbol": summary["symbol"],
        }
        write_atomic(receipt_path, _finalize(receipt))
        report.update(
            {
                **summary,
                "artifact_root": ".",
                "decision_ready": False,
                "inputs": {
                    role: {
                        "byte_count": files[role]["size_bytes"],
                        "path": files[role]["relative_path"],
                        "sha256": files[role]["sha256"],
                    }
                    for role in _INPUT_ROLES
                },
                "issues": list(payloads["admission"].get("issues") or []),
                "receipt_sha256": sha256_bytes(receipt_path.read_bytes()),
            }
        )
    except MarketAwareSessionHistoryFinalReceiptError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(report_path, _finalize(report))
    else:
        _finalize(report)
    return report
