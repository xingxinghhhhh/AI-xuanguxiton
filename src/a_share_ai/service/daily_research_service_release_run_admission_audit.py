"""Audit the internal consistency of a Node73 admission pair."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_release import DAILY_RESEARCH_SERVICE_RELEASE_VERSION
from .daily_research_service_release_run import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION,
)
from .daily_research_service_release_run_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
)
from .daily_research_service_release_run_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION,
)
from .daily_research_service_release_startup import DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION = (
    "daily-research-service-release-run-admission-audit-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME = (
    "daily_research_service_release_run_admission_audit_report.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_ADMISSION_FIELDS = {
    "admission_version",
    "run_version",
    "release_version",
    "startup_version",
    "audit_version",
    "run_report_path",
    "run_report_sha256",
    "run_audit_report_path",
    "run_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "startup_status",
    "startup_ready",
    "probe_status",
    "probe_exit_code",
    "stop_status",
    "service_stopped",
    "run_status",
    "run_ready",
    "audit_ready",
    "status",
    "admission_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_REPORT_FIELDS = _ADMISSION_FIELDS | {"admission_path", "admission_sha256"}
_AUDIT_FIELDS = {
    "audit_version",
    "admission_version",
    "run_version",
    "release_version",
    "startup_version",
    "admission_path",
    "admission_sha256",
    "report_path",
    "report_sha256",
    "run_report_path",
    "run_report_sha256",
    "run_audit_report_path",
    "run_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "startup_status",
    "startup_ready",
    "probe_status",
    "probe_exit_code",
    "stop_status",
    "service_stopped",
    "run_status",
    "run_ready",
    "audit_ready",
    "status",
    "admission_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceReleaseRunAdmissionAuditError(ValueError):
    """A sanitized admission-audit configuration or artifact error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": str(message)}


def _enum(value: Any, allowed: set[str], *, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "FIELD_MISMATCH", f"{label} is invalid"
        )
    return value


def _optional_enum(value: Any, allowed: set[str], *, label: str) -> str | None:
    if value is None:
        return None
    return _enum(value, allowed, label=label)


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "FIELD_MISMATCH", f"{label} is invalid"
        )
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is invalid"
        )
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is invalid"
        )
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is not normalized"
        )
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "JSON_INVALID", f"{label} must be an object"
        )
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "SELF_HASH_MISMATCH", f"{label} self-hash differs"
        )


def _time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "TIME_MISMATCH", f"{label} is invalid"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "TIME_MISMATCH", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "TIME_MISMATCH", f"{label} needs timezone"
        )
    return parsed


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "FIELD_MISMATCH", f"{label} is invalid"
        )
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "FIELD_MISMATCH", f"{label} is invalid"
            )
        result.append(_issue(item["code"], item["message"]))
    return result


