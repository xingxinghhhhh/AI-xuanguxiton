"""Audit a Node81 startup-gate smoke receipt without rerunning the smoke."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_release import (
    DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME,
)
from .daily_research_service_release_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME,
)
from .daily_research_service_release_run_admission_startup_gate_smoke import (
    _REPORT_FIELDS as _SMOKE_REPORT_FIELDS,
)
from .daily_research_service_release_run_admission_startup_gate_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION,
)
from .daily_research_service_release_run_admission_startup_smoke_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
)
from .daily_research_service_release_run_admission_startup_smoke_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME,
)

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_VERSION = (
    "daily-research-service-release-run-admission-startup-gate-smoke-audit-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_REPORT_NAME = (
    "daily_research_service_release_run_admission_startup_gate_smoke_audit_report.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|(?:^|\s)/)")
_STATUS_VALUES = {"ready", "blocked", "failed", "invalid"}
_STARTUP_VALUES = _STATUS_VALUES | {"not_started"}
_PROBE_VALUES = {"not_started", "ready", "failed", "timeout", "service_exited"}
_STOP_VALUES = {"not_attempted", "controlled", "uncontrolled_exit", "failed"}
_AUDIT_FIELDS = {
    "audit_version",
    "smoke_version",
    "smoke_report_path",
    "smoke_report_sha256",
    "admission_path",
    "admission_sha256",
    "admission_report_path",
    "admission_report_sha256",
    "admission_audit_path",
    "admission_audit_sha256",
    "release_manifest_path",
    "release_manifest_sha256",
    "release_report_path",
    "release_report_sha256",
    "release_audit_report_path",
    "release_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "gate_status",
    "gate_ready",
    "startup_status",
    "service_started",
    "probe_status",
    "probe_exit_code",
    "stop_status",
    "service_stopped",
    "port_released",
    "smoke_status",
    "smoke_ready",
    "status",
    "audit_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceReleaseRunAdmissionStartupGateSmokeAuditError(ValueError):
    """A sanitized audit configuration or input failure."""

    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


class _AuditFailure(ValueError):
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
        raise _AuditFailure("HASH_INVALID", f"{label} is invalid")
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _AuditFailure("PATH_INVALID", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise _AuditFailure("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise _AuditFailure("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _safe_file(
    value: Any, *, root: Path, label: str, expected_name: str
) -> tuple[str, str]:
    try:
        if isinstance(value, Path):
            candidate = value.resolve()
            relative = candidate.relative_to(root).as_posix()
        else:
            relative = _relative(value, label=label)
            candidate = (root / PureWindowsPath(relative)).resolve()
        actual_relative = candidate.relative_to(root).as_posix()
        raw = candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise _AuditFailure("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    if actual_relative != relative or not candidate.is_file():
        raise _AuditFailure("PATH_INVALID", f"{label} is outside artifact root")
    if candidate.name != expected_name:
        raise _AuditFailure("FILENAME_INVALID", f"{label} filename is invalid")
    return relative, sha256_bytes(raw)


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise _AuditFailure("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _enum(value: Any, allowed: set[str], *, label: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise _AuditFailure("FIELD_INVALID", f"{label} is invalid")


def _issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, dict)
        or set(item) != {"code", "message"}
        or not isinstance(item["code"], str)
        or not isinstance(item["message"], str)
        for item in value
    ):
        raise _AuditFailure("FIELD_INVALID", f"{label} is invalid")


def _identity(payload: Mapping[str, Any]) -> None:
    if (
        payload["smoke_status"] in {"blocked", "failed"}
        and payload["gate_status"] in {"blocked", "failed"}
        and payload["gate_ready"] is False
        and all(
        payload[field] is None for field in ("symbol", "as_of", "evaluation_at")
        )
    ):
        return
    if not isinstance(payload["symbol"], str) or not payload["symbol"].strip():
        raise _AuditFailure("IDENTITY_INVALID", "symbol is invalid")
    parsed: dict[str, datetime] = {}
    for field in ("as_of", "evaluation_at"):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise _AuditFailure("IDENTITY_INVALID", f"{field} is invalid")
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _AuditFailure("IDENTITY_INVALID", f"{field} is invalid") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise _AuditFailure("IDENTITY_INVALID", f"{field} is invalid")
        parsed[field] = timestamp
    if parsed["as_of"] > parsed["evaluation_at"]:
        raise _AuditFailure("IDENTITY_INVALID", "as_of is later than evaluation_at")


def _validate_fields(payload: Mapping[str, Any]) -> None:
    for field in (
        "gate_ready",
        "service_started",
        "service_stopped",
        "port_released",
        "smoke_ready",
        "decision_ready",
    ):
        if not isinstance(payload[field], bool):
            raise _AuditFailure("FIELD_INVALID", f"{field} is invalid")
    _enum(payload["gate_status"], _STATUS_VALUES, label="gate_status")
    _enum(payload["startup_status"], _STARTUP_VALUES, label="startup_status")
    _enum(payload["probe_status"], _PROBE_VALUES, label="probe_status")
    _enum(payload["stop_status"], _STOP_VALUES, label="stop_status")
    _enum(payload["smoke_status"], _STATUS_VALUES, label="smoke_status")
    if payload["probe_exit_code"] is not None and (
        isinstance(payload["probe_exit_code"], bool)
        or not isinstance(payload["probe_exit_code"], int)
        or payload["probe_exit_code"] < 0
    ):
        raise _AuditFailure("FIELD_INVALID", "probe_exit_code is invalid")
    if payload["decision_ready"] is not False:
        raise _AuditFailure("DECISION_GATE_INVALID", "decision_ready must be false")
    _issues(payload["issues"], label="issues")
    _identity(payload)


def _validate_state(payload: Mapping[str, Any]) -> None:
    issues = payload["issues"]
    status = payload["smoke_status"]
    if status == "ready":
        valid = (
            payload["gate_status"] == "ready"
            and payload["gate_ready"] is True
            and payload["startup_status"] == "ready"
            and payload["service_started"] is True
            and payload["probe_status"] == "ready"
            and payload["probe_exit_code"] == 0
            and payload["stop_status"] == "controlled"
            and payload["service_stopped"] is True
            and payload["port_released"] is True
            and payload["smoke_status"] == "ready"
            and payload["smoke_ready"] is True
            and not issues
        )
    elif status == "blocked":
        valid = (
            payload["gate_status"] == "blocked"
            and payload["gate_ready"] is False
            and payload["startup_status"] == "not_started"
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
            and payload["port_released"] is False
            and payload["smoke_status"] == "blocked"
            and payload["smoke_ready"] is False
            and bool(issues)
        )
    elif status == "failed":
        gate_failure = (
            payload["gate_status"] == "failed"
            and payload["gate_ready"] is False
            and payload["startup_status"] == "not_started"
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
            and payload["port_released"] is False
            and payload["smoke_status"] == "failed"
            and payload["smoke_ready"] is False
        )
        startup_failure = (
            payload["startup_status"] == "failed"
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
            and payload["port_released"] is False
        )
        probe_failure = (
            payload["startup_status"] == "ready"
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
                (
                    payload["stop_status"] == "controlled"
                    and payload["service_stopped"] is True
                )
                or (
                    payload["stop_status"] in {"uncontrolled_exit", "failed"}
                    and payload["service_stopped"] is False
                )
            )
        )
        stop_failure = (
            payload["startup_status"] == "ready"
            and payload["service_started"] is True
            and payload["probe_status"] == "ready"
            and payload["probe_exit_code"] == 0
            and payload["stop_status"] == "failed"
            and payload["service_stopped"] is False
            and payload["port_released"] is False
        )
        port_failure = (
            payload["startup_status"] == "ready"
            and payload["service_started"] is True
            and payload["probe_status"] == "ready"
            and payload["probe_exit_code"] == 0
            and payload["stop_status"] == "controlled"
            and payload["service_stopped"] is True
            and payload["port_released"] is False
        )
        runtime_failure = (
            payload["gate_status"] == "ready"
            and payload["gate_ready"] is True
            and payload["smoke_status"] == "failed"
            and payload["smoke_ready"] is False
            and (startup_failure or probe_failure or stop_failure or port_failure)
        )
        valid = bool(issues) and (gate_failure or runtime_failure)
    else:
        valid = (
            payload["gate_status"] == "invalid"
            and payload["gate_ready"] is False
            and payload["startup_status"] == "not_started"
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
            and payload["port_released"] is False
            and payload["smoke_status"] == "invalid"
            and payload["smoke_ready"] is False
            and bool(issues)
        )
    if not valid:
        raise _AuditFailure("STATE_MISMATCH", "smoke runtime state is invalid")


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": (
            DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_VERSION
        ),
        "smoke_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION,
        "smoke_report_path": None,
        "smoke_report_sha256": None,
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
        "gate_status": "invalid",
        "gate_ready": False,
        "startup_status": "not_started",
        "service_started": False,
        "probe_status": "not_started",
        "probe_exit_code": None,
        "stop_status": "not_attempted",
        "service_stopped": False,
        "port_released": False,
        "smoke_status": "invalid",
        "smoke_ready": False,
        "status": "invalid",
        "audit_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> None:
    canonical = dict(report)
    canonical["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(
            output_dir
            / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_REPORT_NAME,
            _json_bytes(report),
        )
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeAuditError(
            "OUTPUT_UNAVAILABLE", "audit report output is unavailable", configuration=True
        ) from exc


def audit_daily_research_service_release_run_admission_startup_gate_smoke(
    *, smoke_report_path: Path, artifact_root: Path, output_dir: Path
) -> tuple[dict[str, Any], int]:
    """Audit a Node81 receipt and its declared files without starting anything."""

    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    try:
        output = output_dir.resolve()
        output.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir escapes artifact root", configuration=True
        ) from exc
    if output.exists() and not output.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeAuditError(
            "OUTPUT_DIR_INVALID", "output-dir is invalid", configuration=True
        )
    result = _base_report()
    try:
        smoke_relative, smoke_sha = _safe_file(
            smoke_report_path,
            root=root,
            label="smoke report",
            expected_name=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
        )
        smoke_raw = (root / PureWindowsPath(smoke_relative)).read_bytes()
        if _ABSOLUTE_PATH_RE.search(smoke_raw.decode("utf-8")):
            raise _AuditFailure("SENSITIVE_OUTPUT", "smoke report contains an absolute path")
        try:
            smoke = json.loads(smoke_raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise _AuditFailure("INPUT_INVALID", "smoke report is invalid") from exc
        if not isinstance(smoke, dict) or set(smoke) != _SMOKE_REPORT_FIELDS:
            raise _AuditFailure("SCHEMA_INVALID", "smoke report fields are invalid")
        _self_hash(smoke, label="smoke report")
        if (
            smoke["smoke_version"]
            != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION
        ):
            raise _AuditFailure("VERSION_MISMATCH", "smoke version is invalid")
        _validate_fields(smoke)
        _validate_state(smoke)
        result.update(
            {
                "smoke_report_path": smoke_relative,
                "smoke_report_sha256": smoke_sha,
                "symbol": smoke["symbol"],
                "as_of": smoke["as_of"],
                "evaluation_at": smoke["evaluation_at"],
                "gate_status": smoke["gate_status"],
                "gate_ready": smoke["gate_ready"],
                "startup_status": smoke["startup_status"],
                "service_started": smoke["service_started"],
                "probe_status": smoke["probe_status"],
                "probe_exit_code": smoke["probe_exit_code"],
                "stop_status": smoke["stop_status"],
                "service_stopped": smoke["service_stopped"],
                "port_released": smoke["port_released"],
                "smoke_status": smoke["smoke_status"],
                "smoke_ready": smoke["smoke_ready"],
                "decision_ready": False,
                "status": "ready" if smoke["smoke_status"] == "ready" else smoke["smoke_status"],
                "issues": list(smoke["issues"]),
            }
        )
        fields = {
            "admission_path": (
                DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
                "admission manifest",
            ),
            "admission_report_path": (
                DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
                "admission report",
            ),
            "admission_audit_path": (
                DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME,
                "admission audit",
            ),
            "release_manifest_path": (
                DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME,
                "release manifest",
            ),
            "release_report_path": (DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME, "release report"),
            "release_audit_report_path": (
                DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME,
                "release audit report",
            ),
        }
        bound_count = 0
        for field, (filename, label) in fields.items():
            value = smoke[field]
            sha_field = field.replace("_path", "_sha256")
            if value is None and smoke[sha_field] is None:
                continue
            if value is None or smoke[sha_field] is None:
                raise _AuditFailure("PATH_INVALID", f"{label} binding is incomplete")
            relative, actual_sha = _safe_file(
                value, root=root, label=label, expected_name=filename
            )
            declared_sha = _sha(smoke[sha_field], label=f"smoke report.{sha_field}")
            if relative != value or actual_sha != declared_sha:
                raise _AuditFailure("CHAIN_MISMATCH", f"{label} binding differs")
            result[field] = relative
            result[sha_field] = actual_sha
            bound_count += 1
        if smoke["smoke_status"] == "ready" and bound_count != len(fields):
            raise _AuditFailure("PATH_INVALID", "ready smoke report has incomplete input bindings")
        result["audit_ready"] = True
        result["status"] = smoke["smoke_status"]
        _write_report(result, output_dir=output)
        return result, 0 if result["status"] == "ready" else 1
    except _AuditFailure as exc:
        result["status"] = exc.status
        result["issues"] = [_issue(exc.code, str(exc))]
        result["audit_ready"] = False
        result["decision_ready"] = False
        _write_report(result, output_dir=output)
        return result, 1


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupGateSmokeAuditError",
    "audit_daily_research_service_release_run_admission_startup_gate_smoke",
]
