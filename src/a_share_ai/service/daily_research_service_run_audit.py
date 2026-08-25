"""Independently audit one bounded daily-research service run."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_launch_gate_startup import (
    DailyResearchServiceLaunchGateStartupError,
    load_daily_research_service_launch_gate_startup,
)
from .daily_research_service_run import (
    DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_VERSION,
)

DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION = "daily-research-service-run-audit-v1"
DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME = "daily_research_service_run_audit_report.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_STATUSES = {"ready", "blocked", "failed", "invalid"}
_STARTUP_STATUSES = {"ready", "blocked", "invalid"}
_PROBE_STATUSES = {None, "ready", "invalid"}
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


class DailyResearchServiceRunAuditError(ValueError):
    """A sanitized, fail-closed service-run audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _safe_message(value: Any) -> str:
    message = str(value)
    message = re.sub(r"(?i)\b[A-Z]:[\\/][^\s\"']*", "<redacted-path>", message)
    return re.sub(r"(?<![A-Za-z0-9])/(?:[^\s\"']+/?)+", "<redacted-path>", message)


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": _safe_message(message)}


def _json_issue(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchServiceRunAuditError("FIELD_INVALID", f"{label} is invalid")
    issues: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not item["code"].strip()
            or not isinstance(item["message"], str)
        ):
            raise DailyResearchServiceRunAuditError(
                "FIELD_INVALID", f"{label}[{index}] is invalid"
            )
        issues.append(_issue(item["code"], item["message"]))
    return issues


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceRunAuditError("HASH_INVALID", f"{label} is invalid")
    return value


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceRunAuditError("PATH_INVALID", f"{label} is invalid")
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive:
        raise DailyResearchServiceRunAuditError("PATH_INVALID", f"{label} must be relative")
    if any(part in {"", ".", ".."} for part in windows_path.parts):
        raise DailyResearchServiceRunAuditError("PATH_INVALID", f"{label} is not normalized")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(windows_path.parts):
        raise DailyResearchServiceRunAuditError("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceRunAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceRunAuditError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceRunAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {
        "path": candidate,
        "relative_path": relative,
        "raw": raw,
        "sha256": sha256_bytes(raw),
    }


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceRunAuditError("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceRunAuditError("INPUT_INVALID", f"{label} must be an object")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceRunAuditError("HASH_MISMATCH", f"{label} self-hash differs")


def _parse_datetime(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceRunAuditError("FIELD_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceRunAuditError("FIELD_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceRunAuditError(
            "FIELD_INVALID", f"{label} must include timezone"
        )
    return value


def _validate_run_report(report: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(report) != _RUN_FIELDS:
        raise DailyResearchServiceRunAuditError("SCHEMA_INVALID", "run report fields are invalid")
    if report["run_version"] != DAILY_RESEARCH_SERVICE_RUN_VERSION:
        raise DailyResearchServiceRunAuditError("VERSION_MISMATCH", "run report version is invalid")
    _self_hash(report, label="run report")
    if report["decision_ready"] is not False:
        raise DailyResearchServiceRunAuditError(
            "DECISION_GATE_INVALID", "run report decision_ready must be false"
        )
    if not isinstance(report["symbol"], str) or not report["symbol"].strip():
        raise DailyResearchServiceRunAuditError("FIELD_INVALID", "run report symbol is invalid")
    _parse_datetime(report["as_of"], label="run report as_of")
    _parse_datetime(report["evaluation_at"], label="run report evaluation_at")
    if (
        not isinstance(report["startup_status"], str)
        or report["startup_status"] not in _STARTUP_STATUSES
    ):
        raise DailyResearchServiceRunAuditError(
            "STATE_INVALID", "run report startup_status is invalid"
        )
    if report["probe_status"] is not None and (
        not isinstance(report["probe_status"], str)
        or report["probe_status"] not in _PROBE_STATUSES
    ):
        raise DailyResearchServiceRunAuditError(
            "STATE_INVALID", "run report probe_status is invalid"
        )
    if report["probe_exit_code"] is not None and (
        isinstance(report["probe_exit_code"], bool)
        or not isinstance(report["probe_exit_code"], int)
        or report["probe_exit_code"] < 0
    ):
        raise DailyResearchServiceRunAuditError(
            "FIELD_INVALID", "run report probe_exit_code is invalid"
        )
    for field in ("service_stopped", "run_ready"):
        if not isinstance(report[field], bool):
            raise DailyResearchServiceRunAuditError(
                "FIELD_INVALID", f"run report {field} is invalid"
            )
    return _json_issue(report["issues"], label="run report issues")


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
        "run_report_path": None,
        "run_report_sha256": None,
        "gate_path": None,
        "gate_sha256": None,
        "audit_path": None,
        "audit_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "run_status": "invalid",
        "startup_status": None,
        "probe_status": None,
        "probe_exit_code": None,
        "service_stopped": False,
        "run_ready": False,
        "audit_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _finalize(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        canonical = dict(report)
        canonical["output_sha256"] = None
        report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
        write_atomic(output_dir / DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME, _json_bytes(report))
    except OSError as exc:
        raise DailyResearchServiceRunAuditError(
            "OUTPUT_UNAVAILABLE", "audit output is unavailable"
        ) from exc
    return report


def _validate_chain(
    run: Mapping[str, Any],
    *,
    root: Path,
    run_file: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    gate_relative = _relative_path(run["gate_path"], label="run report gate_path")
    audit_relative = _relative_path(run["audit_path"], label="run report audit_path")
    gate_file = _safe_file(root / Path(gate_relative), root=root, label="daily launch gate")
    audit_file = _safe_file(
        root / Path(audit_relative), root=root, label="launch gate audit report"
    )
    if gate_file["path"].name != "daily_research_service_launch_gate.json":
        raise DailyResearchServiceRunAuditError(
            "PATH_INVALID", "daily launch gate filename is invalid"
        )
    if audit_file["path"].name != "daily_research_service_launch_gate_audit_report.json":
        raise DailyResearchServiceRunAuditError(
            "PATH_INVALID", "launch gate audit filename is invalid"
        )
    gate_sha = _sha(run["gate_sha256"], label="run report gate_sha256")
    audit_sha = _sha(run["audit_sha256"], label="run report audit_sha256")
    if gate_sha != gate_file["sha256"] or audit_sha != audit_file["sha256"]:
        raise DailyResearchServiceRunAuditError("HASH_MISMATCH", "run report input hash differs")
    startup = load_daily_research_service_launch_gate_startup(
        gate_path=gate_file["path"], audit_path=audit_file["path"], artifact_root=root
    )
    expected_audit_relative = startup.audit_path.relative_to(root).as_posix()
    if (
        startup.audit_report["gate_path"] != gate_relative
        or startup.audit_report["gate_sha256"] != gate_file["sha256"]
        or expected_audit_relative != audit_relative
        or startup.audit_report["symbol"] != run["symbol"]
        or startup.audit_report["as_of"] != run["as_of"]
        or startup.audit_report["evaluation_at"] != run["evaluation_at"]
    ):
        raise DailyResearchServiceRunAuditError(
            "CHAIN_MISMATCH", "run report metadata differs from audited launch chain"
        )
    if run_file["relative_path"] == gate_relative or run_file["relative_path"] == audit_relative:
        raise DailyResearchServiceRunAuditError(
            "PATH_INVALID", "run report inputs must be distinct"
        )
    return gate_file, audit_file, startup


def _derive_state(
    run: Mapping[str, Any],
    *,
    input_issues: list[dict[str, str]],
    launch_ready: bool,
) -> tuple[str, bool, list[dict[str, str]]]:
    startup_status = run["startup_status"]
    if startup_status == "blocked":
        if launch_ready or run["probe_status"] is not None or run["probe_exit_code"] is not None:
            raise DailyResearchServiceRunAuditError(
                "RUN_STATE_MISMATCH", "blocked run has ready or probe state"
            )
        if run["service_stopped"] or run["run_ready"] or not input_issues:
            raise DailyResearchServiceRunAuditError(
                "RUN_STATE_MISMATCH", "blocked run state is invalid"
            )
        if input_issues[0]["code"] != "STARTUP_NOT_READY":
            raise DailyResearchServiceRunAuditError(
                "RUN_STATE_MISMATCH", "blocked run issue is invalid"
            )
        return "blocked", True, input_issues
    if startup_status != "ready" or not launch_ready:
        raise DailyResearchServiceRunAuditError(
            "RUN_STATE_MISMATCH", "run startup state is not auditable"
        )
    if run["run_ready"]:
        if (
            run["probe_status"] != "ready"
            or run["probe_exit_code"] != 0
            or run["service_stopped"] is not True
            or input_issues
        ):
            raise DailyResearchServiceRunAuditError(
                "RUN_STATE_MISMATCH", "ready run state is inconsistent"
            )
        return "ready", True, []
    if not input_issues:
        raise DailyResearchServiceRunAuditError(
            "RUN_STATE_MISMATCH", "failed run must contain issues"
        )
    return "failed", True, input_issues


def audit_daily_research_service_run(
    *, run_report_path: Path, artifact_root: Path, output_dir: Path
) -> dict[str, Any]:
    """Audit a Node66 run report without starting a service or using HTTP."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceRunAuditError("ARTIFACT_ROOT_INVALID", "artifact root is invalid")
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceRunAuditError(
            "OUTPUT_DIR_INVALID", "output directory escapes artifact root"
        ) from exc
    result = _base_report()
    try:
        run_file = _safe_file(run_report_path, root=root, label="run report")
        if run_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME:
            raise DailyResearchServiceRunAuditError(
                "PATH_INVALID", "run report filename is invalid"
            )
        result["run_report_path"] = run_file["relative_path"]
        result["run_report_sha256"] = run_file["sha256"]
        run = _read_json(run_file["raw"], label="run report")
        input_issues = _validate_run_report(run)
        gate_file, audit_file, startup = _validate_chain(run, root=root, run_file=run_file)
        run_status, audit_ready, issues = _derive_state(
            run,
            input_issues=input_issues,
            launch_ready=startup.launch_ready,
        )
        result.update(
            {
                "gate_path": gate_file["relative_path"],
                "gate_sha256": gate_file["sha256"],
                "audit_path": audit_file["relative_path"],
                "audit_sha256": audit_file["sha256"],
                "symbol": run["symbol"],
                "as_of": run["as_of"],
                "evaluation_at": run["evaluation_at"],
                "run_status": run_status,
                "startup_status": run["startup_status"],
                "probe_status": run["probe_status"],
                "probe_exit_code": run["probe_exit_code"],
                "service_stopped": run["service_stopped"],
                "run_ready": run_status == "ready",
                "audit_ready": audit_ready,
                "issues": issues,
            }
        )
    except DailyResearchServiceRunAuditError as exc:
        result["issues"] = [_issue(exc.code, str(exc))]
    except DailyResearchServiceLaunchGateStartupError as exc:
        result["issues"] = [_issue(exc.code, str(exc))]
    return _finalize(result, output_dir=output_dir)


__all__ = [
    "DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION",
    "DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME",
    "DailyResearchServiceRunAuditError",
    "audit_daily_research_service_run",
]
