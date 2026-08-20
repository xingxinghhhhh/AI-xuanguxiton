"""Independently audit one Node76 release-startup smoke receipt offline."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_release import (
    _MANIFEST_FIELDS as _RELEASE_MANIFEST_FIELDS,
)
from .daily_research_service_release import (
    _REPORT_FIELDS as _RELEASE_REPORT_FIELDS,
)
from .daily_research_service_release import (
    DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
)
from .daily_research_service_release_audit import (
    _AUDIT_OUTPUT_FIELDS as _RELEASE_AUDIT_FIELDS,
)
from .daily_research_service_release_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME,
)
from .daily_research_service_release_run import DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION
from .daily_research_service_release_run_admission import (
    _ADMISSION_FIELDS as _ADMISSION_INPUT_FIELDS,
)
from .daily_research_service_release_run_admission import (
    _ADMISSION_REPORT_FIELDS,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
)
from .daily_research_service_release_run_admission_audit import (
    _AUDIT_FIELDS as _ADMISSION_AUDIT_FIELDS,
)
from .daily_research_service_release_run_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
)
from .daily_research_service_release_run_admission_startup import (
    _REPORT_FIELDS_OUT as _NODE75_REPORT_FIELDS,
)
from .daily_research_service_release_run_admission_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION,
)
from .daily_research_service_release_run_admission_startup_smoke import (
    _REPORT_FIELDS as _NODE76_REPORT_FIELDS,
)
from .daily_research_service_release_run_admission_startup_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION,
)

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_VERSION = (
    "daily-research-service-release-run-admission-startup-smoke-audit-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME = (
    "daily_research_service_release_run_admission_startup_smoke_audit_report.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|(?:^|\s)/)")
_INPUTS = {
    "admission": (
        "admission_path",
        "admission_sha256",
        DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
    ),
    "admission_report": (
        "admission_report_path",
        "admission_report_sha256",
        DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
    ),
    "admission_audit": (
        "admission_audit_path",
        "admission_audit_sha256",
        DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
    ),
    "release_manifest": (
        "release_manifest_path",
        "release_manifest_sha256",
        DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME,
    ),
    "release_report": (
        "release_report_path",
        "release_report_sha256",
        DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME,
    ),
    "release_audit": (
        "release_audit_path",
        "release_audit_sha256",
        DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME,
    ),
}
_CHAIN_FIELDS = (
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
    "audit_status",
)
_SMOKE_STATUS_FIELDS = (
    "admission_status",
    "audit_status",
    "startup_status",
    "startup_ready",
    "service_started",
    "probe_status",
    "probe_exit_code",
    "stop_status",
    "service_stopped",
    "status",
    "run_ready",
    "issues",
    "decision_ready",
)


class DailyResearchServiceReleaseRunAdmissionStartupSmokeAuditError(ValueError):
    """A sanitized audit configuration or output failure."""

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


def _pretty_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _enum(value: Any, allowed: set[str], *, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _AuditFailure("FIELD_MISMATCH", f"{label} is invalid")
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


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _AuditFailure("HASH_MISMATCH", f"{label} is invalid")
    return value


def _safe_file(path: Path, *, root: Path, label: str) -> tuple[Path, str, bytes, str]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root).as_posix()
        raw = candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise _AuditFailure("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    if not candidate.is_file():
        raise _AuditFailure("INPUT_UNAVAILABLE", f"{label} is unavailable")
    return candidate, relative, raw, sha256_bytes(raw)


def _declared_file(
    payload: Mapping[str, Any], *, key: str, root: Path, required: bool = True
) -> tuple[Path, str, bytes, str] | None:
    path_field, sha_field, filename = _INPUTS[key]
    if payload[path_field] is None and payload[sha_field] is None and not required:
        return None
    relative = _relative(payload[path_field], label=path_field)
    declared_sha = _sha(payload[sha_field], label=sha_field)
    candidate, actual_relative, raw, actual_sha = _safe_file(
        root / relative, root=root, label=path_field
    )
    if candidate.name != filename:
        raise _AuditFailure("FILENAME_INVALID", f"{path_field} filename is invalid")
    if relative != actual_relative or declared_sha != actual_sha:
        raise _AuditFailure("HASH_MISMATCH", f"{path_field} does not match the file")
    return candidate, actual_relative, raw, actual_sha


def _read_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _AuditFailure("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise _AuditFailure("INPUT_INVALID", f"{label} must be an object")
    return payload


def _self_hash(
    payload: Mapping[str, Any], *, label: str, canonical_bytes: Any = _json_bytes
) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(canonical_bytes(canonical)):
        raise _AuditFailure("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _validate_common_receipt(
    payload: Mapping[str, Any],
    *,
    fields: set[str],
    label: str,
    version_field: str,
    version: str,
    canonical_bytes: Any,
    statuses: set[str],
    booleans: tuple[str, ...],
) -> None:
    if set(payload) != fields:
        raise _AuditFailure("SCHEMA_INVALID", f"{label} fields are invalid")
    _self_hash(payload, label=label, canonical_bytes=canonical_bytes)
    if payload[version_field] != version:
        raise _AuditFailure("VERSION_MISMATCH", f"{label} version is invalid")
    if payload.get("decision_ready") is not False:
        raise _AuditFailure("DECISION_GATE_INVALID", f"{label} decision gate is invalid")
    for field in booleans:
        if not isinstance(payload[field], bool):
            raise _AuditFailure("FIELD_MISMATCH", f"{label}.{field} is invalid")
    if "status" in payload:
        _enum(payload["status"], statuses, label=f"{label}.status")
    if "issues" in payload:
        _issues(payload["issues"], label=f"{label}.issues")


def _validate_upstream_inputs(
    input_files: Mapping[str, tuple[Path, str, bytes, str]],
    *,
    smoke: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Independently validate every available Node73/74/68 input receipt."""

    missing = [key for key in _INPUTS if key not in input_files]
    if any(key not in input_files for key in ("admission", "admission_report", "admission_audit")):
        raise _AuditFailure("INPUT_UNAVAILABLE", "admission inputs are incomplete")

    values: dict[str, dict[str, Any]] = {}
    for key, (_, _, raw, _) in input_files.items():
        values[key] = _read_object(raw, label=key)

    admission = values["admission"]
    admission_report = values["admission_report"]
    admission_audit = values["admission_audit"]

    _validate_common_receipt(
        admission,
        fields=_ADMISSION_INPUT_FIELDS,
        label="admission",
        version_field="admission_version",
        version=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
        canonical_bytes=_pretty_json_bytes,
        statuses={"ready", "blocked", "failed", "invalid"},
        booleans=(
            "startup_ready",
            "service_stopped",
            "run_ready",
            "audit_ready",
            "admission_ready",
        ),
    )
    _validate_common_receipt(
        admission_report,
        fields=_ADMISSION_REPORT_FIELDS,
        label="admission report",
        version_field="admission_version",
        version=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
        canonical_bytes=_pretty_json_bytes,
        statuses={"ready", "blocked", "failed", "invalid"},
        booleans=(
            "startup_ready",
            "service_stopped",
            "run_ready",
            "audit_ready",
            "admission_ready",
        ),
    )
    _validate_common_receipt(
        admission_audit,
        fields=_ADMISSION_AUDIT_FIELDS,
        label="admission audit",
        version_field="audit_version",
        version=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
        canonical_bytes=_pretty_json_bytes,
        statuses={"ready", "blocked", "failed", "invalid"},
        booleans=(
            "startup_ready",
            "service_stopped",
            "run_ready",
            "audit_ready",
            "admission_ready",
        ),
    )

    for field in _ADMISSION_INPUT_FIELDS - {"output_sha256"}:
        if admission_report[field] != admission[field]:
            raise _AuditFailure("STATE_MISMATCH", f"admission report {field} differs")
    for field in _ADMISSION_INPUT_FIELDS - {"output_sha256", "audit_version"}:
        if admission_audit[field] != admission[field]:
            raise _AuditFailure("STATE_MISMATCH", f"admission audit {field} differs")
    admission_file = input_files["admission"]
    admission_report_file = input_files["admission_report"]
    if (
        admission_report["admission_path"] != admission_file[1]
        or admission_report["admission_sha256"] != admission_file[3]
        or admission_audit["admission_path"] != admission_file[1]
        or admission_audit["admission_sha256"] != admission_file[3]
        or admission_audit["report_path"] != admission_report_file[1]
        or admission_audit["report_sha256"] != admission_report_file[3]
    ):
        raise _AuditFailure("CHAIN_MISMATCH", "admission input references differ")
    if admission_audit["status"] != admission["status"]:
        raise _AuditFailure("STATE_MISMATCH", "admission status chain differs")

    admission_status = admission["status"]
    admission_issues = _issues(admission["issues"], label="admission issues")
    if admission_status == "ready":
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
            raise _AuditFailure("STATE_MISMATCH", "ready admission state is inconsistent")
    elif admission_status in {"blocked", "failed"}:
        if not (
            admission["audit_ready"] is True
            and admission["admission_ready"] is False
            and admission["run_status"] == admission_status
            and admission_issues
        ):
            raise _AuditFailure("STATE_MISMATCH", "non-ready admission state is inconsistent")
    elif not (
        admission["audit_ready"] is False
        and admission["admission_ready"] is False
        and admission["run_status"] == "invalid"
        and admission_issues
    ):
        raise _AuditFailure("STATE_MISMATCH", "invalid admission state is inconsistent")
    if (
        admission["status"] != smoke["admission_status"]
        or admission_audit["status"] != smoke["audit_status"]
    ):
        raise _AuditFailure("STATE_MISMATCH", "upstream and smoke admission status differs")

    release_keys = {"release_manifest", "release_report", "release_audit"}
    if admission_status in {"blocked", "failed"} and not missing:
        raise _AuditFailure(
            "STATE_MISMATCH",
            "blocked or failed admission must not carry release inputs",
        )
    if missing:
        if (
            set(missing) == release_keys
            and smoke["status"] in {"blocked", "failed"}
            and admission_status == smoke["status"]
        ):
            return admission, admission_report, admission_audit, None, None, None
        raise _AuditFailure("INPUT_UNAVAILABLE", "upstream inputs are incomplete")

    release_manifest = values["release_manifest"]
    release_report = values["release_report"]
    release_audit = values["release_audit"]

    for payload, label in (
        (release_manifest, "release manifest"),
        (release_report, "release report"),
        (release_audit, "release audit"),
    ):
        _validate_common_receipt(
            payload,
            fields=(
                _RELEASE_MANIFEST_FIELDS
                if label == "release manifest"
                else _RELEASE_REPORT_FIELDS
                if label == "release report"
                else _RELEASE_AUDIT_FIELDS
            ),
            label=label,
            version_field="release_version",
            version=DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
            canonical_bytes=_pretty_json_bytes,
            statuses={"ready", "blocked", "failed", "invalid"},
            booleans=("run_ready", "service_stopped", "audit_ready", "release_ready"),
        )
    release_manifest_file = input_files["release_manifest"]
    release_report_file = input_files["release_report"]
    if (
        release_report["manifest_path"] != release_manifest_file[1]
        or release_report["manifest_sha256"] != release_manifest_file[3]
        or release_audit["manifest_path"] != release_manifest_file[1]
        or release_audit["manifest_sha256"] != release_manifest_file[3]
        or release_audit["report_path"] != release_report_file[1]
        or release_audit["report_sha256"] != release_report_file[3]
    ):
        raise _AuditFailure("CHAIN_MISMATCH", "release input references differ")
    for field in _RELEASE_MANIFEST_FIELDS - {"output_sha256"}:
        if release_report[field] != release_manifest[field]:
            raise _AuditFailure("STATE_MISMATCH", f"release report {field} differs")
    for field in _RELEASE_MANIFEST_FIELDS - {"output_sha256"}:
        if release_audit[field] != release_manifest[field]:
            raise _AuditFailure("STATE_MISMATCH", f"release audit {field} differs")
    if release_manifest["release_version"] != admission["release_version"]:
        raise _AuditFailure("VERSION_MISMATCH", "admission and release versions differ")
    for field in ("symbol", "as_of", "evaluation_at"):
        if release_manifest[field] != admission[field]:
            raise _AuditFailure("CHAIN_MISMATCH", f"admission and release {field} differs")
    release_status = release_manifest["status"]
    release_issues = _issues(release_manifest["issues"], label="release issues")
    if release_status == "ready":
        if not (
            release_manifest["run_status"] == "ready"
            and release_manifest["run_ready"] is True
            and release_manifest["service_stopped"] is True
            and release_manifest["audit_status"] == "ready"
            and release_manifest["audit_ready"] is True
            and release_manifest["release_ready"] is True
            and not release_issues
        ):
            raise _AuditFailure("STATE_MISMATCH", "ready release state is inconsistent")
    elif release_status in {"blocked", "failed"}:
        if not (
            release_manifest["release_ready"] is False
            and release_manifest["audit_ready"] is True
            and release_issues
        ):
            raise _AuditFailure("STATE_MISMATCH", "non-ready release state is inconsistent")
    elif not (
        release_manifest["release_ready"] is False
        and release_manifest["audit_ready"] is False
        and release_issues
    ):
        raise _AuditFailure("STATE_MISMATCH", "invalid release state is inconsistent")
    if admission["status"] == "ready" and release_manifest["status"] != "ready":
        raise _AuditFailure("STATE_MISMATCH", "ready admission has non-ready release state")

    return (
        admission,
        admission_report,
        admission_audit,
        release_manifest,
        release_report,
        release_audit,
    )


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
        ):
            raise _AuditFailure("FIELD_MISMATCH", f"{label} is invalid")
        result.append({"code": item["code"], "message": item["message"]})
    return result


