"""Bind the Node73/74 run admission chain to the existing read-only service."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_release_run_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
)
from .daily_research_service_release_run_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
)
from .daily_research_service_release_startup import (
    DailyResearchServiceReleaseStartupError,
    load_daily_research_service_release_startup,
)
from .read_only_receipt_server import (
    ReadOnlyReceiptServiceError,
    create_read_only_receipt_server,
)

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION = (
    "daily-research-service-release-run-admission-startup-v2"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME = (
    "daily_research_service_release_run_admission_startup_report.json"
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
_REPORT_FIELDS_OUT = {
    "startup_version",
    "admission_version",
    "audit_version",
    "release_version",
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
    "release_audit_path",
    "release_audit_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "admission_status",
    "admission_ready",
    "audit_status",
    "audit_ready",
    "startup_status",
    "startup_ready",
    "mode",
    "service_started",
    "status",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceReleaseRunAdmissionStartupError(ValueError):
    """A sanitized configuration or admission-startup validation error."""

    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


@dataclass(frozen=True)
class DailyResearchServiceReleaseRunAdmissionStartupConfig:
    """Validated values needed to start the existing receipt service."""

    artifact_root: Path
    output_dir: Path
    admission: dict[str, Any]
    audit: dict[str, Any]
    admission_path: Path
    report_path: Path
    audit_path: Path
    admission_sha256: str
    report_sha256: str
    audit_sha256: str
    release_startup: Any


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _output_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": str(message)}


def _error(code: str, message: str) -> DailyResearchServiceReleaseRunAdmissionStartupError:
    return DailyResearchServiceReleaseRunAdmissionStartupError(code, message)


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _error("HASH_MISMATCH", f"{label} is invalid")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise _error("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise _error("PATH_OUTSIDE_ROOT", f"{label} is outside artifact root") from exc
    if not candidate.is_file():
        raise _error("INPUT_UNAVAILABLE", f"{label} is unavailable")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise _error("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _error("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise _error("INPUT_INVALID", f"{label} must be an object")
    return value


def _relative(value: Any, *, label: str, allow_none: bool = False) -> str | None:
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _error("PATH_INVALID", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise _error("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise _error("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _time(value: Any, *, label: str, allow_none: bool = False) -> datetime | None:
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _error("FIELD_MISMATCH", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _error("FIELD_MISMATCH", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _error("FIELD_MISMATCH", f"{label} needs timezone")
    return parsed


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _error("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise _error("FIELD_MISMATCH", f"{label} is invalid")
        result.append(_issue(item["code"], item["message"]))
    return result


def _bool_fields(payload: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if not isinstance(payload[field], bool):
            raise _error("FIELD_MISMATCH", f"{label}.{field} is invalid")


def _enum(value: Any, allowed: set[str], *, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _error("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _validate_admission(
    admission_file: Mapping[str, Any], report_file: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    admission = _read_json(admission_file["raw"], label="admission")
    report = _read_json(report_file["raw"], label="admission report")
    if set(admission) != _ADMISSION_FIELDS:
        raise _error("SCHEMA_INVALID", "admission fields are invalid")
    if set(report) != _REPORT_FIELDS:
        raise _error("SCHEMA_INVALID", "admission report fields are invalid")
    _self_hash(admission, label="admission")
    _self_hash(report, label="admission report")
    if admission["admission_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION:
        raise _error("VERSION_MISMATCH", "admission version is invalid")
    if admission["decision_ready"] is not False or report["decision_ready"] is not False:
        raise _error("DECISION_GATE_INVALID", "admission decision_ready must be false")
    _bool_fields(
        admission,
        ("startup_ready", "service_stopped", "run_ready", "audit_ready", "admission_ready"),
        label="admission",
    )
    _enum(admission["status"], {"ready", "blocked", "failed", "invalid"}, label="admission status")
    _enum(
        admission["run_status"],
        {"ready", "blocked", "failed", "invalid"},
        label="admission run_status",
    )
    invalid = admission["status"] == "invalid"
    expected_versions = {
        "run_version": "daily-research-service-release-run-v1",
        "release_version": "daily-research-service-release-v1",
        "startup_version": "daily-research-service-release-startup-v1",
        "audit_version": "daily-research-service-release-run-audit-v1",
    }
    for field, expected in expected_versions.items():
        if (not invalid and admission[field] != expected) or (
            invalid and admission[field] is not None and admission[field] != expected
        ):
            raise _error("VERSION_MISMATCH", f"admission {field} is invalid")
    status_enums = {
        "startup_status": {"ready", "blocked", "failed"},
        "probe_status": {"not_started", "ready", "failed", "timeout", "service_exited"},
        "stop_status": {"not_attempted", "controlled", "uncontrolled_exit", "failed"},
    }
    for field, allowed in status_enums.items():
        if admission[field] is None and invalid:
            continue
        _enum(admission[field], allowed, label=f"admission {field}")
    path_fields = ("run_report_path", "run_audit_report_path")
    sha_fields = ("run_report_sha256", "run_audit_report_sha256")
    for field in path_fields:
        _relative(admission[field], label=f"admission {field}", allow_none=invalid)
    for field in sha_fields:
        if invalid and admission[field] is None:
            continue
        _sha(admission[field], label=f"admission {field}")
    _bool_fields(
        report,
        ("startup_ready", "service_stopped", "run_ready", "audit_ready", "admission_ready"),
        label="admission report",
    )
    if report["admission_path"] != admission_file["relative_path"]:
        raise _error("CHAIN_MISMATCH", "admission report path differs")
    if report["admission_sha256"] != admission_file["sha256"]:
        raise _error("HASH_MISMATCH", "admission report SHA differs")
    for field in _ADMISSION_FIELDS - {"output_sha256"}:
        if report[field] != admission[field]:
            raise _error("STATE_MISMATCH", f"admission report {field} differs")
    if admission["symbol"] is not None and (
        not isinstance(admission["symbol"], str) or not admission["symbol"].strip()
    ):
        raise _error("FIELD_MISMATCH", "admission symbol is invalid")
    if admission["probe_exit_code"] is not None and (
        isinstance(admission["probe_exit_code"], bool)
        or not isinstance(admission["probe_exit_code"], int)
        or admission["probe_exit_code"] < 0
    ):
        raise _error("FIELD_MISMATCH", "admission probe_exit_code is invalid")
    if not invalid:
        if not isinstance(admission["symbol"], str) or not admission["symbol"].strip():
            raise _error("FIELD_MISMATCH", "admission symbol is invalid")
        as_of = _time(admission["as_of"], label="admission as_of")
        evaluation_at = _time(admission["evaluation_at"], label="admission evaluation_at")
        if as_of is not None and evaluation_at is not None and as_of > evaluation_at:
            raise _error("FIELD_MISMATCH", "admission as_of is after evaluation_at")
    else:
        as_of = _time(admission["as_of"], label="admission as_of", allow_none=True)
        evaluation_at = _time(
            admission["evaluation_at"], label="admission evaluation_at", allow_none=True
        )
        if as_of is not None and evaluation_at is not None and as_of > evaluation_at:
            raise _error("FIELD_MISMATCH", "admission as_of is after evaluation_at")
    admission_issues = _issues(admission["issues"], label="admission issues")
    if admission["status"] == "ready":
        if not (
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
        ):
            raise _error("STATE_MISMATCH", "ready admission state is inconsistent")
    elif admission["status"] in {"blocked", "failed"}:
        if not (
            admission["audit_ready"] is True
            and admission["admission_ready"] is False
            and admission["run_status"] == admission["status"]
            and admission_issues
        ):
            raise _error("STATE_MISMATCH", "blocked or failed admission state is inconsistent")
    elif not (
        admission["audit_ready"] is False
        and admission["admission_ready"] is False
        and admission["run_status"] == "invalid"
        and admission_issues
    ):
        raise _error("STATE_MISMATCH", "invalid admission state is inconsistent")
    return admission, report


def _validate_audit(
    audit_file: Mapping[str, Any],
    admission_file: Mapping[str, Any],
    report_file: Mapping[str, Any],
    admission: Mapping[str, Any],
) -> dict[str, Any]:
    audit = _read_json(audit_file["raw"], label="admission audit")
    if set(audit) != _AUDIT_FIELDS:
        raise _error("SCHEMA_INVALID", "admission audit fields are invalid")
    _self_hash(audit, label="admission audit")
    if audit["audit_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION:
        raise _error("VERSION_MISMATCH", "admission audit version is invalid")
    if audit["decision_ready"] is not False:
        raise _error("DECISION_GATE_INVALID", "admission audit decision_ready must be false")
    if audit["admission_path"] != admission_file["relative_path"]:
        raise _error("CHAIN_MISMATCH", "admission audit admission path differs")
    if audit["admission_sha256"] != admission_file["sha256"]:
        raise _error("HASH_MISMATCH", "admission audit admission SHA differs")
    if audit["report_path"] != report_file["relative_path"]:
        raise _error("CHAIN_MISMATCH", "admission audit report path differs")
    if audit["report_sha256"] != report_file["sha256"]:
        raise _error("HASH_MISMATCH", "admission audit report SHA differs")
    _bool_fields(
        audit,
        ("startup_ready", "service_stopped", "run_ready", "audit_ready", "admission_ready"),
        label="admission audit",
    )
    for field in _AUDIT_FIELDS & _ADMISSION_FIELDS - {"audit_version", "output_sha256"}:
        if audit[field] != admission[field]:
            raise _error("STATE_MISMATCH", f"admission audit {field} differs")
    _enum(
        audit["status"],
        {"ready", "blocked", "failed", "invalid"},
        label="admission audit status",
    )
    if audit["status"] == "ready" and not (
        audit["audit_ready"] is True and audit["admission_ready"] is True
    ):
        raise _error("STATE_MISMATCH", "ready admission audit state is inconsistent")
    if audit["status"] in {"blocked", "failed"} and not (
        audit["audit_ready"] is True and audit["admission_ready"] is False
    ):
        raise _error("STATE_MISMATCH", "blocked or failed admission audit is inconsistent")
    if audit["status"] == "invalid" and not (
        audit["audit_ready"] is False and audit["admission_ready"] is False and audit["issues"]
    ):
        raise _error("STATE_MISMATCH", "invalid admission audit state is inconsistent")
    _issues(audit["issues"], label="admission audit issues")
    return audit


def _validate_config(*, artifact_root: Path, output_dir: Path) -> tuple[Path, Path]:
    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    try:
        output = output_dir.resolve()
        output.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupError(
            "OUTPUT_DIR_INVALID", "output directory is outside artifact root", configuration=True
        ) from exc
    if output.exists() and not output.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupError(
            "OUTPUT_DIR_INVALID", "output directory is invalid", configuration=True
        )
    return root, output


def _write_report(report: dict[str, Any], *, output_dir: Path) -> bytes:
    canonical = dict(report)
    canonical["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_output_json_bytes(canonical))
    raw = _output_json_bytes(report)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(
            output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME, raw
        )
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupError(
            "OUTPUT_UNAVAILABLE", "startup report is unavailable", configuration=True
        ) from exc
    return raw


def _base_report(*, mode: str) -> dict[str, Any]:
    return {
        "startup_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION,
        "admission_version": None,
        "audit_version": None,
        "release_version": None,
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
        "release_audit_path": None,
        "release_audit_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "admission_status": "invalid",
        "admission_ready": False,
        "audit_status": "invalid",
        "audit_ready": False,
        "startup_status": "invalid",
        "startup_ready": False,
        "mode": mode,
        "service_started": False,
        "status": "invalid",
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _report_from_config(
    config: DailyResearchServiceReleaseRunAdmissionStartupConfig,
    *,
    mode: str,
    service_started: bool,
    status: str,
    startup_status: str,
    startup_ready: bool,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    admission = config.admission
    audit = config.audit
    release = config.release_startup
    if release is None:
        raise _error("STATE_MISMATCH", "release startup is unavailable")
    report = _base_report(mode=mode)
    report.update(
        {
            "admission_version": admission["admission_version"],
            "audit_version": audit["audit_version"],
            "release_version": admission["release_version"],
            "admission_path": config.admission_path.relative_to(config.artifact_root).as_posix(),
            "admission_sha256": config.admission_sha256,
            "admission_report_path": config.report_path.relative_to(
                config.artifact_root
            ).as_posix(),
            "admission_report_sha256": config.report_sha256,
            "admission_audit_path": config.audit_path.relative_to(config.artifact_root).as_posix(),
            "admission_audit_sha256": config.audit_sha256,
            "release_manifest_path": release.manifest_path.relative_to(
                config.artifact_root
            ).as_posix(),
            "release_manifest_sha256": release.manifest_sha256,
            "release_report_path": release.report_path.relative_to(
                config.artifact_root
            ).as_posix(),
            "release_report_sha256": release.report_sha256,
            "release_audit_path": release.audit_report_path.relative_to(
                config.artifact_root
            ).as_posix(),
            "release_audit_sha256": release.audit_sha256,
            "symbol": admission["symbol"],
            "as_of": admission["as_of"],
            "evaluation_at": admission["evaluation_at"],
            "admission_status": admission["status"],
            "admission_ready": admission["admission_ready"],
            "audit_status": audit["status"],
            "audit_ready": audit["audit_ready"],
            "startup_status": startup_status,
            "startup_ready": startup_ready,
            "service_started": service_started,
            "status": status,
            "issues": issues,
        }
    )
    return report


def _report_from_inputs(
    *,
    mode: str,
    admission_file: Mapping[str, Any] | None = None,
    report_file: Mapping[str, Any] | None = None,
    audit_file: Mapping[str, Any] | None = None,
    admission: Mapping[str, Any] | None = None,
    audit: Mapping[str, Any] | None = None,
    status: str = "invalid",
    issue: dict[str, str],
) -> dict[str, Any]:
    report = _base_report(mode=mode)
    if admission_file and admission:
        report.update(
            {
                "admission_version": admission.get("admission_version"),
                "audit_version": audit.get("audit_version") if audit else None,
                "release_version": admission.get("release_version"),
                "admission_path": admission_file["relative_path"],
                "admission_sha256": admission_file["sha256"],
                "symbol": admission.get("symbol"),
                "as_of": admission.get("as_of"),
                "evaluation_at": admission.get("evaluation_at"),
                "admission_status": admission.get("status", "invalid"),
                "admission_ready": admission.get("admission_ready") is True,
            }
        )
    if report_file:
        report["admission_report_path"] = report_file["relative_path"]
        report["admission_report_sha256"] = report_file["sha256"]
    if audit_file:
        report["admission_audit_path"] = audit_file["relative_path"]
        report["admission_audit_sha256"] = audit_file["sha256"]
    if audit:
        report["audit_status"] = audit.get("status", "invalid")
        report["audit_ready"] = audit.get("audit_ready") is True
    report["status"] = status
    report["startup_status"] = status
    report["issues"] = [issue]
    return report


def _load_config(
    *,
    admission_path: Path,
    report_path: Path,
    audit_path: Path,
    release_manifest_path: Path,
    release_report_path: Path,
    release_audit_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> DailyResearchServiceReleaseRunAdmissionStartupConfig:
    root, _ = _validate_config(artifact_root=artifact_root, output_dir=output_dir)
    admission_file = _safe_file(admission_path, root=root, label="admission")
    report_file = _safe_file(report_path, root=root, label="admission report")
    audit_file = _safe_file(audit_path, root=root, label="admission audit")
    if admission_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME:
        raise _error("SCHEMA_INVALID", "admission filename is invalid")
    if report_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME:
        raise _error("SCHEMA_INVALID", "admission report filename is invalid")
    if audit_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME:
        raise _error("SCHEMA_INVALID", "admission audit filename is invalid")
    admission, report = _validate_admission(admission_file, report_file)
    audit = _validate_audit(audit_file, admission_file, report_file, admission)
    if audit["status"] != admission["status"]:
        raise _error("STATE_MISMATCH", "admission and audit status differs")
    if admission["status"] != "ready" or audit["status"] != "ready":
        return DailyResearchServiceReleaseRunAdmissionStartupConfig(
            artifact_root=root,
            output_dir=output_dir.resolve(),
            admission=admission,
            audit=audit,
            admission_path=admission_file["path"],
            report_path=report_file["path"],
            audit_path=audit_file["path"],
            admission_sha256=admission_file["sha256"],
            report_sha256=report_file["sha256"],
            audit_sha256=audit_file["sha256"],
            release_startup=None,
        )
    try:
        release_startup = load_daily_research_service_release_startup(
            manifest_path=release_manifest_path,
            report_path=release_report_path,
            audit_report_path=release_audit_path,
            artifact_root=root,
        )
    except DailyResearchServiceReleaseStartupError as exc:
        raise _error(exc.code, "release startup admission is invalid") from exc
    if (
        admission["release_version"] != release_startup.release_version
        or admission["symbol"] != release_startup.symbol
        or admission["as_of"] != release_startup.as_of
        or admission["evaluation_at"] != release_startup.evaluation_at
        or admission["status"] != release_startup.status
        or admission["audit_ready"] != release_startup.audit_ready
        or admission["startup_ready"] != release_startup.startup_ready
        or admission["decision_ready"] != release_startup.decision_ready
        or release_startup.status != "ready"
        or release_startup.release_ready is not True
        or release_startup.audit_ready is not True
        or release_startup.startup_ready is not True
        or release_startup.decision_ready is not False
    ):
        raise _error("STATE_MISMATCH", "admission and release startup identity differs")
    return DailyResearchServiceReleaseRunAdmissionStartupConfig(
        artifact_root=root,
        output_dir=output_dir.resolve(),
        admission=admission,
        audit=audit,
        admission_path=admission_file["path"],
        report_path=report_file["path"],
        audit_path=audit_file["path"],
        admission_sha256=admission_file["sha256"],
        report_sha256=report_file["sha256"],
        audit_sha256=audit_file["sha256"],
        release_startup=release_startup,
    )


def _write_failure(
    *,
    output_dir: Path,
    mode: str,
    error: DailyResearchServiceReleaseRunAdmissionStartupError,
    admission_file: Mapping[str, Any] | None = None,
    report_file: Mapping[str, Any] | None = None,
    audit_file: Mapping[str, Any] | None = None,
    admission: Mapping[str, Any] | None = None,
    audit: Mapping[str, Any] | None = None,
    status: str = "invalid",
) -> bytes:
    report = _report_from_inputs(
        mode=mode,
        admission_file=admission_file,
        report_file=report_file,
        audit_file=audit_file,
        admission=admission,
        audit=audit,
        status=status,
        issue=_issue(error.code, str(error)),
    )
    return _write_report(report, output_dir=output_dir)


def run_daily_research_service_release_run_admission_startup(
    *,
    admission_path: Path,
    report_path: Path,
    audit_path: Path,
    release_manifest_path: Path,
    release_report_path: Path,
    release_audit_path: Path,
    artifact_root: Path,
    output_dir: Path,
    check_only: bool,
) -> tuple[int, bytes | None]:
    """Validate the complete chain and optionally run the existing service."""

    root, output = _validate_config(artifact_root=artifact_root, output_dir=output_dir)
    mode = "check_only" if check_only else "serve"
    try:
        config = _load_config(
            admission_path=admission_path,
            report_path=report_path,
            audit_path=audit_path,
            release_manifest_path=release_manifest_path,
            release_report_path=release_report_path,
            release_audit_path=release_audit_path,
            artifact_root=root,
            output_dir=output,
        )
    except DailyResearchServiceReleaseRunAdmissionStartupError as exc:
        raw = _write_failure(output_dir=output, mode=mode, error=exc)
        return 1, raw if check_only else None
    if config.release_startup is None:
        status = config.admission["status"]
        error = _error("ADMISSION_NOT_READY", "run admission is not ready")
        raw = _write_failure(
            output_dir=output,
            mode=mode,
            error=error,
            admission_file={
                "relative_path": config.admission_path.relative_to(root).as_posix(),
                "sha256": config.admission_sha256,
            },
            report_file={
                "relative_path": config.report_path.relative_to(root).as_posix(),
                "sha256": config.report_sha256,
            },
            audit_file={
                "relative_path": config.audit_path.relative_to(root).as_posix(),
                "sha256": config.audit_sha256,
            },
            admission=config.admission,
            audit=config.audit,
            status=status,
        )
        return 1, raw if check_only else None
    startup = config.release_startup
    gate = startup.gate_config
    if check_only:
        report = _report_from_config(
            config,
            mode="check_only",
            service_started=False,
            status="ready",
            startup_status="ready",
            startup_ready=True,
            issues=[],
        )
        return 0, _write_report(report, output_dir=output)
    try:
        server = create_read_only_receipt_server(
            receipt_path=gate.receipt_path,
            receipt_report_path=gate.receipt_report_path,
            artifact_root=gate.artifact_root,
            host=gate.host,
            port=gate.port,
            daily_admission_path=gate.daily_admission_path,
            daily_admission_report_path=gate.daily_admission_report_path,
            daily_admission_root=gate.artifact_root,
        )
    except ReadOnlyReceiptServiceError as exc:
        error = _error(exc.code, "read-only service startup failed")
        failure_report = _report_from_config(
            config,
            mode=mode,
            service_started=False,
            status="failed",
            startup_status="failed",
            startup_ready=False,
            issues=[_issue(error.code, str(error))],
        )
        raw = _write_report(failure_report, output_dir=output)
        return 1, None
    ready_report = _report_from_config(
        config,
        mode="serve",
        service_started=True,
        status="ready",
        startup_status="ready",
        startup_ready=True,
        issues=[],
    )
    try:
        _write_report(ready_report, output_dir=output)
    except DailyResearchServiceReleaseRunAdmissionStartupError as exc:
        server.server_close()
        print(f"{exc.code}: startup report is unavailable", file=sys.stderr)
        return 1, None
    try:
        print(
            f"read-only receipt service listening on http://{gate.host}:{gate.port}",
            file=sys.stderr,
        )
        server.serve_forever()
    finally:
        server.server_close()
    return 0, None


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME",
    "DailyResearchServiceReleaseRunAdmissionStartupConfig",
    "DailyResearchServiceReleaseRunAdmissionStartupError",
    "run_daily_research_service_release_run_admission_startup",
]
