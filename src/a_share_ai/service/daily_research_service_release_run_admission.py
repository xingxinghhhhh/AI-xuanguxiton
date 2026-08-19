"""Build a read-only admission summary from Node71 and Node72 receipts."""

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
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION,
)
from .daily_research_service_release_run_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION,
)
from .daily_research_service_release_startup import DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION = (
    "daily-research-service-release-run-admission-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME = (
    "daily_research_service_release_run_admission.json"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME = (
    "daily_research_service_release_run_admission_report.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_RUN_FIELDS = {
    "run_version",
    "release_version",
    "startup_version",
    "release_manifest_path",
    "release_manifest_sha256",
    "release_report_path",
    "release_report_sha256",
    "release_audit_report_path",
    "release_audit_report_sha256",
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
    "issues",
    "decision_ready",
    "output_sha256",
}
_AUDIT_FIELDS = {
    "audit_version",
    "run_version",
    "release_version",
    "startup_version",
    "run_report_path",
    "run_report_sha256",
    "release_manifest_path",
    "release_manifest_sha256",
    "release_report_path",
    "release_report_sha256",
    "release_audit_report_path",
    "release_audit_report_sha256",
    "node66_run_report_path",
    "node66_run_report_sha256",
    "node67_run_audit_report_path",
    "node67_run_audit_report_sha256",
    "node65_gate_path",
    "node65_gate_sha256",
    "node65_audit_path",
    "node65_audit_sha256",
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
    "status",
    "audit_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
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
_ADMISSION_REPORT_FIELDS = _ADMISSION_FIELDS | {"admission_path", "admission_sha256"}


class DailyResearchServiceReleaseRunAdmissionError(ValueError):
    """A sanitized admission configuration or artifact error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": str(message)}


def _enum(value: Any, allowed: set[str], *, label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise DailyResearchServiceReleaseRunAdmissionError("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _optional_enum(value: Any, allowed: set[str], *, label: str) -> str | None:
    if value is None:
        return None
    return _enum(value, allowed, label=label)


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceReleaseRunAdmissionError("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseRunAdmissionError(
            "PATH_OUTSIDE_ROOT", f"{label} is invalid"
        )
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "PATH_OUTSIDE_ROOT", f"{label} is invalid"
        )
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "PATH_OUTSIDE_ROOT", f"{label} is not normalized"
        )
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "PATH_OUTSIDE_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceReleaseRunAdmissionError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "JSON_INVALID", f"{label} must be an object"
        )
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "SELF_HASH_MISMATCH", f"{label} self-hash differs"
        )


def _time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseRunAdmissionError("TIME_MISMATCH", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "TIME_MISMATCH", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "TIME_MISMATCH", f"{label} needs timezone"
        )
    return parsed


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchServiceReleaseRunAdmissionError("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise DailyResearchServiceReleaseRunAdmissionError(
                "FIELD_MISMATCH", f"{label} is invalid"
            )
        result.append(_issue(item["code"], item["message"]))
    return result


def _validate_run(run: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(run) != _RUN_FIELDS:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "UNKNOWN_FIELD", "Node71 run report fields are invalid"
        )
    _self_hash(run, label="Node71 run report")
    if run["run_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "VERSION_MISMATCH", "Node71 run version is invalid"
        )
    if run["release_version"] != DAILY_RESEARCH_SERVICE_RELEASE_VERSION:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "VERSION_MISMATCH", "Node71 release version is invalid"
        )
    if run["startup_version"] != DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "VERSION_MISMATCH", "Node71 startup version is invalid"
        )
    if run["decision_ready"] is not False:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "DECISION_GATE_INVALID", "Node71 decision_ready must be false"
        )
    _enum(run["startup_status"], {"ready", "blocked", "failed"}, label="Node71 startup_status")
    _enum(
        run["probe_status"],
        {"not_started", "ready", "failed", "timeout", "service_exited"},
        label="Node71 probe_status",
    )
    _enum(
        run["stop_status"],
        {"not_attempted", "controlled", "uncontrolled_exit", "failed"},
        label="Node71 stop_status",
    )
    _enum(run["run_status"], {"ready", "blocked", "failed"}, label="Node71 run_status")
    for field in ("startup_ready", "service_stopped", "run_ready"):
        if not isinstance(run[field], bool):
            raise DailyResearchServiceReleaseRunAdmissionError(
                "FIELD_MISMATCH", f"Node71 {field} is invalid"
            )
    if run["probe_exit_code"] is not None and (
        isinstance(run["probe_exit_code"], bool)
        or not isinstance(run["probe_exit_code"], int)
        or run["probe_exit_code"] < 0
    ):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "FIELD_MISMATCH", "Node71 probe_exit_code is invalid"
        )
    if run["startup_status"] == "blocked" and run["symbol"] is None:
        if run["as_of"] is not None or run["evaluation_at"] is not None:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "FIELD_MISMATCH", "blocked Node71 identity fields are invalid"
            )
    else:
        if not isinstance(run["symbol"], str) or not run["symbol"].strip():
            raise DailyResearchServiceReleaseRunAdmissionError(
                "FIELD_MISMATCH", "Node71 symbol is invalid"
            )
        as_of = _time(run["as_of"], label="Node71 as_of")
        evaluation_at = _time(run["evaluation_at"], label="Node71 evaluation_at")
        if as_of > evaluation_at:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "TIME_MISMATCH", "Node71 as_of is after evaluation_at"
            )
    for field in (
        "release_manifest_path",
        "release_report_path",
        "release_audit_report_path",
    ):
        _relative(run[field], label=f"Node71 {field}")
    for field in (
        "release_manifest_sha256",
        "release_report_sha256",
        "release_audit_report_sha256",
    ):
        _sha(run[field], label=f"Node71 {field}")
    return _issues(run["issues"], label="Node71 issues")


def _validate_audit(audit: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(audit) != _AUDIT_FIELDS:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "UNKNOWN_FIELD", "Node72 audit report fields are invalid"
        )
    _self_hash(audit, label="Node72 audit report")
    if audit["audit_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "VERSION_MISMATCH", "Node72 audit version is invalid"
        )
    if not isinstance(audit["audit_ready"], bool):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "FIELD_MISMATCH", "Node72 audit_ready is invalid"
        )
    audit_ready = audit["audit_ready"] is True
    for field, expected in (
        ("run_version", DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION),
        ("release_version", DAILY_RESEARCH_SERVICE_RELEASE_VERSION),
        ("startup_version", DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION),
    ):
        if (audit_ready and audit[field] != expected) or (
            not audit_ready and audit[field] is not None and audit[field] != expected
        ):
            raise DailyResearchServiceReleaseRunAdmissionError(
                "VERSION_MISMATCH", f"Node72 {field} is invalid"
            )
    if audit["decision_ready"] is not False:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "DECISION_GATE_INVALID", "Node72 decision_ready must be false"
        )
    _optional_enum(
        audit["startup_status"],
        {"ready", "blocked", "invalid", "failed"},
        label="Node72 startup_status",
    )
    _optional_enum(
        audit["probe_status"],
        {"not_started", "ready", "invalid", "failed", "timeout", "service_exited"},
        label="Node72 probe_status",
    )
    _optional_enum(
        audit["stop_status"],
        {"not_attempted", "controlled", "uncontrolled_exit", "failed"},
        label="Node72 stop_status",
    )
    _enum(audit["run_status"], {"ready", "blocked", "failed", "invalid"}, label="Node72 run_status")
    _enum(audit["status"], {"ready", "blocked", "failed", "invalid"}, label="Node72 status")
    for field in (
        "startup_ready",
        "service_stopped",
        "run_ready",
        "audit_ready",
    ):
        if not isinstance(audit[field], bool):
            raise DailyResearchServiceReleaseRunAdmissionError(
                "FIELD_MISMATCH", f"Node72 {field} is invalid"
            )
    if audit["probe_exit_code"] is not None and (
        isinstance(audit["probe_exit_code"], bool)
        or not isinstance(audit["probe_exit_code"], int)
        or audit["probe_exit_code"] < 0
    ):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "FIELD_MISMATCH", "Node72 probe_exit_code is invalid"
        )
    path_fields = (
        "run_report_path",
        "release_manifest_path",
        "release_report_path",
        "release_audit_report_path",
        "node66_run_report_path",
        "node67_run_audit_report_path",
        "node65_gate_path",
        "node65_audit_path",
    )
    for field in path_fields:
        if audit_ready or audit[field] is not None:
            _relative(audit[field], label=f"Node72 {field}")
    hash_fields = (
        "run_report_sha256",
        "release_manifest_sha256",
        "release_report_sha256",
        "release_audit_report_sha256",
        "node66_run_report_sha256",
        "node67_run_audit_report_sha256",
        "node65_gate_sha256",
        "node65_audit_sha256",
    )
    for field in hash_fields:
        if audit_ready or audit[field] is not None:
            _sha(audit[field], label=f"Node72 {field}")
    if audit_ready and (not isinstance(audit["symbol"], str) or not audit["symbol"].strip()):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "FIELD_MISMATCH", "Node72 symbol is invalid"
        )
    if audit["symbol"] is not None and (
        not isinstance(audit["symbol"], str) or not audit["symbol"].strip()
    ):
        raise DailyResearchServiceReleaseRunAdmissionError(
            "FIELD_MISMATCH", "Node72 symbol is invalid"
        )
    if audit_ready or audit["as_of"] is not None:
        as_of = _time(audit["as_of"], label="Node72 as_of")
        evaluation_at = _time(audit["evaluation_at"], label="Node72 evaluation_at")
        if as_of > evaluation_at:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "TIME_MISMATCH", "Node72 as_of is after evaluation_at"
            )
    elif audit["evaluation_at"] is not None:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "TIME_MISMATCH", "Node72 evaluation_at is invalid"
        )
    return _issues(audit["issues"], label="Node72 issues")


def _base_admission() -> dict[str, Any]:
    return {
        "admission_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
        "run_version": None,
        "release_version": None,
        "startup_version": None,
        "audit_version": None,
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


def _finalize(
    admission: dict[str, Any], *, output_dir: Path, artifact_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        admission_path = output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME
        admission_raw = _write_hashed(admission, admission_path)
        report = dict(admission)
        report["admission_path"] = (
            admission_path.resolve().relative_to(artifact_root.resolve()).as_posix()
        )
        report["admission_sha256"] = sha256_bytes(admission_raw)
        report_path = output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME
        _write_hashed(report, report_path)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "OUTPUT_UNAVAILABLE", "admission output is unavailable"
        ) from exc
    return admission, report


def build_daily_research_service_release_run_admission(
    *, run_report_path: Path, run_audit_report_path: Path, artifact_root: Path, output_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Build a deterministic admission summary without reading upstream artifacts."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionError(
            "OUTPUT_DIR_INVALID", "output directory escapes artifact root"
        ) from exc

    admission = _base_admission()
    try:
        run_file = _safe_file(run_report_path, root=root, label="Node71 run report")
        audit_file = _safe_file(run_audit_report_path, root=root, label="Node72 audit report")
        if run_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "SCHEMA_INVALID", "Node71 run filename is invalid"
            )
        if audit_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "SCHEMA_INVALID", "Node72 audit filename is invalid"
            )
        run = _read_json(run_file["raw"], label="Node71 run report")
        audit = _read_json(audit_file["raw"], label="Node72 audit report")
        run_issues = _validate_run(run)
        audit_issues = _validate_audit(audit)
        if audit["run_report_path"] != run_file["relative_path"]:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "CHAIN_MISMATCH", "Node72 run report path differs"
            )
        if audit["run_report_sha256"] != run_file["sha256"]:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "HASH_MISMATCH", "Node72 run report SHA differs"
            )
        common_fields = (
            "startup_status",
            "startup_ready",
            "probe_status",
            "probe_exit_code",
            "stop_status",
            "service_stopped",
            "run_status",
            "run_ready",
        )
        for field in common_fields:
            if audit[field] != run[field]:
                raise DailyResearchServiceReleaseRunAdmissionError(
                    "STATE_MISMATCH", f"Node71/72 {field} differs"
                )
        identity = {field: run[field] for field in ("symbol", "as_of", "evaluation_at")}
        blocked_identity = (
            run["run_status"] == "blocked"
            and run["symbol"] is None
            and run["as_of"] is None
            and run["evaluation_at"] is None
        )
        for field in identity:
            if blocked_identity:
                identity[field] = audit[field]
            elif audit[field] != run[field]:
                raise DailyResearchServiceReleaseRunAdmissionError(
                    "STATE_MISMATCH", f"Node71/72 {field} differs"
                )
        if audit_issues != run_issues:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "STATE_MISMATCH", "Node71/72 issues differ"
            )
        for field in (
            "release_manifest_path",
            "release_manifest_sha256",
            "release_report_path",
            "release_report_sha256",
            "release_audit_report_path",
            "release_audit_report_sha256",
        ):
            if audit[field] != run[field]:
                raise DailyResearchServiceReleaseRunAdmissionError(
                    "CHAIN_MISMATCH", f"Node71/72 {field} differs"
                )
        expected_status = run["run_status"]
        if audit["status"] != expected_status:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "STATE_MISMATCH", "Node71/72 status differs"
            )
        if audit["audit_ready"] is not True:
            raise DailyResearchServiceReleaseRunAdmissionError(
                "UPSTREAM_NOT_READY", "Node72 audit is not ready"
            )
        ready = (
            audit["status"] == "ready"
            and run["startup_status"] == "ready"
            and run["startup_ready"] is True
            and run["probe_status"] == "ready"
            and run["probe_exit_code"] == 0
            and run["stop_status"] == "controlled"
            and run["service_stopped"] is True
            and run["run_status"] == "ready"
            and run["run_ready"] is True
            and not run_issues
        )
        status = "ready" if ready else expected_status
        admission.update(
            {
                "run_version": run["run_version"],
                "release_version": run["release_version"],
                "startup_version": run["startup_version"],
                "audit_version": audit["audit_version"],
                "run_report_path": run_file["relative_path"],
                "run_report_sha256": run_file["sha256"],
                "run_audit_report_path": audit_file["relative_path"],
                "run_audit_report_sha256": audit_file["sha256"],
                "symbol": identity["symbol"],
                "as_of": identity["as_of"],
                "evaluation_at": identity["evaluation_at"],
                "startup_status": run["startup_status"],
                "startup_ready": run["startup_ready"],
                "probe_status": run["probe_status"],
                "probe_exit_code": run["probe_exit_code"],
                "stop_status": run["stop_status"],
                "service_stopped": run["service_stopped"],
                "run_status": run["run_status"],
                "run_ready": run["run_ready"],
                "audit_ready": True,
                "status": status,
                "admission_ready": ready,
                "issues": run_issues,
            }
        )
    except DailyResearchServiceReleaseRunAdmissionError as exc:
        admission["issues"] = [_issue(exc.code, str(exc))]
    admission_report, report = _finalize(admission, output_dir=output_dir, artifact_root=root)
    exit_code = 0 if admission["admission_ready"] is True else 1
    return admission_report, report, exit_code


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME",
    "DailyResearchServiceReleaseRunAdmissionError",
    "build_daily_research_service_release_run_admission",
]