def _validate_node75_report(
    payload: Mapping[str, Any], *, mode: str, root: Path, inputs: Mapping[str, Any]
) -> dict[str, Any]:
    if set(payload) != _NODE75_REPORT_FIELDS:
        raise _AuditFailure("SCHEMA_INVALID", "Node75 report fields are invalid")
    _self_hash(payload, label="Node75 report")
    if payload["startup_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION:
        raise _AuditFailure("VERSION_MISMATCH", "Node75 startup version is invalid")
    expected_versions = {
        "admission_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
        "audit_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
        "release_version": DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
    }
    for field, expected in expected_versions.items():
        if payload["status"] == "ready" and payload[field] != expected:
            raise _AuditFailure("VERSION_MISMATCH", f"Node75 {field} is invalid")
        if (
            payload["status"] != "ready"
            and payload[field] is not None
            and payload[field] != expected
        ):
            raise _AuditFailure("VERSION_MISMATCH", f"Node75 {field} is invalid")
    if payload["mode"] != mode or payload["decision_ready"] is not False:
        raise _AuditFailure("STATE_MISMATCH", "Node75 report mode or decision gate is invalid")
    for field in ("admission_ready", "audit_ready", "startup_ready", "service_started"):
        if not isinstance(payload[field], bool):
            raise _AuditFailure("FIELD_MISMATCH", f"Node75 {field} is invalid")
    for field in ("status", "admission_status", "audit_status", "startup_status"):
        _enum(payload[field], {"ready", "blocked", "failed", "invalid"}, label=f"Node75 {field}")
    issues = _issues(payload["issues"], label="Node75 issues")
    if payload["status"] == "ready":
        if not (
            payload["admission_status"] == "ready"
            and payload["audit_status"] == "ready"
            and payload["admission_ready"] is True
            and payload["audit_ready"] is True
            and payload["startup_status"] == "ready"
            and payload["startup_ready"] is True
            and payload["service_started"] is (mode == "serve")
            and not issues
        ):
            raise _AuditFailure("STATE_MISMATCH", "ready Node75 report is inconsistent")
    elif not (
        issues
        and payload["startup_ready"] is False
        and payload["service_started"] is False
        and payload["startup_status"] == payload["status"]
    ):
        raise _AuditFailure("STATE_MISMATCH", "non-ready Node75 report is inconsistent")
    for key in _INPUTS:
        path_field, sha_field, _ = _INPUTS[key]
        if payload[path_field] != inputs[path_field] or payload[sha_field] != inputs[sha_field]:
            raise _AuditFailure("CHAIN_MISMATCH", f"Node75 {key} input differs")
    for key in _INPUTS:
        smoke_path_field, smoke_sha_field, _ = _INPUTS[key]
        value = {
            smoke_path_field: payload[smoke_path_field],
            smoke_sha_field: payload[smoke_sha_field],
        }
        required = payload["status"] == "ready"
        _declared_file(value, key=key, root=root, required=required)
    return dict(payload)


def _validate_smoke(
    payload: Mapping[str, Any], *, root: Path, smoke_relative: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    if set(payload) != _NODE76_REPORT_FIELDS:
        raise _AuditFailure("SCHEMA_INVALID", "smoke report fields are invalid")
    _self_hash(payload, label="smoke report")
    if payload["run_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION:
        raise _AuditFailure("VERSION_MISMATCH", "smoke version is invalid")
    if payload["decision_ready"] is not False:
        raise _AuditFailure("DECISION_GATE_INVALID", "smoke decision gate is invalid")
    for field in (
        "startup_ready",
        "service_started",
        "service_stopped",
        "run_ready",
    ):
        if not isinstance(payload[field], bool):
            raise _AuditFailure("FIELD_MISMATCH", f"smoke {field} is invalid")
    for field in ("status", "startup_status", "admission_status", "audit_status"):
        _enum(payload[field], {"ready", "blocked", "failed", "invalid"}, label=f"smoke {field}")
    _enum(
        payload["probe_status"],
        {"not_started", "ready", "failed", "timeout", "service_exited"},
        label="smoke probe_status",
    )
    _enum(
        payload["stop_status"],
        {"not_attempted", "controlled", "uncontrolled_exit", "failed"},
        label="smoke stop_status",
    )
    if payload["probe_exit_code"] is not None and (
        isinstance(payload["probe_exit_code"], bool)
        or not isinstance(payload["probe_exit_code"], int)
        or payload["probe_exit_code"] < 0
    ):
        raise _AuditFailure("FIELD_MISMATCH", "smoke probe_exit_code is invalid")
    issues = _issues(payload["issues"], label="smoke issues")
    if payload["status"] == "ready":
        if not (
            payload["startup_status"] == "ready"
            and payload["startup_ready"] is True
            and payload["service_started"] is True
            and payload["probe_status"] == "ready"
            and payload["probe_exit_code"] == 0
            and payload["stop_status"] == "controlled"
            and payload["service_stopped"] is True
            and payload["run_ready"] is True
            and not issues
        ):
            raise _AuditFailure("STATE_MISMATCH", "ready smoke report is inconsistent")
    elif payload["status"] == "blocked":
        if not (
            payload["admission_status"] == "blocked"
            and payload["audit_status"] == "blocked"
            and payload["startup_status"] == "blocked"
            and payload["startup_ready"] is False
            and payload["service_started"] is False
            and payload["probe_status"] == "not_started"
            and payload["probe_exit_code"] is None
            and payload["stop_status"] == "not_attempted"
            and payload["service_stopped"] is False
            and payload["run_ready"] is False
            and issues
        ):
            raise _AuditFailure("STATE_MISMATCH", "blocked smoke report is inconsistent")
    elif payload["status"] == "failed":
        admission_failure = (
            payload["admission_status"] in {"ready", "failed"}
            and payload["audit_status"] in {"ready", "failed"}
            and payload["startup_status"] == "failed"
            and payload["startup_ready"] is False
            and payload["service_started"] is False
        )
        probe_failure = (
            payload["admission_status"] == "ready"
            and payload["audit_status"] == "ready"
            and payload["startup_status"] == "ready"
            and payload["startup_ready"] is True
            and payload["service_started"] is True
            and payload["probe_status"] in {"failed", "timeout", "service_exited"}
            and (
                (
                    payload["probe_status"] == "timeout"
                    and payload["probe_exit_code"] is None
                )
                or (
                    payload["probe_status"] == "failed"
                    and (
                        payload["probe_exit_code"] is None
                        or payload["probe_exit_code"] > 0
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
        if not ((admission_failure or probe_failure) and payload["run_ready"] is False and issues):
            raise _AuditFailure("STATE_MISMATCH", "failed smoke report is inconsistent")
    elif not (
        payload["admission_status"] in {"invalid", "ready"}
        and payload["audit_status"] in {"invalid", "ready"}
        and payload["startup_status"] == "invalid"
        and payload["startup_ready"] is False
        and payload["service_started"] is False
        and payload["run_ready"] is False
        and issues
    ):
        raise _AuditFailure("STATE_MISMATCH", "invalid smoke report is inconsistent")
    input_files: dict[str, tuple[Path, str, bytes, str]] = {}
    for key in _INPUTS:
        item = _declared_file(payload, key=key, root=root, required=payload["status"] == "ready")
        if item is not None:
            input_files[key] = item
    smoke_dir = PureWindowsPath(smoke_relative).parent.as_posix()
    if smoke_dir == ".":
        smoke_dir = ""
    preflight_relative = payload["preflight_report_path"]
    if not isinstance(preflight_relative, str):
        raise _AuditFailure("PATH_INVALID", "preflight report path is invalid")
    preflight_relative = _relative(preflight_relative, label="preflight_report_path")
    expected_preflight = (
        f"{smoke_dir + '/' if smoke_dir else ''}preflight/"
        f"{DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME}"
    )
    if preflight_relative != expected_preflight:
        raise _AuditFailure("CHAIN_MISMATCH", "preflight report path is invalid")
    preflight_path, _, preflight_raw, _ = _safe_file(
        root / preflight_relative, root=root, label="preflight report"
    )
    if payload["preflight_report_sha256"] != sha256_bytes(preflight_raw):
        raise _AuditFailure("HASH_MISMATCH", "preflight report SHA differs")
    preflight = _read_object(preflight_raw, label="preflight report")
    preflight = _validate_node75_report(
        preflight, mode="check_only", root=root, inputs=payload
    )
    startup: dict[str, Any] | None = None
    if payload["startup_report_path"] is not None:
        startup_relative = _relative(payload["startup_report_path"], label="startup_report_path")
        expected_startup = (
            f"{smoke_dir + '/' if smoke_dir else ''}startup/"
            f"{DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME}"
        )
        if startup_relative != expected_startup:
            raise _AuditFailure("CHAIN_MISMATCH", "startup report path is invalid")
        _, _, startup_raw, _ = _safe_file(
            root / startup_relative, root=root, label="startup report"
        )
        if payload["startup_report_sha256"] != sha256_bytes(startup_raw):
            raise _AuditFailure("HASH_MISMATCH", "startup report SHA differs")
        startup = _validate_node75_report(
            _read_object(startup_raw, label="startup report"),
            mode="serve",
            root=root,
            inputs=payload,
        )
        for field in (*_CHAIN_FIELDS, "admission_status", "audit_status"):
            if startup[field] != preflight[field]:
                raise _AuditFailure("CHAIN_MISMATCH", "preflight and startup identity differs")
    elif payload["status"] == "ready" or payload["startup_ready"] is True:
        raise _AuditFailure("INPUT_UNAVAILABLE", "ready smoke report lacks startup report")
    for field in (*_CHAIN_FIELDS, "admission_status", "audit_status"):
        if preflight[field] != payload[field]:
            raise _AuditFailure("CHAIN_MISMATCH", f"smoke and preflight {field} differs")
    if payload["status"] == "ready" and startup is None:
        raise _AuditFailure("INPUT_UNAVAILABLE", "ready smoke report lacks startup report")
    return dict(payload), preflight, startup, input_files


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_VERSION,
        "smoke_version": None,
        "run_version": None,
        "startup_version": None,
        "admission_version": None,
        "audit_input_version": None,
        "release_version": None,
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
        "release_audit_path": None,
        "release_audit_sha256": None,
        "preflight_report_path": None,
        "preflight_report_sha256": None,
        "startup_report_path": None,
        "startup_report_sha256": None,
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
        "status": "invalid",
        "run_ready": False,
        "audit_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> Path:
    canonical = dict(report)
    canonical["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = (
            output_dir
            / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME
        )
        write_atomic(path, _json_bytes(report))
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAuditError(
            "OUTPUT_UNAVAILABLE", "audit report output is unavailable"
        ) from exc
    return path


def _rooted_dir(path: Path, *, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root", configuration=True
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAuditError(
            "OUTPUT_DIR_INVALID", f"{label} is invalid", configuration=True
        )
    return resolved


def audit_daily_research_service_release_run_admission_startup_smoke(
    *, smoke_report_path: Path, artifact_root: Path, output_dir: Path
) -> tuple[dict[str, Any], int]:
    """Audit a Node76 smoke report without starting a process or using HTTP."""

    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    output = _rooted_dir(output_dir, root=root, label="output-dir")
    report = _base_report()
    try:
        candidate, relative, raw, digest = _safe_file(
            smoke_report_path, root=root, label="smoke report"
        )
        if candidate.name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME:
            raise _AuditFailure("FILENAME_INVALID", "smoke report filename is invalid")
        text = raw.decode("utf-8")
        if _ABSOLUTE_PATH_RE.search(text):
            raise _AuditFailure("SENSITIVE_OUTPUT", "smoke report contains an absolute path")
        smoke = _read_object(raw, label="smoke report")
        smoke, preflight, startup, input_files = _validate_smoke(
            smoke, root=root, smoke_relative=relative
        )
        upstream = _validate_upstream_inputs(input_files, smoke=smoke)
        admission, _, admission_audit, _, _, _ = upstream
        report.update(
            {
                "smoke_version": smoke["run_version"],
                "run_version": None,
                "startup_version": smoke["startup_version"],
                "admission_version": smoke["admission_version"],
                "release_version": smoke["release_version"],
                "smoke_report_path": relative,
                "smoke_report_sha256": digest,
                "preflight_report_path": smoke["preflight_report_path"],
                "preflight_report_sha256": smoke["preflight_report_sha256"],
                "startup_report_path": smoke["startup_report_path"],
                "startup_report_sha256": smoke["startup_report_sha256"],
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
                "status": smoke["status"],
                "run_ready": smoke["run_ready"],
                "issues": smoke["issues"],
            }
        )
        for key, (path, _, _, actual_sha) in input_files.items():
            path_field, sha_field, _ = _INPUTS[key]
            report[path_field] = smoke[path_field]
            report[sha_field] = actual_sha
        report["run_version"] = admission["run_version"]
        report["audit_input_version"] = admission_audit["audit_version"]
        if report["run_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION:
            raise _AuditFailure("VERSION_MISMATCH", "run version is invalid")
        if (
            report["audit_input_version"]
            != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION
        ):
            raise _AuditFailure("VERSION_MISMATCH", "audit input version is invalid")
        if smoke["status"] == "ready":
            if startup is None or startup["status"] != "ready":
                raise _AuditFailure("STATE_MISMATCH", "ready startup and smoke status differs")
        elif smoke["status"] == "blocked":
            if preflight["status"] != "blocked" or startup is not None:
                raise _AuditFailure("STATE_MISMATCH", "blocked startup chain differs")
        elif smoke["status"] == "failed":
            if preflight["status"] not in {"ready", "failed"}:
                raise _AuditFailure("STATE_MISMATCH", "failed preflight chain differs")
            if startup is not None:
                expected_startup_status = (
                    "ready" if smoke["startup_status"] == "ready" else "failed"
                )
                if startup["status"] != expected_startup_status:
                    raise _AuditFailure("STATE_MISMATCH", "failed startup chain differs")
        elif startup is not None:
            raise _AuditFailure("STATE_MISMATCH", "invalid startup chain differs")
        report["audit_ready"] = True
        return _finish(
            report,
            output_dir=output,
            status=smoke["status"],
            code=0 if smoke["status"] == "ready" else 1,
        )
    except _AuditFailure as exc:
        report["issues"] = [_issue(exc.code, str(exc))]
        return _finish(report, output_dir=output, status="invalid", code=1)


def _finish(
    report: dict[str, Any], *, output_dir: Path, status: str, code: int
) -> tuple[dict[str, Any], int]:
    report["status"] = status
    report["audit_ready"] = report["audit_ready"] is True and status != "invalid"
    report["run_ready"] = report["run_ready"] is True and status == "ready"
    report["decision_ready"] = False
    _write_report(report, output_dir=output_dir)
    return report, code


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupSmokeAuditError",
    "audit_daily_research_service_release_run_admission_startup_smoke",
]
