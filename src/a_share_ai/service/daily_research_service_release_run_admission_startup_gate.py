"""Bind Node78/79 admission evidence to the existing loopback startup loader."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes
from .daily_research_service_release_run_admission_startup_smoke_admission import (
    _MANIFEST_FIELDS as _ADMISSION_MANIFEST_FIELDS,
)
from .daily_research_service_release_run_admission_startup_smoke_admission import (
    _REPORT_FIELDS as _ADMISSION_REPORT_FIELDS,
)
from .daily_research_service_release_run_admission_startup_smoke_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_VERSION,
)
from .daily_research_service_release_run_admission_startup_smoke_admission_audit import (
    _AUDIT_FIELDS as _ADMISSION_AUDIT_FIELDS,
)
from .daily_research_service_release_run_admission_startup_smoke_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_VERSION,
)
from .daily_research_service_release_startup import (
    DailyResearchServiceReleaseStartupConfig,
    DailyResearchServiceReleaseStartupError,
    load_daily_research_service_release_startup,
)

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_VERSION = (
    "daily-research-service-release-run-admission-startup-gate-v1"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|(?:^|\s)/)")
_STATUS_VALUES = {"ready", "blocked", "failed", "invalid"}
_PROBE_VALUES = {"not_started", "ready", "failed", "timeout", "service_exited"}
_STOP_VALUES = {"not_attempted", "controlled", "uncontrolled_exit", "failed"}


class DailyResearchServiceReleaseRunAdmissionStartupGateError(ValueError):
    """A sanitized gate configuration or input failure."""

    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


class _GateFailure(ValueError):
    def __init__(self, code: str, message: str, *, status: str = "invalid") -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _GateFailure("HASH_MISMATCH", f"{label} is invalid")
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _GateFailure("PATH_INVALID", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise _GateFailure("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise _GateFailure("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _safe_file(path: Path, *, root: Path, label: str, filename: str) -> tuple[str, bytes, str]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root).as_posix()
        raw = candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise _GateFailure("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    if not candidate.is_file() or candidate.name != filename:
        raise _GateFailure("FILENAME_INVALID", f"{label} filename is invalid")
    return relative, raw, sha256_bytes(raw)


def _read_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _GateFailure("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise _GateFailure("INPUT_INVALID", f"{label} must be an object")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise _GateFailure("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _enum(value: Any, allowed: set[str], *, label: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise _GateFailure("FIELD_MISMATCH", f"{label} is invalid")


def _issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, dict)
        or set(item) != {"code", "message"}
        or not isinstance(item["code"], str)
        or not isinstance(item["message"], str)
        for item in value
    ):
        raise _GateFailure("FIELD_MISMATCH", f"{label} is invalid")


def _validate_runtime_state(payload: Mapping[str, Any], *, label: str) -> None:
    for field in (
        "startup_ready",
        "service_started",
        "service_stopped",
        "run_ready",
        "audit_ready",
        "admission_ready",
    ):
        if not isinstance(payload[field], bool):
            raise _GateFailure("FIELD_MISMATCH", f"{label}.{field} is invalid")
    for field in ("status", "startup_status", "smoke_status", "audit_status"):
        _enum(payload[field], _STATUS_VALUES, label=f"{label}.{field}")
    _enum(payload["probe_status"], _PROBE_VALUES, label=f"{label}.probe_status")
    _enum(payload["stop_status"], _STOP_VALUES, label=f"{label}.stop_status")
    probe_exit_code = payload["probe_exit_code"]
    if probe_exit_code is not None and (
        isinstance(probe_exit_code, bool)
        or not isinstance(probe_exit_code, int)
        or probe_exit_code < 0
    ):
        raise _GateFailure("FIELD_MISMATCH", f"{label}.probe_exit_code is invalid")
    _issues(payload["issues"], label=f"{label}.issues")
    if payload["status"] == "ready":
        valid = (
            payload["smoke_status"] == "ready"
            and payload["audit_status"] == "ready"
            and payload["startup_status"] == "ready"
            and payload["startup_ready"] is True
            and payload["service_started"] is True
            and payload["probe_status"] == "ready"
            and payload["probe_exit_code"] == 0
            and payload["stop_status"] == "controlled"
            and payload["service_stopped"] is True
            and payload["run_ready"] is True
            and payload["audit_ready"] is True
            and payload["admission_ready"] is True
            and not payload["issues"]
        )
    elif payload["status"] == "blocked":
        valid = (
            payload["smoke_status"] == "blocked"
            and payload["audit_status"] == "blocked"
            and payload["startup_status"] == "blocked"
            and payload["startup_ready"] is False
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
            and payload["run_ready"] is False
            and payload["audit_ready"] is True
            and payload["admission_ready"] is False
            and bool(payload["issues"])
        )
    elif payload["status"] == "failed":
        admission_failure = (
            payload["startup_status"] == "failed"
            and payload["startup_ready"] is False
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
        )
        probe_failure = (
            payload["startup_status"] == "ready"
            and payload["startup_ready"] is True
            and payload["service_started"] is True
            and payload["probe_status"] in {"failed", "timeout", "service_exited"}
            and (
                (payload["probe_status"] == "timeout" and payload["probe_exit_code"] is None)
                or (
                    payload["probe_status"] == "failed"
                    and (
                        payload["probe_exit_code"] is None or payload["probe_exit_code"] > 0
                    )
                )
                or (
                    payload["probe_status"] == "service_exited"
                    and payload["probe_exit_code"] in {None, 0}
                )
            )
            and (
                (payload["stop_status"] == "controlled" and payload["service_stopped"] is True)
                or (
                    payload["stop_status"] in {"uncontrolled_exit", "failed"}
                    and payload["service_stopped"] is False
                )
            )
        )
        valid = (
            payload["smoke_status"] == "failed"
            and payload["audit_status"] == "failed"
            and payload["run_ready"] is False
            and payload["audit_ready"] is True
            and payload["admission_ready"] is False
            and bool(payload["issues"])
            and (admission_failure or probe_failure)
        )
    else:
        valid = (
            payload["smoke_status"] == "invalid"
            and payload["audit_status"] == "invalid"
            and payload["startup_status"] == "invalid"
            and payload["startup_ready"] is False
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
            and payload["run_ready"] is False
            and payload["audit_ready"] is False
            and payload["admission_ready"] is False
            and bool(payload["issues"])
        )
    if not valid:
        raise _GateFailure("STATE_MISMATCH", f"{label} runtime state is invalid")


def _validate_identity(payload: Mapping[str, Any], *, label: str) -> None:
    if not isinstance(payload["symbol"], str) or not payload["symbol"].strip():
        raise _GateFailure("IDENTITY_INVALID", f"{label}.symbol is invalid")
    parsed_times: dict[str, datetime] = {}
    for field in ("as_of", "evaluation_at"):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise _GateFailure("IDENTITY_INVALID", f"{label}.{field} is invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _GateFailure("IDENTITY_INVALID", f"{label}.{field} is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise _GateFailure("IDENTITY_INVALID", f"{label}.{field} is invalid")
        parsed_times[field] = parsed
    if parsed_times["as_of"] > parsed_times["evaluation_at"]:
        raise _GateFailure("IDENTITY_INVALID", f"{label} time order is invalid")


def _base_summary() -> dict[str, Any]:
    return {
        "startup_gate_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_VERSION,
        "admission_path": None,
        "admission_sha256": None,
        "admission_report_path": None,
        "admission_report_sha256": None,
        "admission_audit_path": None,
        "admission_audit_sha256": None,
        "release_manifest_path": None,
        "release_manifest_sha256": None,
        "release_report_path": None,
        "release_report_sha256": None,
        "release_audit_report_path": None,
        "release_audit_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "admission_status": "invalid",
        "admission_ready": False,
        "audit_status": "invalid",
        "audit_ready": False,
        "release_startup_status": "not_checked",
        "release_startup_ready": False,
        "bind_attempted": False,
        "service_started": False,
        "status": "invalid",
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _finalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    summary["output_sha256"] = None
    summary["output_sha256"] = sha256_bytes(_json_bytes(summary))
    return summary


def _validate_inputs(
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    audit: Mapping[str, Any],
    *,
    admission_relative: str,
    admission_sha: str,
    report_relative: str,
    report_sha: str,
    audit_relative: str,
    audit_sha: str,
) -> None:
    if set(manifest) != _ADMISSION_MANIFEST_FIELDS:
        raise _GateFailure("SCHEMA_INVALID", "admission manifest fields are invalid")
    if set(report) != _ADMISSION_REPORT_FIELDS:
        raise _GateFailure("SCHEMA_INVALID", "admission report fields are invalid")
    if set(audit) != _ADMISSION_AUDIT_FIELDS:
        raise _GateFailure("SCHEMA_INVALID", "admission audit fields are invalid")
    _self_hash(manifest, label="admission manifest")
    _self_hash(report, label="admission report")
    _self_hash(audit, label="admission audit")
    for payload, label in ((manifest, "admission manifest"), (report, "admission report")):
        if payload["decision_ready"] is not False:
            raise _GateFailure("DECISION_GATE_INVALID", f"{label} decision gate is invalid")
    if audit["decision_ready"] is not False:
        raise _GateFailure("DECISION_GATE_INVALID", "admission audit decision gate is invalid")
    _validate_runtime_state(manifest, label="admission manifest")
    _validate_runtime_state(report, label="admission report")
    _validate_runtime_state(audit, label="admission audit")
    if not isinstance(audit["input_audit_ready"], bool):
        raise _GateFailure("FIELD_MISMATCH", "admission audit.input_audit_ready is invalid")
    if audit["input_audit_ready"] != manifest["audit_ready"]:
        raise _GateFailure("CHAIN_MISMATCH", "admission audit input readiness differs")
    for field in (
        "startup_status",
        "startup_ready",
        "service_started",
        "probe_status",
        "probe_exit_code",
        "stop_status",
        "service_stopped",
        "smoke_status",
        "audit_status",
        "run_ready",
        "admission_ready",
        "audit_ready",
        "issues",
    ):
        if audit[field] != manifest[field]:
            raise _GateFailure("CHAIN_MISMATCH", f"admission audit {field} differs")
    _validate_identity(manifest, label="admission manifest")
    _validate_identity(report, label="admission report")
    _validate_identity(audit, label="admission audit")
    if (
        manifest["admission_version"]
        != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_VERSION
    ):
        raise _GateFailure("VERSION_MISMATCH", "admission version is invalid")
    if (
        audit["audit_version"]
        != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_VERSION
    ):
        raise _GateFailure("VERSION_MISMATCH", "admission audit version is invalid")
    if (
        report["admission_path"] != admission_relative
        or report["admission_sha256"] != admission_sha
    ):
        raise _GateFailure("CHAIN_MISMATCH", "admission report reference differs")
    if report["admission_version"] != manifest["admission_version"]:
        raise _GateFailure("CHAIN_MISMATCH", "admission report version differs")
    for field in _ADMISSION_MANIFEST_FIELDS - {"output_sha256"}:
        if report[field] != manifest[field]:
            raise _GateFailure("CHAIN_MISMATCH", f"admission report {field} differs")
    if audit["admission_path"] != admission_relative or audit["admission_sha256"] != admission_sha:
        raise _GateFailure("CHAIN_MISMATCH", "admission audit manifest reference differs")
    if audit["report_path"] != report_relative or audit["report_sha256"] != report_sha:
        raise _GateFailure("CHAIN_MISMATCH", "admission audit report reference differs")
    if audit["admission_version"] != manifest["admission_version"]:
        raise _GateFailure("CHAIN_MISMATCH", "admission audit version differs")
    if audit["status"] != manifest["status"]:
        raise _GateFailure("CHAIN_MISMATCH", "admission audit status differs")
    if audit["smoke_report_path"] != manifest["smoke_report_path"]:
        raise _GateFailure("CHAIN_MISMATCH", "smoke report path differs")
    if audit["smoke_report_sha256"] != manifest["smoke_report_sha256"]:
        raise _GateFailure("CHAIN_MISMATCH", "smoke report SHA differs")
    if audit["smoke_audit_report_path"] != manifest["smoke_audit_report_path"]:
        raise _GateFailure("CHAIN_MISMATCH", "smoke audit path differs")
    if audit["smoke_audit_report_sha256"] != manifest["smoke_audit_report_sha256"]:
        raise _GateFailure("CHAIN_MISMATCH", "smoke audit SHA differs")
    for field in ("smoke_report_path", "smoke_audit_report_path"):
        _relative(manifest[field], label=f"admission manifest.{field}")
    for field in ("smoke_report_sha256", "smoke_audit_report_sha256"):
        _sha(manifest[field], label=f"admission manifest.{field}")
    if (
        manifest["symbol"] != audit["symbol"]
        or manifest["as_of"] != audit["as_of"]
        or manifest["evaluation_at"] != audit["evaluation_at"]
    ):
        raise _GateFailure("IDENTITY_MISMATCH", "admission audit identity differs")
    if manifest["status"] == "invalid":
        raise _GateFailure("ADMISSION_INVALID", "admission pair is invalid")
    if manifest["status"] == "failed":
        raise _GateFailure("ADMISSION_FAILED", "admission pair failed", status="failed")
    if manifest["status"] == "blocked":
        raise _GateFailure("ADMISSION_NOT_READY", "admission pair is blocked", status="blocked")
    if manifest["admission_ready"] is not True or manifest["audit_ready"] is not True:
        raise _GateFailure("ADMISSION_NOT_READY", "admission readiness is false", status="blocked")
    if audit["admission_ready"] is not True or audit["audit_ready"] is not True:
        raise _GateFailure(
            "AUDIT_NOT_READY", "admission audit readiness is false", status="blocked"
        )
    if manifest["run_ready"] is not True or manifest["issues"]:
        raise _GateFailure(
            "ADMISSION_NOT_READY", "admission runtime is not ready", status="blocked"
        )


def check_daily_research_service_release_run_admission_startup_gate(
    *,
    admission_path: Path,
    admission_report_path: Path,
    admission_audit_path: Path,
    release_manifest_path: Path,
    release_report_path: Path,
    release_audit_report_path: Path,
    artifact_root: Path,
) -> tuple[dict[str, Any], int, DailyResearchServiceReleaseStartupConfig | None]:
    """Return a fail-closed gate summary and an existing startup config when ready."""

    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupGateError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    summary = _base_summary()
    try:
        admission_relative, admission_raw, admission_sha = _safe_file(
            admission_path,
            root=root,
            label="admission manifest",
            filename=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
        )
        report_relative, report_raw, report_sha = _safe_file(
            admission_report_path,
            root=root,
            label="admission report",
            filename=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
        )
        audit_relative, audit_raw, audit_sha = _safe_file(
            admission_audit_path,
            root=root,
            label="admission audit",
            filename=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME,
        )
        for raw, label in (
            (admission_raw, "admission manifest"),
            (report_raw, "admission report"),
            (audit_raw, "admission audit"),
        ):
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise _GateFailure("INPUT_INVALID", f"{label} is invalid") from exc
            if _ABSOLUTE_PATH_RE.search(text):
                raise _GateFailure("SENSITIVE_OUTPUT", f"{label} contains an absolute path")
        manifest = _read_object(admission_raw, label="admission manifest")
        report = _read_object(report_raw, label="admission report")
        audit = _read_object(audit_raw, label="admission audit")
        _validate_inputs(
            manifest,
            report,
            audit,
            admission_relative=admission_relative,
            admission_sha=admission_sha,
            report_relative=report_relative,
            report_sha=report_sha,
            audit_relative=audit_relative,
            audit_sha=audit_sha,
        )
        summary.update(
            {
                "admission_path": admission_relative,
                "admission_sha256": admission_sha,
                "admission_report_path": report_relative,
                "admission_report_sha256": report_sha,
                "admission_audit_path": audit_relative,
                "admission_audit_sha256": audit_sha,
                "symbol": manifest["symbol"],
                "as_of": manifest["as_of"],
                "evaluation_at": manifest["evaluation_at"],
                "admission_status": manifest["status"],
                "admission_ready": manifest["admission_ready"],
                "audit_status": audit["status"],
                "audit_ready": audit["audit_ready"],
            }
        )
        try:
            startup = load_daily_research_service_release_startup(
                manifest_path=release_manifest_path,
                report_path=release_report_path,
                audit_report_path=release_audit_report_path,
                artifact_root=root,
            )
        except DailyResearchServiceReleaseStartupError as exc:
            status = "failed" if exc.code in {"RELEASE_NOT_READY", "GATE_NOT_READY"} else "invalid"
            raise _GateFailure(
                "RELEASE_STARTUP_INVALID", "release startup chain is not ready", status=status
            ) from exc
        summary.update(
            {
                "release_manifest_path": startup.manifest_path.relative_to(root).as_posix(),
                "release_manifest_sha256": startup.manifest_sha256,
                "release_report_path": startup.report_path.relative_to(root).as_posix(),
                "release_report_sha256": startup.report_sha256,
                "release_audit_report_path": startup.audit_report_path.relative_to(root).as_posix(),
                "release_audit_report_sha256": startup.audit_sha256,
                "release_startup_status": startup.status,
                "release_startup_ready": startup.startup_ready,
                "status": "ready",
            }
        )
        return _finalize_summary(summary), 0, startup
    except _GateFailure as exc:
        summary["status"] = exc.status
        summary["issues"] = [_issue(exc.code, str(exc))]
        if exc.status == "blocked":
            summary["admission_ready"] = False
            summary["audit_ready"] = summary["audit_ready"] is True
        else:
            summary["admission_ready"] = False
            summary["audit_ready"] = False
        return _finalize_summary(summary), 1, None


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupGateError",
    "check_daily_research_service_release_run_admission_startup_gate",
]
