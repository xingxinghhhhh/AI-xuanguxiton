"""Build a deterministic read-only admission summary for Nodes45-47."""

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
from .market_aware_session_history_closure_render_audit import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_AUDIT_VERSION,
)
from .market_aware_session_history_closure_renderer import (
    MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION,
)

MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION = (
    "market-aware-session-history-closure-admission-v1"
)
_ROLES = (
    "closure",
    "closure_report",
    "markdown",
    "render_report",
    "render_audit_report",
)
_STATUSES = {"blocked", "ready", "stale"}


class MarketAwareSessionHistoryClosureAdmissionError(ValueError):
    """A fail-closed admission error."""

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
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "INPUT_JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "INPUT_ROOT_INVALID", f"{label} must be an object"
        )
    return payload


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "INPUT_UNAVAILABLE", f"{label} is not a file"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionError(
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
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_INVALID", f"{label}.output_sha256 is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", f"{label}.output_sha256 is invalid"
        )


def _parse_datetime(value: Any, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "TIME_INVALID", f"{label} is required"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "TIME_INVALID", f"{label} must include timezone"
        )


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "ISSUES_INVALID", f"{label} must be a list"
        )
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MarketAwareSessionHistoryClosureAdmissionError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise MarketAwareSessionHistoryClosureAdmissionError(
                "ISSUES_INVALID", f"{label}[{index}] is invalid"
            )


def _validate_path(value: Any, *, expected: str, label: str) -> None:
    if value != expected:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "CHAIN_MISMATCH", f"{label} is inconsistent"
        )


def _validate_chain(
    *,
    files: Mapping[str, Mapping[str, Any]],
    closure: Mapping[str, Any],
    closure_report: Mapping[str, Any],
    render_report: Mapping[str, Any],
    render_audit_report: Mapping[str, Any],
) -> dict[str, Any]:
    if closure.get("closure_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "VERSION_MISMATCH", "closure version is invalid"
        )
    if closure_report.get("closure_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_VERSION:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "VERSION_MISMATCH", "closure report version is invalid"
        )
    if render_report.get("render_version") != MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_VERSION:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "VERSION_MISMATCH", "render report version is invalid"
        )
    if render_audit_report.get("audit_version") != (
        MARKET_AWARE_SESSION_HISTORY_CLOSURE_RENDER_AUDIT_VERSION
    ):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "VERSION_MISMATCH", "render audit report version is invalid"
        )
    for label, payload in (
        ("closure", closure),
        ("closure_report", closure_report),
        ("render_report", render_report),
        ("render_audit_report", render_audit_report),
    ):
        if payload.get("decision_ready") is not False:
            raise MarketAwareSessionHistoryClosureAdmissionError(
                "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=f"{label}.issues")
    _validate_self_hash(closure, label="closure")
    _validate_self_hash(closure_report, label="closure_report")
    _validate_self_hash(render_report, label="render_report")
    _validate_self_hash(render_audit_report, label="render_audit_report")
    if closure_report.get("closure_sha256") != files["closure"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "closure report SHA does not match closure"
        )
    if render_report.get("closure_sha256") != files["closure"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "render report closure SHA does not match closure"
        )
    if render_report.get("closure_report_sha256") != files["closure_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "render report closure report SHA does not match report"
        )
    if render_report.get("markdown_sha256") != files["markdown"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "render report Markdown SHA does not match Markdown"
        )
    if render_audit_report.get("closure_sha256") != files["closure"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "render audit closure SHA does not match closure"
        )
    if render_audit_report.get("closure_report_sha256") != files["closure_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "render audit closure report SHA does not match report"
        )
    if render_audit_report.get("markdown_sha256") != files["markdown"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "render audit Markdown SHA does not match Markdown"
        )
    if render_audit_report.get("render_report_sha256") != files["render_report"]["sha256"]:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "HASH_MISMATCH", "render audit render report SHA does not match report"
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
    _validate_path(
        render_audit_report.get("closure_path"),
        expected=files["closure"]["relative_path"],
        label="render audit closure_path",
    )
    _validate_path(
        render_audit_report.get("closure_report_path"),
        expected=files["closure_report"]["relative_path"],
        label="render audit closure_report_path",
    )
    _validate_path(
        render_audit_report.get("markdown_path"),
        expected=files["markdown"]["relative_path"],
        label="render audit markdown_path",
    )
    _validate_path(
        render_audit_report.get("render_report_path"),
        expected=files["render_report"]["relative_path"],
        label="render audit render_report_path",
    )
    if len({item["path"] for item in files.values()}) != len(files):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "PATH_INVALID", "admission inputs must be distinct files"
        )
    status = closure.get("status")
    if status not in _STATUSES:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "STATUS_INVALID", "closure status is invalid"
        )
    closure_ready = closure_report.get("closure_ready")
    if not isinstance(closure_ready, bool) or closure_ready is not (status == "ready"):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_MISMATCH", "closure status and closure_ready differ"
        )
    if closure_report.get("status") != status or render_report.get("status") != status:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_MISMATCH", "closure/report/render status differs"
        )
    if render_audit_report.get("closure_status") != status:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_MISMATCH", "render audit closure status differs"
        )
    render_ready = render_report.get("render_ready")
    audit_ready = render_audit_report.get("audit_ready")
    if render_ready is not closure_ready or audit_ready is not True:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_MISMATCH", "readiness fields are inconsistent"
        )
    for field in ("symbol", "package_count", "first_as_of", "last_as_of"):
        for payload, label in (
            (render_report, "render_report"),
            (render_audit_report, "render_audit_report"),
        ):
            if payload.get(field) != closure.get(field):
                raise MarketAwareSessionHistoryClosureAdmissionError(
                    "FIELD_MISMATCH", f"{label}.{field} differs"
                )
    if closure_report.get("issues") != closure.get("issues"):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_MISMATCH", "closure report issues differ"
        )
    if render_report.get("issues") != closure.get("issues"):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_MISMATCH", "render report issues differ"
        )
    _parse_datetime(closure.get("first_as_of"), label="closure.first_as_of")
    _parse_datetime(closure.get("last_as_of"), label="closure.last_as_of")
    if not isinstance(closure.get("symbol"), str) or not closure.get("symbol"):
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_INVALID", "closure.symbol is required"
        )
    if not isinstance(closure.get("package_count"), int) or closure.get("package_count") < 0:
        raise MarketAwareSessionHistoryClosureAdmissionError(
            "FIELD_INVALID", "closure.package_count is invalid"
        )
    return {
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
        "admission_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION,
        "artifact_root": ".",
        "closure_ready": False,
        "closure_status": "invalid",
        "decision_ready": False,
        "issues": [],
        "output_sha256": None,
        "render_ready": False,
        "status": "invalid",
    }


