"""Build a read-only admission summary from Node76 and Node77 receipts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_release import DAILY_RESEARCH_SERVICE_RELEASE_VERSION
from .daily_research_service_release_run import DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION
from .daily_research_service_release_run_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
)
from .daily_research_service_release_run_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
)
from .daily_research_service_release_run_admission_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION,
)
from .daily_research_service_release_run_admission_startup_smoke import (
    _REPORT_FIELDS as _NODE76_FIELDS,
)
from .daily_research_service_release_run_admission_startup_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION,
)
from .daily_research_service_release_run_admission_startup_smoke_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_VERSION,
)

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_VERSION = (
    "daily-research-service-release-run-admission-startup-smoke-admission-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME = (
    "daily_research_service_release_run_admission_startup_smoke_admission.json"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME = (
    "daily_research_service_release_run_admission_startup_smoke_admission_report.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|(?:^|\s)/)")
_NODE77_FIELDS = {
    "audit_version",
    "smoke_version",
    "run_version",
    "startup_version",
    "admission_version",
    "audit_input_version",
    "release_version",
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
    "release_audit_path",
    "release_audit_sha256",
    "preflight_report_path",
    "preflight_report_sha256",
    "startup_report_path",
    "startup_report_sha256",
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
    "status",
    "run_ready",
    "audit_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_MANIFEST_FIELDS = {
    "admission_version",
    "smoke_report_path",
    "smoke_report_sha256",
    "smoke_audit_report_path",
    "smoke_audit_report_sha256",
    "smoke_version",
    "audit_version",
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
    "audit_ready",
    "status",
    "admission_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_REPORT_FIELDS = _MANIFEST_FIELDS | {"admission_path", "admission_sha256"}
_STATUS_VALUES = {"ready", "blocked", "failed", "invalid"}
_PROBE_VALUES = {"not_started", "ready", "failed", "timeout", "service_exited"}
_STOP_VALUES = {"not_attempted", "controlled", "uncontrolled_exit", "failed"}
_LINK_FIELDS = (
    "startup_version",
    "admission_version",
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
    "preflight_report_path",
    "preflight_report_sha256",
    "startup_report_path",
    "startup_report_sha256",
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
    "run_ready",
    "decision_ready",
)


class DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionError(ValueError):
    """A sanitized admission configuration or receipt failure."""

    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


class _AdmissionFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _enum(value: Any, allowed: set[str], *, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _AdmissionFailure("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _AdmissionFailure("HASH_MISMATCH", f"{label} is invalid")
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _AdmissionFailure("PATH_INVALID", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise _AdmissionFailure("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise _AdmissionFailure("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _safe_file(path: Path, *, root: Path, label: str, filename: str) -> tuple[str, bytes, str]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root).as_posix()
        raw = candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise _AdmissionFailure("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    if not candidate.is_file():
        raise _AdmissionFailure("INPUT_UNAVAILABLE", f"{label} is unavailable")
    if candidate.name != filename:
        raise _AdmissionFailure("FILENAME_INVALID", f"{label} filename is invalid")
    return relative, raw, sha256_bytes(raw)


def _read_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _AdmissionFailure("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise _AdmissionFailure("INPUT_INVALID", f"{label} must be an object")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise _AdmissionFailure("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _AdmissionFailure("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise _AdmissionFailure("FIELD_MISMATCH", f"{label} is invalid")
        result.append({"code": item["code"], "message": item["message"]})
    return result


def _validate_input_types(payload: Mapping[str, Any], *, fields: set[str], label: str) -> None:
    if set(payload) != fields:
        raise _AdmissionFailure("SCHEMA_INVALID", f"{label} fields are invalid")
    _self_hash(payload, label=label)
    if payload["decision_ready"] is not False:
        raise _AdmissionFailure("DECISION_GATE_INVALID", f"{label} decision gate is invalid")
    for field in ("startup_ready", "service_started", "service_stopped", "run_ready"):
        if not isinstance(payload[field], bool):
            raise _AdmissionFailure("FIELD_MISMATCH", f"{label}.{field} is invalid")
    _enum(payload["status"], _STATUS_VALUES, label=f"{label}.status")
    _enum(payload["startup_status"], _STATUS_VALUES, label=f"{label}.startup_status")
    _enum(payload["admission_status"], _STATUS_VALUES, label=f"{label}.admission_status")
    _enum(payload["audit_status"], _STATUS_VALUES, label=f"{label}.audit_status")
    _enum(payload["probe_status"], _PROBE_VALUES, label=f"{label}.probe_status")
    _enum(payload["stop_status"], _STOP_VALUES, label=f"{label}.stop_status")
    if payload["probe_exit_code"] is not None and (
        isinstance(payload["probe_exit_code"], bool)
        or not isinstance(payload["probe_exit_code"], int)
        or payload["probe_exit_code"] < 0
    ):
        raise _AdmissionFailure("FIELD_MISMATCH", f"{label}.probe_exit_code is invalid")
    _issues(payload["issues"], label=f"{label}.issues")


def _base_manifest() -> dict[str, Any]:
    return {
        "admission_version": (
            DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_VERSION
        ),
        "smoke_report_path": None,
        "smoke_report_sha256": None,
        "smoke_audit_report_path": None,
        "smoke_audit_report_sha256": None,
        "smoke_version": None,
        "audit_version": None,
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
        "audit_ready": False,
        "status": "invalid",
        "admission_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_pair(
    manifest: dict[str, Any], *, output_dir: Path, root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = (
            output_dir
            / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME
        )
        canonical_manifest = dict(manifest)
        canonical_manifest["output_sha256"] = None
        manifest["output_sha256"] = sha256_bytes(_json_bytes(canonical_manifest))
        write_atomic(manifest_path, _json_bytes(manifest))
        manifest_relative = manifest_path.resolve().relative_to(root).as_posix()
        manifest_sha = sha256_bytes(manifest_path.read_bytes())
        report = dict(manifest)
        report["admission_path"] = manifest_relative
        report["admission_sha256"] = manifest_sha
        report["output_sha256"] = None
        report["output_sha256"] = sha256_bytes(_json_bytes(report))
        write_atomic(
            output_dir
            / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
            _json_bytes(report),
        )
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionError(
            "OUTPUT_UNAVAILABLE", "admission output is unavailable", configuration=True
        ) from exc
    return manifest, report


def _rooted_dir(path: Path, *, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root", configuration=True
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionError(
            "OUTPUT_DIR_INVALID", f"{label} is invalid", configuration=True
        )
    return resolved


def build_daily_research_service_release_run_admission_startup_smoke_admission(
    *, smoke_report_path: Path, smoke_audit_report_path: Path, artifact_root: Path, output_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Build a Node76/Node77 read-only admission summary without rerunning either node."""

    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    output = _rooted_dir(output_dir, root=root, label="output-dir")
    manifest = _base_manifest()
    try:
        smoke_relative, smoke_raw, smoke_sha = _safe_file(
            smoke_report_path,
            root=root,
            label="smoke report",
            filename=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME,
        )
        audit_relative, audit_raw, audit_sha = _safe_file(
            smoke_audit_report_path,
            root=root,
            label="smoke audit report",
            filename=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME,
        )
        for raw, label in ((smoke_raw, "smoke report"), (audit_raw, "smoke audit report")):
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise _AdmissionFailure("INPUT_INVALID", f"{label} is invalid") from exc
            if _ABSOLUTE_PATH_RE.search(text):
                raise _AdmissionFailure("SENSITIVE_OUTPUT", f"{label} contains an absolute path")
        smoke = _read_object(smoke_raw, label="smoke report")
        audit = _read_object(audit_raw, label="smoke audit report")
        _validate_input_types(smoke, fields=_NODE76_FIELDS, label="smoke report")
        if set(audit) != _NODE77_FIELDS:
            raise _AdmissionFailure("SCHEMA_INVALID", "smoke audit report fields are invalid")
        _self_hash(audit, label="smoke audit report")
        if audit["decision_ready"] is not False:
            raise _AdmissionFailure("DECISION_GATE_INVALID", "smoke audit decision gate is invalid")
        for field in (
            "startup_ready",
            "service_started",
            "service_stopped",
            "run_ready",
            "audit_ready",
        ):
            if not isinstance(audit[field], bool):
                raise _AdmissionFailure("FIELD_MISMATCH", f"smoke audit {field} is invalid")
        _enum(audit["status"], _STATUS_VALUES, label="smoke audit status")
        _enum(audit["startup_status"], _STATUS_VALUES, label="smoke audit startup_status")
        _enum(audit["probe_status"], _PROBE_VALUES, label="smoke audit probe_status")
        _enum(audit["stop_status"], _STOP_VALUES, label="smoke audit stop_status")
        _issues(audit["issues"], label="smoke audit issues")
        expected_versions = {
            "run_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION,
            "startup_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION,
            "admission_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
            "audit_input_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
            "release_version": DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
        }
        for field, expected in expected_versions.items():
            if audit[field] != expected:
                raise _AdmissionFailure("VERSION_MISMATCH", f"smoke audit {field} is invalid")
        if (
            smoke["run_version"]
            != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION
        ):
            raise _AdmissionFailure("VERSION_MISMATCH", "smoke version is invalid")
        if (
            audit["audit_version"]
            != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_VERSION
        ):
            raise _AdmissionFailure("VERSION_MISMATCH", "smoke audit version is invalid")
        if audit["smoke_version"] != smoke["run_version"]:
            raise _AdmissionFailure("VERSION_MISMATCH", "smoke versions differ")
        if (
            audit["smoke_report_path"] != smoke_relative
            or audit["smoke_report_sha256"] != smoke_sha
        ):
            raise _AdmissionFailure("CHAIN_MISMATCH", "smoke report reference differs")
        for field in _LINK_FIELDS:
            if audit[field] != smoke[field]:
                raise _AdmissionFailure("CHAIN_MISMATCH", f"smoke audit {field} differs")
        if audit["status"] != smoke["status"] or audit["issues"] != smoke["issues"]:
            raise _AdmissionFailure("STATE_MISMATCH", "smoke and audit status differs")
        if audit["run_ready"] != smoke["run_ready"]:
            raise _AdmissionFailure("STATE_MISMATCH", "smoke and audit readiness differs")
        if audit["status"] == "ready":
            if not (
                audit["audit_ready"] is True
                and audit["run_ready"] is True
                and audit["startup_status"] == "ready"
                and audit["startup_ready"] is True
                and audit["service_started"] is True
                and audit["probe_status"] == "ready"
                and audit["probe_exit_code"] == 0
                and audit["stop_status"] == "controlled"
                and audit["service_stopped"] is True
                and not audit["issues"]
            ):
                raise _AdmissionFailure("STATE_MISMATCH", "ready admission state is inconsistent")
        elif audit["status"] in {"blocked", "failed"}:
            if not (
                audit["audit_ready"] is True
                and audit["run_ready"] is False
                and audit["issues"]
            ):
                raise _AdmissionFailure(
                    "STATE_MISMATCH", "non-ready admission state is inconsistent"
                )
        else:
            if (
                audit["audit_ready"] is not False
                or audit["run_ready"] is not False
                or not audit["issues"]
            ):
                raise _AdmissionFailure(
                    "STATE_MISMATCH", "invalid admission state is inconsistent"
                )
        manifest.update(
            {
                "smoke_report_path": smoke_relative,
                "smoke_report_sha256": smoke_sha,
                "smoke_audit_report_path": audit_relative,
                "smoke_audit_report_sha256": audit_sha,
                "smoke_version": smoke["run_version"],
                "audit_version": audit["audit_version"],
                "symbol": smoke["symbol"],
                "as_of": smoke["as_of"],
                "evaluation_at": smoke["evaluation_at"],
                "startup_status": smoke["startup_status"],
                "startup_ready": smoke["startup_ready"],
                "service_started": smoke["service_started"],
                "probe_status": smoke["probe_status"],
                "probe_exit_code": smoke["probe_exit_code"],
                "stop_status": smoke["stop_status"],
                "service_stopped": smoke["service_stopped"],
                "smoke_status": smoke["status"],
                "audit_status": audit["status"],
                "run_ready": audit["run_ready"],
                "audit_ready": audit["audit_ready"],
                "status": audit["status"],
                "admission_ready": audit["status"] == "ready",
                "issues": audit["issues"],
            }
        )
        manifest, report = _write_pair(manifest, output_dir=output, root=root)
        return manifest, report, 0 if manifest["status"] == "ready" else 1
    except _AdmissionFailure as exc:
        manifest["issues"] = [_issue(exc.code, str(exc))]
        manifest["status"] = "invalid"
        manifest["smoke_status"] = "invalid"
        manifest["audit_status"] = "invalid"
        manifest["audit_ready"] = False
        manifest["admission_ready"] = False
        manifest, report = _write_pair(manifest, output_dir=output, root=root)
        return manifest, report, 1


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupSmokeAdmissionError",
    "build_daily_research_service_release_run_admission_startup_smoke_admission",
]