def _validate_admission(admission: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(admission) != _ADMISSION_FIELDS:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "UNKNOWN_FIELD", "admission fields are invalid"
        )
    _self_hash(admission, label="admission")
    if admission["admission_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "VERSION_MISMATCH", "admission version is invalid"
        )
    if admission["decision_ready"] is not False:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "DECISION_GATE_INVALID", "admission decision_ready must be false"
        )
    for field in (
        "audit_ready",
        "admission_ready",
        "startup_ready",
        "service_stopped",
        "run_ready",
    ):
        if not isinstance(admission[field], bool):
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "FIELD_MISMATCH", f"admission {field} is invalid"
            )
    _enum(admission["status"], {"ready", "blocked", "failed", "invalid"}, label="admission status")
    _enum(
        admission["run_status"],
        {"ready", "blocked", "failed", "invalid"},
        label="admission run_status",
    )
    invalid = admission["status"] == "invalid"
    for field, expected in (
        ("run_version", DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION),
        ("release_version", DAILY_RESEARCH_SERVICE_RELEASE_VERSION),
        ("startup_version", DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION),
        ("audit_version", DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION),
    ):
        if (not invalid and admission[field] != expected) or (
            invalid and admission[field] is not None and admission[field] != expected
        ):
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "VERSION_MISMATCH", f"admission {field} is invalid"
            )
    _optional_enum(
        admission["startup_status"],
        {"ready", "blocked", "failed"},
        label="admission startup_status",
    )
    _optional_enum(
        admission["probe_status"],
        {"not_started", "ready", "failed", "timeout", "service_exited"},
        label="admission probe_status",
    )
    _optional_enum(
        admission["stop_status"],
        {"not_attempted", "controlled", "uncontrolled_exit", "failed"},
        label="admission stop_status",
    )
    if admission["probe_exit_code"] is not None and (
        isinstance(admission["probe_exit_code"], bool)
        or not isinstance(admission["probe_exit_code"], int)
        or admission["probe_exit_code"] < 0
    ):
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "FIELD_MISMATCH", "admission probe_exit_code is invalid"
        )
    for field in ("run_report_path", "run_audit_report_path"):
        if invalid and admission[field] is None:
            continue
        _relative(admission[field], label=f"admission {field}")
    for field in ("run_report_sha256", "run_audit_report_sha256"):
        if invalid and admission[field] is None:
            continue
        _sha(admission[field], label=f"admission {field}")
    if admission["symbol"] is not None and (
        not isinstance(admission["symbol"], str) or not admission["symbol"].strip()
    ):
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "FIELD_MISMATCH", "admission symbol is invalid"
        )
    if not invalid:
        if not isinstance(admission["symbol"], str) or not admission["symbol"].strip():
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "FIELD_MISMATCH", "admission symbol is invalid"
            )
        as_of = _time(admission["as_of"], label="admission as_of")
        evaluation_at = _time(admission["evaluation_at"], label="admission evaluation_at")
        if as_of > evaluation_at:
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "TIME_MISMATCH", "admission as_of is after evaluation_at"
            )
    elif admission["as_of"] is not None or admission["evaluation_at"] is not None:
        as_of = _time(admission["as_of"], label="admission as_of")
        evaluation_at = _time(admission["evaluation_at"], label="admission evaluation_at")
        if as_of > evaluation_at:
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "TIME_MISMATCH", "admission as_of is after evaluation_at"
            )
    return _issues(admission["issues"], label="admission issues")