def _finalize(payload: dict[str, Any]) -> bytes:
    canonical = dict(payload)
    canonical["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    return _json_bytes(payload)


def build_market_aware_session_history_closure_admission(
    *,
    closure_path: Path,
    closure_report_path: Path,
    markdown_path: Path,
    render_report_path: Path,
    render_audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic admission summary from Nodes45-47."""

    report = _base_report()
    can_write_report = False
    admission_path = output_dir / "market_aware_session_history_closure_admission.json"
    report_path = output_dir / "market_aware_session_history_closure_admission_report.json"
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise MarketAwareSessionHistoryClosureAdmissionError(
                "ARTIFACT_ROOT_INVALID", "artifact root must be a directory"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise MarketAwareSessionHistoryClosureAdmissionError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "admission output is outside artifact root"
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
            "render_audit_report": _safe_file(
                render_audit_report_path, root=root, label="render audit report"
            ),
        }
        payloads = {
            "closure": _read_json(files["closure"]["raw"], label="closure"),
            "closure_report": _read_json(
                files["closure_report"]["raw"], label="closure report"
            ),
            "render_report": _read_json(
                files["render_report"]["raw"], label="render report"
            ),
            "render_audit_report": _read_json(
                files["render_audit_report"]["raw"], label="render audit report"
            ),
        }
        files["markdown"]["raw"].decode("utf-8")
        summary = _validate_chain(files=files, **payloads)
        issues = list(payloads["closure"].get("issues") or [])
        admission_ready = (
            summary["closure_status"] == "ready"
            and summary["closure_ready"] is True
            and summary["render_ready"] is True
            and summary["audit_ready"] is True
        )
        admission = {
            "admission_version": MARKET_AWARE_SESSION_HISTORY_CLOSURE_ADMISSION_VERSION,
            "admission_ready": admission_ready,
            "audit_ready": summary["audit_ready"],
            "closure_ready": summary["closure_ready"],
            "closure_report_sha256": files["closure_report"]["sha256"],
            "closure_sha256": files["closure"]["sha256"],
            "closure_status": summary["closure_status"],
            "decision_ready": False,
            "first_as_of": summary["first_as_of"],
            "issues": issues,
            "last_as_of": summary["last_as_of"],
            "output_sha256": None,
            "package_count": summary["package_count"],
            "render_audit_report_sha256": files["render_audit_report"]["sha256"],
            "render_ready": summary["render_ready"],
            "render_report_sha256": files["render_report"]["sha256"],
            "status": summary["closure_status"],
            "symbol": summary["symbol"],
        }
        write_atomic(admission_path, _finalize(admission))
        report.update(
            {
                "admission_ready": admission_ready,
                "audit_ready": summary["audit_ready"],
                "closure_ready": summary["closure_ready"],
                "closure_report_sha256": files["closure_report"]["sha256"],
                "closure_sha256": files["closure"]["sha256"],
                "closure_status": summary["closure_status"],
                "decision_ready": False,
                "first_as_of": summary["first_as_of"],
                "inputs": {
                    role: {
                        "byte_count": files[role]["size_bytes"],
                        "path": files[role]["relative_path"],
                        "sha256": files[role]["sha256"],
                    }
                    for role in _ROLES
                },
                "issues": issues,
                "last_as_of": summary["last_as_of"],
                "package_count": summary["package_count"],
                "render_ready": summary["render_ready"],
                "render_audit_report_sha256": files["render_audit_report"]["sha256"],
                "render_report_sha256": files["render_report"]["sha256"],
                "status": summary["closure_status"],
                "symbol": summary["symbol"],
            }
        )
        report["admission_sha256"] = sha256_bytes(admission_path.read_bytes())
    except MarketAwareSessionHistoryClosureAdmissionError as exc:
        report["issues"] = [{"code": exc.code, "message": str(exc)}]
    except (OSError, UnicodeError) as exc:
        report["issues"] = [{"code": "OUTPUT_INVALID", "message": str(exc)}]
    if can_write_report:
        write_atomic(report_path, _finalize(report))
    else:
        _finalize(report)
    return report
