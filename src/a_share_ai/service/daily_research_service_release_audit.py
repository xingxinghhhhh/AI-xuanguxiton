"""Independently audit the Node68 daily-service release admission."""

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
    DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
)
from .daily_research_service_run import (
    DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_VERSION,
)
from .daily_research_service_run_audit import (
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
)

DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION = "daily-research-service-release-audit-v1"
DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME = (
    "daily_research_service_release_audit_report.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_FIELDS = {
    "run_version",
    "gate_path",
    "gate_sha256",
    "audit_path",
    "audit_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "startup_status",
    "probe_status",
    "probe_exit_code",
    "service_stopped",
    "run_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_AUDIT_FIELDS = {
    "audit_version",
    "run_report_path",
    "run_report_sha256",
    "gate_path",
    "gate_sha256",
    "audit_path",
    "audit_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "run_status",
    "startup_status",
    "probe_status",
    "probe_exit_code",
    "service_stopped",
    "run_ready",
    "audit_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_MANIFEST_FIELDS = {
    "release_version",
    "run_report_path",
    "run_report_sha256",
    "run_audit_report_path",
    "run_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "run_status",
    "run_ready",
    "service_stopped",
    "audit_status",
    "audit_ready",
    "status",
    "release_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_REPORT_FIELDS = {
    "release_version",
    "manifest_path",
    "manifest_sha256",
    "run_report_path",
    "run_report_sha256",
    "run_audit_report_path",
    "run_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "run_status",
    "run_ready",
    "service_stopped",
    "audit_status",
    "audit_ready",
    "status",
    "release_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_AUDIT_OUTPUT_FIELDS = {
    "audit_version",
    "release_version",
    "manifest_path",
    "manifest_sha256",
    "report_path",
    "report_sha256",
    "run_report_path",
    "run_report_sha256",
    "run_audit_report_path",
    "run_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "run_status",
    "run_ready",
    "service_stopped",
    "audit_status",
    "audit_ready",
    "status",
    "release_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceReleaseAuditError(ValueError):
    """A sanitized, fail-closed release audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": str(message)}


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceReleaseAuditError("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseAuditError("PATH_OUTSIDE_ROOT", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceReleaseAuditError("PATH_OUTSIDE_ROOT", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise DailyResearchServiceReleaseAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is not normalized"
        )
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceReleaseAuditError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceReleaseAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceReleaseAuditError("JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceReleaseAuditError("JSON_INVALID", f"{label} must be an object")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceReleaseAuditError(
            "SELF_HASH_MISMATCH", f"{label} self-hash differs"
        )


def _time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseAuditError("TIME_MISMATCH", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceReleaseAuditError("TIME_MISMATCH", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceReleaseAuditError("TIME_MISMATCH", f"{label} needs timezone")
    return parsed


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchServiceReleaseAuditError("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise DailyResearchServiceReleaseAuditError("FIELD_MISMATCH", f"{label} is invalid")
        result.append(_issue(item["code"], item["message"]))
    return result


def _validate_upstream(
    run: Mapping[str, Any], audit: Mapping[str, Any]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if set(run) != _RUN_FIELDS:
        raise DailyResearchServiceReleaseAuditError(
            "UNKNOWN_FIELD", "run report fields are invalid"
        )
    if run["run_version"] != DAILY_RESEARCH_SERVICE_RUN_VERSION:
        raise DailyResearchServiceReleaseAuditError(
            "VERSION_MISMATCH", "run report version is invalid"
        )
    _self_hash(run, label="run report")
    if set(audit) != _AUDIT_FIELDS:
        raise DailyResearchServiceReleaseAuditError("UNKNOWN_FIELD", "run audit fields are invalid")
    if audit["audit_version"] != DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION:
        raise DailyResearchServiceReleaseAuditError(
            "VERSION_MISMATCH", "run audit version is invalid"
        )
    _self_hash(audit, label="run audit")
    for payload, label in ((run, "run"), (audit, "audit")):
        if payload["decision_ready"] is not False:
            raise DailyResearchServiceReleaseAuditError(
                "DECISION_GATE_INVALID", f"{label} decision_ready must be false"
            )
        if not isinstance(payload["symbol"], str) or not payload["symbol"].strip():
            raise DailyResearchServiceReleaseAuditError(
                "FIELD_MISMATCH", f"{label} symbol is invalid"
            )
        _time(payload["as_of"], label=f"{label} as_of")
        _time(payload["evaluation_at"], label=f"{label} evaluation_at")
    if run["symbol"] != audit["symbol"]:
        raise DailyResearchServiceReleaseAuditError("SYMBOL_MISMATCH", "upstream symbols differ")
    if run["as_of"] != audit["as_of"] or run["evaluation_at"] != audit["evaluation_at"]:
        raise DailyResearchServiceReleaseAuditError("TIME_MISMATCH", "upstream timestamps differ")
    if _time(run["as_of"], label="as_of") > _time(run["evaluation_at"], label="evaluation_at"):
        raise DailyResearchServiceReleaseAuditError("TIME_MISMATCH", "as_of is after evaluation_at")
    for payload, label in ((run, "run"), (audit, "audit")):
        if not isinstance(payload["startup_status"], str) or payload["startup_status"] not in {
            "ready",
            "blocked",
            "invalid",
        }:
            raise DailyResearchServiceReleaseAuditError(
                "FIELD_MISMATCH", f"{label} startup_status is invalid"
            )
        if payload["probe_status"] is not None and (
            not isinstance(payload["probe_status"], str)
            or payload["probe_status"] not in {"ready", "invalid"}
        ):
            raise DailyResearchServiceReleaseAuditError(
                "FIELD_MISMATCH", f"{label} probe_status is invalid"
            )
        if payload["probe_exit_code"] is not None and (
            isinstance(payload["probe_exit_code"], bool)
            or not isinstance(payload["probe_exit_code"], int)
            or payload["probe_exit_code"] < 0
        ):
            raise DailyResearchServiceReleaseAuditError(
                "FIELD_MISMATCH", f"{label} probe_exit_code is invalid"
            )
        for field in ("service_stopped", "run_ready"):
            if not isinstance(payload[field], bool):
                raise DailyResearchServiceReleaseAuditError(
                    "FIELD_MISMATCH", f"{label} {field} is invalid"
                )
    if not isinstance(audit["run_status"], str) or audit["run_status"] not in {
        "ready",
        "failed",
        "blocked",
        "invalid",
    }:
        raise DailyResearchServiceReleaseAuditError("FIELD_MISMATCH", "audit run_status is invalid")
    if not isinstance(audit["audit_ready"], bool):
        raise DailyResearchServiceReleaseAuditError("FIELD_MISMATCH", "audit_ready is invalid")
    expected_run_status = (
        "blocked"
        if run["startup_status"] == "blocked"
        else "ready"
        if run["run_ready"]
        else "failed"
    )
    if audit["run_status"] != expected_run_status:
        raise DailyResearchServiceReleaseAuditError(
            "FIELD_MISMATCH", "upstream run_status differs"
        )
    for field in (
        "startup_status",
        "probe_status",
        "probe_exit_code",
        "run_ready",
        "service_stopped",
    ):
        if audit[field] != run[field]:
            raise DailyResearchServiceReleaseAuditError(
                "FIELD_MISMATCH", f"upstream {field} differs"
            )
    return _issues(run["issues"], label="run issues"), _issues(
        audit["issues"], label="audit issues"
    )


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION,
        "release_version": None,
        "manifest_path": None,
        "manifest_sha256": None,
        "report_path": None,
        "report_sha256": None,
        "run_report_path": None,
        "run_report_sha256": None,
        "run_audit_report_path": None,
        "run_audit_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "run_status": "invalid",
        "run_ready": False,
        "service_stopped": False,
        "audit_status": "invalid",
        "audit_ready": False,
        "status": "invalid",
        "release_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        canonical = dict(report)
        canonical["output_sha256"] = None
        report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
        write_atomic(
            output_dir / DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME, _json_bytes(report)
        )
    except OSError as exc:
        raise DailyResearchServiceReleaseAuditError(
            "OUTPUT_UNAVAILABLE", "audit output is unavailable"
        ) from exc
    return report


def audit_daily_research_service_release(
    *, manifest_path: Path, report_path: Path, artifact_root: Path, output_dir: Path
) -> dict[str, Any]:
    """Audit Node68 artifacts without rebuilding or executing any service."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceReleaseAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseAuditError(
            "OUTPUT_DIR_INVALID", "output directory escapes artifact root"
        ) from exc
    result = _base_report()
    try:
        manifest_file = _safe_file(manifest_path, root=root, label="release manifest")
        report_file = _safe_file(report_path, root=root, label="release report")
        if manifest_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME:
            raise DailyResearchServiceReleaseAuditError(
                "SCHEMA_INVALID", "manifest filename is invalid"
            )
        if report_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME:
            raise DailyResearchServiceReleaseAuditError(
                "SCHEMA_INVALID", "report filename is invalid"
            )
        manifest = _read_json(manifest_file["raw"], label="release manifest")
        report = _read_json(report_file["raw"], label="release report")
        if set(manifest) != _MANIFEST_FIELDS:
            raise DailyResearchServiceReleaseAuditError(
                "UNKNOWN_FIELD", "manifest fields are invalid"
            )
        if set(report) != _REPORT_FIELDS:
            raise DailyResearchServiceReleaseAuditError(
                "UNKNOWN_FIELD", "report fields are invalid"
            )
        if manifest["release_version"] != DAILY_RESEARCH_SERVICE_RELEASE_VERSION:
            raise DailyResearchServiceReleaseAuditError(
                "VERSION_MISMATCH", "manifest version is invalid"
            )
        if report["release_version"] != DAILY_RESEARCH_SERVICE_RELEASE_VERSION:
            raise DailyResearchServiceReleaseAuditError(
                "VERSION_MISMATCH", "report version is invalid"
            )
        _self_hash(manifest, label="release manifest")
        _self_hash(report, label="release report")
        if manifest["decision_ready"] is not False or report["decision_ready"] is not False:
            raise DailyResearchServiceReleaseAuditError(
                "DECISION_GATE_INVALID", "release decision_ready must be false"
            )
        declared_manifest_path = _relative(report["manifest_path"], label="report manifest_path")
        if declared_manifest_path != manifest_file["relative_path"]:
            raise DailyResearchServiceReleaseAuditError("CHAIN_MISMATCH", "manifest path differs")
        if (
            _sha(report["manifest_sha256"], label="report manifest_sha256")
            != manifest_file["sha256"]
        ):
            raise DailyResearchServiceReleaseAuditError("HASH_MISMATCH", "manifest SHA differs")
        run_path = _relative(manifest["run_report_path"], label="manifest run_report_path")
        audit_path = _relative(
            manifest["run_audit_report_path"], label="manifest run_audit_report_path"
        )
        run_file = _safe_file(root / Path(run_path), root=root, label="Node66 run report")
        audit_file = _safe_file(root / Path(audit_path), root=root, label="Node67 audit report")
        if run_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME:
            raise DailyResearchServiceReleaseAuditError(
                "SCHEMA_INVALID", "run report filename is invalid"
            )
        if audit_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME:
            raise DailyResearchServiceReleaseAuditError(
                "SCHEMA_INVALID", "run audit filename is invalid"
            )
        if (
            manifest["run_report_sha256"] != run_file["sha256"]
            or report["run_report_sha256"] != run_file["sha256"]
            or manifest["run_audit_report_sha256"] != audit_file["sha256"]
            or report["run_audit_report_sha256"] != audit_file["sha256"]
        ):
            raise DailyResearchServiceReleaseAuditError("HASH_MISMATCH", "upstream SHA differs")
        run = _read_json(run_file["raw"], label="Node66 run report")
        audit = _read_json(audit_file["raw"], label="Node67 audit report")
        run_issues, audit_issues = _validate_upstream(run, audit)
        if audit["run_report_path"] != run_path or audit["run_report_sha256"] != run_file["sha256"]:
            raise DailyResearchServiceReleaseAuditError(
                "CHAIN_MISMATCH", "audit run reference differs"
            )
        gate_path = _relative(run["gate_path"], label="run gate_path")
        chain_path = _relative(run["audit_path"], label="run audit_path")
        gate_file = _safe_file(root / Path(gate_path), root=root, label="launch gate")
        chain_file = _safe_file(root / Path(chain_path), root=root, label="launch gate audit")
        if gate_file["path"].name != "daily_research_service_launch_gate.json":
            raise DailyResearchServiceReleaseAuditError(
                "SCHEMA_INVALID", "gate filename is invalid"
            )
        if chain_file["path"].name != "daily_research_service_launch_gate_audit_report.json":
            raise DailyResearchServiceReleaseAuditError(
                "SCHEMA_INVALID", "launch gate audit filename is invalid"
            )
        if audit["gate_path"] != gate_path or audit["audit_path"] != chain_path:
            raise DailyResearchServiceReleaseAuditError("CHAIN_MISMATCH", "gate paths differ")
        if (
            _sha(run["gate_sha256"], label="run gate_sha256") != gate_file["sha256"]
            or _sha(audit["gate_sha256"], label="audit gate_sha256") != gate_file["sha256"]
            or _sha(run["audit_sha256"], label="run audit_sha256") != chain_file["sha256"]
            or _sha(audit["audit_sha256"], label="audit audit_sha256") != chain_file["sha256"]
        ):
            raise DailyResearchServiceReleaseAuditError("HASH_MISMATCH", "gate SHA differs")
        common_fields = (
            "run_report_path",
            "run_report_sha256",
            "run_audit_report_path",
            "run_audit_report_sha256",
            "symbol",
            "as_of",
            "evaluation_at",
            "run_status",
            "run_ready",
            "service_stopped",
            "audit_status",
            "audit_ready",
            "status",
            "release_ready",
            "issues",
            "decision_ready",
        )
        for field in common_fields:
            if manifest[field] != report[field]:
                raise DailyResearchServiceReleaseAuditError(
                    "FIELD_MISMATCH", f"release field {field} differs"
                )
        for field in (
            "symbol",
            "as_of",
            "evaluation_at",
            "run_status",
            "run_ready",
            "service_stopped",
        ):
            if field in {"symbol", "as_of", "evaluation_at"} and (
                manifest[field] != run[field] or manifest[field] != audit[field]
            ):
                raise DailyResearchServiceReleaseAuditError(
                    "FIELD_MISMATCH", f"upstream field {field} differs"
                )
        expected_status = (
            "blocked"
            if run["startup_status"] == "blocked"
            else "ready"
            if run["run_ready"]
            else "failed"
        )
        expected_ready = (
            audit["audit_ready"] is True
            and expected_status == "ready"
            and run["startup_status"] == "ready"
            and run["probe_status"] == "ready"
            and run["probe_exit_code"] == 0
            and run["run_ready"] is True
            and run["service_stopped"] is True
            and not run_issues
            and not audit_issues
        )
        expected_audit_ready = audit["audit_ready"] is True
        expected_admission_status = (
            "ready" if expected_ready else "blocked" if expected_audit_ready else "invalid"
        )
        if manifest["release_ready"] != expected_ready or report["release_ready"] != expected_ready:
            raise DailyResearchServiceReleaseAuditError(
                "STATE_MISMATCH", "release_ready is inconsistent"
            )
        if (
            manifest["status"] != expected_admission_status
            or report["status"] != expected_admission_status
        ):
            raise DailyResearchServiceReleaseAuditError(
                "STATE_MISMATCH", "release status is inconsistent"
            )
        if (
            manifest["audit_ready"] is not expected_audit_ready
            or report["audit_ready"] is not expected_audit_ready
        ):
            raise DailyResearchServiceReleaseAuditError(
                "STATE_MISMATCH", "audit_ready is inconsistent"
            )
        if manifest["run_status"] != expected_status:
            raise DailyResearchServiceReleaseAuditError(
                "STATE_MISMATCH", "run_status is inconsistent"
            )
        if manifest["audit_status"] != ("ready" if expected_audit_ready else "invalid"):
            raise DailyResearchServiceReleaseAuditError(
                "STATE_MISMATCH", "audit_status is inconsistent"
            )
        result.update(
            {
                "release_version": manifest["release_version"],
                "manifest_path": manifest_file["relative_path"],
                "manifest_sha256": manifest_file["sha256"],
                "report_path": report_file["relative_path"],
                "report_sha256": report_file["sha256"],
                "run_report_path": run_path,
                "run_report_sha256": run_file["sha256"],
                "run_audit_report_path": audit_path,
                "run_audit_report_sha256": audit_file["sha256"],
                "symbol": manifest["symbol"],
                "as_of": manifest["as_of"],
                "evaluation_at": manifest["evaluation_at"],
                "run_status": expected_status,
                "run_ready": run["run_ready"],
                "service_stopped": run["service_stopped"],
                "audit_status": "ready" if expected_audit_ready else "invalid",
                "audit_ready": expected_audit_ready,
                "status": expected_admission_status,
                "release_ready": expected_ready,
                "issues": [] if expected_ready else (audit_issues or run_issues),
            }
        )
    except DailyResearchServiceReleaseAuditError as exc:
        result["issues"] = [_issue(exc.code, str(exc))]
    return _write_report(result, output_dir=output_dir)


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION",
    "DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME",
    "DailyResearchServiceReleaseAuditError",
    "audit_daily_research_service_release",
]
