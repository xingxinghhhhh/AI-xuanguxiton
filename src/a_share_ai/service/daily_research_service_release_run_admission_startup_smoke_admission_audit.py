"""Audit the Node78 startup-smoke admission pair without reading upstream data."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_release_run_admission_startup_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION,
)
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
from .daily_research_service_release_run_admission_startup_smoke_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_VERSION,
)

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_VERSION = (
    "daily-research-service-release-run-admission-startup-smoke-admission-audit-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME = (
    "daily_research_service_release_run_admission_startup_smoke_admission_audit_report.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|(?:^|\s)/)")
_STATUS_VALUES = {"ready", "blocked", "failed", "invalid"}
_PROBE_VALUES = {"not_started", "ready", "failed", "timeout", "service_exited"}
_STOP_VALUES = {"not_attempted", "controlled", "uncontrolled_exit", "failed"}
_AUDIT_FIELDS = {
    "audit_version",
    "admission_version",
    "smoke_version",
    "input_audit_version",
    "admission_path",
    "admission_sha256",
    "report_path",
    "report_sha256",
    "smoke_report_path",
    "smoke_report_sha256",
    "smoke_audit_report_path",
    "smoke_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
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
    "input_audit_ready",
    "status",
    "admission_ready",
    "audit_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionAuditError(ValueError):
    """A sanitized audit configuration or input receipt failure."""

    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


class _AuditFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _AuditFailure("HASH_MISMATCH", f"{label} is invalid")
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


def _enum(value: Any, allowed: set[str], *, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _AuditFailure("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _AuditFailure("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
            or _ABSOLUTE_PATH_RE.search(item["message"]) is not None
        ):
            raise _AuditFailure("FIELD_MISMATCH", f"{label} is invalid")
        result.append({"code": item["code"], "message": item["message"]})
    return result


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise _AuditFailure("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _read_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _AuditFailure("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise _AuditFailure("INPUT_INVALID", f"{label} must be an object")
    return value


def _safe_file(path: Path, *, root: Path, label: str, filename: str) -> tuple[str, bytes, str]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root).as_posix()
        raw = candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise _AuditFailure("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    if not candidate.is_file():
        raise _AuditFailure("INPUT_UNAVAILABLE", f"{label} is unavailable")
    if candidate.name != filename:
        raise _AuditFailure("FILENAME_INVALID", f"{label} filename is invalid")
    return relative, raw, sha256_bytes(raw)


def _validate_common(payload: Mapping[str, Any], *, fields: set[str], label: str) -> None:
    if set(payload) != fields:
        raise _AuditFailure("SCHEMA_INVALID", f"{label} fields are invalid")
    _self_hash(payload, label=label)
    if payload["decision_ready"] is not False:
        raise _AuditFailure("DECISION_GATE_INVALID", f"{label} decision gate is invalid")
    for field in (
        "startup_ready",
        "service_started",
        "service_stopped",
        "run_ready",
        "audit_ready",
        "admission_ready",
    ):
        if not isinstance(payload[field], bool):
            raise _AuditFailure("FIELD_MISMATCH", f"{label}.{field} is invalid")
    for field in ("status", "startup_status", "smoke_status", "audit_status"):
        _enum(payload[field], _STATUS_VALUES, label=f"{label}.{field}")
    _enum(payload["probe_status"], _PROBE_VALUES, label=f"{label}.probe_status")
    _enum(payload["stop_status"], _STOP_VALUES, label=f"{label}.stop_status")
    if payload["probe_exit_code"] is not None and (
        isinstance(payload["probe_exit_code"], bool)
        or not isinstance(payload["probe_exit_code"], int)
        or payload["probe_exit_code"] < 0
    ):
        raise _AuditFailure("FIELD_MISMATCH", f"{label}.probe_exit_code is invalid")
    for field in ("symbol", "as_of", "evaluation_at"):
        if payload[field] is not None and not isinstance(payload[field], str):
            raise _AuditFailure("FIELD_MISMATCH", f"{label}.{field} is invalid")
    _issues(payload["issues"], label=f"{label}.issues")


def _validate_manifest(payload: Mapping[str, Any], *, label: str) -> None:
    _validate_common(payload, fields=_ADMISSION_MANIFEST_FIELDS, label=label)
    if (
        payload["admission_version"]
        != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_VERSION
    ):
        raise _AuditFailure("VERSION_MISMATCH", f"{label}.admission_version is invalid")
    if (
        payload["smoke_version"]
        != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION
    ):
        raise _AuditFailure("VERSION_MISMATCH", f"{label}.smoke_version is invalid")
    if (
        payload["audit_version"]
        != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_VERSION
    ):
        raise _AuditFailure("VERSION_MISMATCH", f"{label}.audit_version is invalid")
    for field in ("smoke_report_path", "smoke_audit_report_path"):
        _relative(payload[field], label=f"{label}.{field}")
    for field in ("smoke_report_sha256", "smoke_audit_report_sha256"):
        _sha(payload[field], label=f"{label}.{field}")
    if not isinstance(payload["symbol"], str) or not payload["symbol"].strip():
        raise _AuditFailure("IDENTITY_INVALID", f"{label}.symbol is invalid")
    parsed_times: dict[str, datetime] = {}
    for field in ("as_of", "evaluation_at"):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise _AuditFailure("IDENTITY_INVALID", f"{label}.{field} is invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _AuditFailure("IDENTITY_INVALID", f"{label}.{field} is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise _AuditFailure("IDENTITY_INVALID", f"{label}.{field} is invalid")
        parsed_times[field] = parsed
    if parsed_times["as_of"] > parsed_times["evaluation_at"]:
        raise _AuditFailure("IDENTITY_INVALID", f"{label} time order is invalid")
    _validate_runtime_state(payload, label=label)


def _validate_runtime_state(payload: Mapping[str, Any], *, label: str) -> None:
    if payload["status"] == "ready":
        if not (
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
        ):
            raise _AuditFailure("STATE_MISMATCH", f"{label} ready state is invalid")
        return
    if payload["status"] == "blocked":
        if not (
            payload["smoke_status"] == payload["status"]
            and payload["audit_status"] == payload["status"]
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
            and payload["issues"]
        ):
            raise _AuditFailure("STATE_MISMATCH", f"{label} blocked state is invalid")
        return
    if payload["status"] == "failed":
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
                    and (payload["probe_exit_code"] is None or payload["probe_exit_code"] > 0)
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
        if not (
            payload["smoke_status"] == "failed"
            and payload["audit_status"] == "failed"
            and payload["run_ready"] is False
            and payload["audit_ready"] is True
            and payload["admission_ready"] is False
            and payload["issues"]
            and (admission_failure or probe_failure)
        ):
            raise _AuditFailure("STATE_MISMATCH", f"{label} failed state is invalid")
        return
    if not (
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
        and payload["issues"]
    ):
        raise _AuditFailure("STATE_MISMATCH", f"{label} invalid state is invalid")


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": (
            DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_VERSION
        ),
        "admission_version": None,
        "smoke_version": None,
        "input_audit_version": None,
        "admission_path": None,
        "admission_sha256": None,
        "report_path": None,
        "report_sha256": None,
        "smoke_report_path": None,
        "smoke_report_sha256": None,
        "smoke_audit_report_path": None,
        "smoke_audit_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "startup_status": "invalid",
        "startup_ready": False,
        "service_started": False,
        "probe_status": "not_started",
        "probe_exit_code": None,
        "stop_status": "not_attempted",
        "service_stopped": False,
        "smoke_status": "invalid",
        "audit_status": "invalid",
        "run_ready": False,
        "input_audit_ready": False,
        "status": "invalid",
        "admission_ready": False,
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
        audit_report_path = output_dir / (
            DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME
        )
        write_atomic(
            audit_report_path,
            _json_bytes(report),
        )
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionAuditError(
            "OUTPUT_UNAVAILABLE", "audit report output is unavailable", configuration=True
        ) from exc


def _rooted_dir(path: Path, *, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root", configuration=True
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionAuditError(
            "OUTPUT_DIR_INVALID", f"{label} is invalid", configuration=True
        )
    return resolved


def audit_daily_research_service_release_run_admission_startup_smoke_admission(
    *, admission_path: Path, report_path: Path, artifact_root: Path, output_dir: Path
) -> tuple[dict[str, Any], int]:
    """Audit Node78's manifest/report pair without reading any upstream receipt."""

    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    output = _rooted_dir(output_dir, root=root, label="output-dir")
    result = _base_report()
    try:
        admission_relative, admission_raw, admission_sha = _safe_file(
            admission_path,
            root=root,
            label="admission manifest",
            filename=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
        )
        report_relative, report_raw, report_sha = _safe_file(
            report_path,
            root=root,
            label="admission report",
            filename=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
        )
        for raw, label in ((admission_raw, "admission manifest"), (report_raw, "admission report")):
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise _AuditFailure("INPUT_INVALID", f"{label} is invalid") from exc
            if _ABSOLUTE_PATH_RE.search(text):
                raise _AuditFailure("SENSITIVE_OUTPUT", f"{label} contains an absolute path")
        manifest = _read_object(admission_raw, label="admission manifest")
        report = _read_object(report_raw, label="admission report")
        _validate_manifest(manifest, label="admission manifest")
        _validate_common(report, fields=_ADMISSION_REPORT_FIELDS, label="admission report")
        if (
            report["admission_path"] != admission_relative
            or report["admission_sha256"] != admission_sha
        ):
            raise _AuditFailure("CHAIN_MISMATCH", "report manifest reference differs")
        if report["admission_version"] != manifest["admission_version"]:
            raise _AuditFailure("VERSION_MISMATCH", "report admission version differs")
        if report["smoke_version"] != manifest["smoke_version"]:
            raise _AuditFailure("VERSION_MISMATCH", "report smoke version differs")
        if report["audit_version"] != manifest["audit_version"]:
            raise _AuditFailure("VERSION_MISMATCH", "report audit version differs")
        for field in _ADMISSION_MANIFEST_FIELDS - {"output_sha256"}:
            if report[field] != manifest[field]:
                raise _AuditFailure("CHAIN_MISMATCH", f"report {field} differs")
        result.update(
            {
                "admission_version": manifest["admission_version"],
                "smoke_version": manifest["smoke_version"],
                "input_audit_version": manifest["audit_version"],
                "admission_path": admission_relative,
                "admission_sha256": admission_sha,
                "report_path": report_relative,
                "report_sha256": report_sha,
                "smoke_report_path": manifest["smoke_report_path"],
                "smoke_report_sha256": manifest["smoke_report_sha256"],
                "smoke_audit_report_path": manifest["smoke_audit_report_path"],
                "smoke_audit_report_sha256": manifest["smoke_audit_report_sha256"],
                "symbol": manifest["symbol"],
                "as_of": manifest["as_of"],
                "evaluation_at": manifest["evaluation_at"],
                "startup_status": manifest["startup_status"],
                "startup_ready": manifest["startup_ready"],
                "service_started": manifest["service_started"],
                "probe_status": manifest["probe_status"],
                "probe_exit_code": manifest["probe_exit_code"],
                "stop_status": manifest["stop_status"],
                "service_stopped": manifest["service_stopped"],
                "smoke_status": manifest["smoke_status"],
                "audit_status": manifest["audit_status"],
                "run_ready": manifest["run_ready"],
                "input_audit_ready": manifest["audit_ready"],
                "status": manifest["status"],
                "admission_ready": manifest["admission_ready"],
                "audit_ready": True,
                "issues": manifest["issues"],
            }
        )
        _write_report(result, output_dir=output)
        return result, 0 if result["status"] == "ready" else 1
    except _AuditFailure as exc:
        result["issues"] = [_issue(exc.code, str(exc))]
        result["status"] = "invalid"
        result["input_audit_ready"] = False
        result["audit_ready"] = False
        result["admission_ready"] = False
        _write_report(result, output_dir=output)
        return result, 1


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionAuditError",
    "audit_daily_research_service_release_run_admission_startup_smoke_admission",
]