def _validate_report(
    report: Mapping[str, Any], admission: Mapping[str, Any], *, admission_file: Mapping[str, Any]
) -> None:
    if set(report) != _REPORT_FIELDS:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "UNKNOWN_FIELD", "admission report fields are invalid"
        )
    _self_hash(report, label="admission report")
    if report["decision_ready"] is not False:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "DECISION_GATE_INVALID", "admission report decision_ready must be false"
        )
    if report["admission_path"] != admission_file["relative_path"]:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "CHAIN_MISMATCH", "report admission_path differs"
        )
    if report["admission_sha256"] != admission_file["sha256"]:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "HASH_MISMATCH", "report admission_sha256 differs"
        )
    for field in _ADMISSION_FIELDS - {"output_sha256"}:
        if report[field] != admission[field]:
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "STATE_MISMATCH", f"report {field} differs"
            )


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
        "admission_version": None,
        "run_version": None,
        "release_version": None,
        "startup_version": None,
        "admission_path": None,
        "admission_sha256": None,
        "report_path": None,
        "report_sha256": None,
        "run_report_path": None,
        "run_report_sha256": None,
        "run_audit_report_path": None,
        "run_audit_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "startup_status": None,
        "startup_ready": False,
        "probe_status": None,
        "probe_exit_code": None,
        "stop_status": None,
        "service_stopped": False,
        "run_status": "invalid",
        "run_ready": False,
        "audit_ready": False,
        "status": "invalid",
        "admission_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_hashed(payload: dict[str, Any], path: Path) -> bytes:
    canonical = dict(payload)
    canonical["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    raw = _json_bytes(payload)
    write_atomic(path, raw)
    return raw


def _finalize(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_hashed(
            report,
            output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
        )
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "OUTPUT_UNAVAILABLE", "audit output is unavailable"
        ) from exc
    return report


def audit_daily_research_service_release_run_admission(
    *, admission_path: Path, report_path: Path, artifact_root: Path, output_dir: Path
) -> dict[str, Any]:
    """Audit Node73's two artifacts without consulting upstream evidence."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionAuditError(
            "OUTPUT_DIR_INVALID", "output directory escapes artifact root"
        ) from exc
    result = _base_report()
    try:
        admission_file = _safe_file(admission_path, root=root, label="admission")
        report_file = _safe_file(report_path, root=root, label="admission report")
        if admission_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME:
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "SCHEMA_INVALID", "admission filename is invalid"
            )
        if report_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME:
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "SCHEMA_INVALID", "admission report filename is invalid"
            )
        admission = _read_json(admission_file["raw"], label="admission")
        report = _read_json(report_file["raw"], label="admission report")
        admission_issues = _validate_admission(admission)
        _validate_report(report, admission, admission_file=admission_file)
        status = admission["status"]
        expected_ready = (
            admission["startup_status"] == "ready"
            and admission["startup_ready"] is True
            and admission["probe_status"] == "ready"
            and admission["probe_exit_code"] == 0
            and admission["stop_status"] == "controlled"
            and admission["service_stopped"] is True
            and admission["run_status"] == "ready"
            and admission["run_ready"] is True
            and admission["audit_ready"] is True
            and admission["admission_ready"] is True
            and not admission_issues
        )
        if status == "ready":
            if not expected_ready:
                raise DailyResearchServiceReleaseRunAdmissionAuditError(
                    "STATE_MISMATCH", "ready admission state is inconsistent"
                )
        elif status in {"blocked", "failed"}:
            if (
                admission["audit_ready"] is not True
                or admission["admission_ready"] is not False
                or admission["run_status"] != status
                or not admission_issues
            ):
                raise DailyResearchServiceReleaseRunAdmissionAuditError(
                    "STATE_MISMATCH", "blocked or failed admission state is inconsistent"
                )
        elif (
            admission["audit_ready"] is not False
            or admission["admission_ready"] is not False
            or admission["run_status"] != "invalid"
            or not admission_issues
        ):
            raise DailyResearchServiceReleaseRunAdmissionAuditError(
                "STATE_MISMATCH", "invalid admission state is inconsistent"
            )
        result.update(
            {
                "admission_version": admission["admission_version"],
                "run_version": admission["run_version"],
                "release_version": admission["release_version"],
                "startup_version": admission["startup_version"],
                "admission_path": admission_file["relative_path"],
                "admission_sha256": admission_file["sha256"],
                "report_path": report_file["relative_path"],
                "report_sha256": report_file["sha256"],
                "run_report_path": admission["run_report_path"],
                "run_report_sha256": admission["run_report_sha256"],
                "run_audit_report_path": admission["run_audit_report_path"],
                "run_audit_report_sha256": admission["run_audit_report_sha256"],
                "symbol": admission["symbol"],
                "as_of": admission["as_of"],
                "evaluation_at": admission["evaluation_at"],
                "startup_status": admission["startup_status"],
                "startup_ready": admission["startup_ready"],
                "probe_status": admission["probe_status"],
                "probe_exit_code": admission["probe_exit_code"],
                "stop_status": admission["stop_status"],
                "service_stopped": admission["service_stopped"],
                "run_status": admission["run_status"],
                "run_ready": admission["run_ready"],
                "audit_ready": admission["audit_ready"],
                "status": status,
                "admission_ready": admission["admission_ready"],
                "issues": admission_issues,
            }
        )
    except DailyResearchServiceReleaseRunAdmissionAuditError as exc:
        result["issues"] = [_issue(exc.code, str(exc))]
    return _finalize(result, output_dir=output_dir)


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME",
    "DailyResearchServiceReleaseRunAdmissionAuditError",
    "audit_daily_research_service_release_run_admission",
]
