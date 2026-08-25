"""Build a read-only, path-bound daily research startup handoff."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_admission import DAILY_RESEARCH_ADMISSION_VERSION
from .daily_research_run import DAILY_RESEARCH_RUN_VERSION
from .daily_research_run_audit import DAILY_RESEARCH_RUN_AUDIT_VERSION

DAILY_RESEARCH_HANDOFF_VERSION = "daily-research-handoff-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_STATUSES = {"ready", "blocked"}
_ADMISSION_STATUSES = {"ready", "stale", "calendar_unknown", "blocked", "invalid"}
_ADMISSION_FRESHNESS = {
    "ready": "fresh",
    "stale": "stale",
    "calendar_unknown": "calendar_unknown",
    "blocked": "blocked",
    "invalid": "invalid",
}
_RUN_FIELDS = {
    "run_version",
    "source_mode",
    "symbol",
    "start_date",
    "end_date",
    "as_of",
    "received_at",
    "status",
    "stages",
    "analysis_input_path",
    "analysis_input_sha256",
    "analysis_input_ready",
    "market_context_summary_version",
    "relative_strength_version",
    "issues",
    "decision_ready",
}
_AUDIT_FIELDS = {
    "audit_version",
    "run_report_path",
    "run_report_sha256",
    "status",
    "audit_ready",
    "run_status",
    "symbol",
    "as_of",
    "received_at",
    "stage_count",
    "failed_stage",
    "analysis_input_path",
    "analysis_input_sha256",
    "analysis_input_ready",
    "market_context_summary_version",
    "relative_strength_version",
    "issues",
    "decision_ready",
    "output_sha256",
}
_ADMISSION_FIELDS = {
    "admission_version",
    "run_report_path",
    "run_audit_report_path",
    "calendar_path",
    "calendar_report_path",
    "run_report_sha256",
    "run_audit_report_sha256",
    "calendar_sha256",
    "calendar_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "expected_latest_trading_date",
    "status",
    "freshness_status",
    "audit_ready",
    "analysis_input_ready",
    "admission_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
_ADMISSION_PATH_FIELDS = (
    "run_report_path",
    "run_audit_report_path",
    "calendar_path",
    "calendar_report_path",
)
_ADMISSION_REFERENCE_FIELDS = (
    *_ADMISSION_PATH_FIELDS,
    "run_report_sha256",
    "run_audit_report_sha256",
    "calendar_sha256",
    "calendar_report_sha256",
)
_EXPECTED_STAGE_COUNT = 9


class DailyResearchHandoffError(ValueError):
    """A fail-closed handoff validation or output error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _safe_message(value: Any) -> str:
    message = str(value)
    message = re.sub(r"(?i)(?:\b[A-Z]:[\\/][^\s\"']*|\\\\[^\s\"']+)", "<redacted-path>", message)
    return re.sub(r"(?<![A-Za-z0-9])/(?:[^\s\"']+/?)+", "<redacted-path>", message)


def _safe_issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchHandoffError("ISSUES_INVALID", f"{label} issues are invalid")
    safe: list[dict[str, str]] = []
    for index, issue in enumerate(value):
        if (
            not isinstance(issue, Mapping)
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("message"), str)
        ):
            raise DailyResearchHandoffError(
                "ISSUES_INVALID", f"{label} issues[{index}] is invalid"
            )
        safe.append({"code": issue["code"], "message": _safe_message(issue["message"])})
    return safe


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchHandoffError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchHandoffError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return value


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchHandoffError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchHandoffError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchHandoffError("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchHandoffError("PATH_INVALID", f"{label} must be relative")
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or any(part in {"", ".", ".."} for part in path.parts):
        raise DailyResearchHandoffError("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(path.parts):
        raise DailyResearchHandoffError("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchHandoffError("HASH_INVALID", f"{label} is invalid")
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _validate_sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchHandoffError("HASH_MISMATCH", f"{label} self-hash is invalid")


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchHandoffError("TIME_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchHandoffError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchHandoffError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _parse_date(value: Any, *, label: str) -> date:
    if not isinstance(value, str):
        raise DailyResearchHandoffError("DATE_INVALID", f"{label} is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DailyResearchHandoffError("DATE_INVALID", f"{label} is invalid") from exc


def _validate_run(report: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(report) != _RUN_FIELDS:
        raise DailyResearchHandoffError("RUN_FIELDS_INVALID", "run report fields are invalid")
    if report.get("run_version") != DAILY_RESEARCH_RUN_VERSION:
        raise DailyResearchHandoffError("VERSION_MISMATCH", "run report version is invalid")
    if report.get("source_mode") != "public-read-only":
        raise DailyResearchHandoffError("SOURCE_MODE_INVALID", "run source mode is invalid")
    if not isinstance(report.get("symbol"), str) or not report["symbol"].strip():
        raise DailyResearchHandoffError("FIELD_INVALID", "run symbol is invalid")
    start = _parse_date(report.get("start_date"), label="run.start_date")
    end = _parse_date(report.get("end_date"), label="run.end_date")
    if start > end:
        raise DailyResearchHandoffError("DATE_INVALID", "run date range is reversed")
    as_of = _parse_datetime(report.get("as_of"), label="run.as_of")
    received_at = _parse_datetime(report.get("received_at"), label="run.received_at")
    if received_at != as_of:
        raise DailyResearchHandoffError("TIME_MISMATCH", "run received_at differs from as_of")
    if report.get("status") not in _RUN_STATUSES:
        raise DailyResearchHandoffError("STATUS_INVALID", "run status is invalid")
    if not isinstance(report.get("analysis_input_ready"), bool):
        raise DailyResearchHandoffError("FIELD_INVALID", "analysis_input_ready is invalid")
    if report["status"] == "ready" and report["analysis_input_ready"] is not True:
        raise DailyResearchHandoffError(
            "READY_STATE_INVALID", "ready run analysis_input_ready must be true"
        )
    if report.get("decision_ready") is not False:
        raise DailyResearchHandoffError(
            "DECISION_GATE_INVALID", "run decision_ready must be false"
        )
    return _safe_issues(report.get("issues"), label="run")


def _validate_audit(
    audit: Mapping[str, Any], *, run_file: Mapping[str, Any], root: Path
) -> list[dict[str, str]]:
    if set(audit) != _AUDIT_FIELDS:
        raise DailyResearchHandoffError("AUDIT_FIELDS_INVALID", "run audit fields are invalid")
    _validate_self_hash(audit, label="run audit report")
    if audit.get("audit_version") != DAILY_RESEARCH_RUN_AUDIT_VERSION:
        raise DailyResearchHandoffError("VERSION_MISMATCH", "run audit version is invalid")
    if audit.get("status") not in _RUN_STATUSES or audit.get("run_status") not in _RUN_STATUSES:
        raise DailyResearchHandoffError("STATUS_INVALID", "run audit status is invalid")
    if audit.get("run_status") != audit.get("status"):
        raise DailyResearchHandoffError("FIELD_MISMATCH", "run audit status differs")
    if not isinstance(audit.get("audit_ready"), bool):
        raise DailyResearchHandoffError("FIELD_INVALID", "audit_ready is invalid")
    if audit["audit_ready"] is not (audit["status"] == "ready"):
        raise DailyResearchHandoffError("FIELD_MISMATCH", "audit readiness differs from status")
    if (
        not isinstance(audit.get("stage_count"), int)
        or audit["stage_count"] != _EXPECTED_STAGE_COUNT
    ):
        raise DailyResearchHandoffError("FIELD_INVALID", "run audit stage_count is invalid")
    if not isinstance(audit.get("analysis_input_ready"), bool):
        raise DailyResearchHandoffError(
            "FIELD_INVALID", "run audit analysis_input_ready is invalid"
        )
    if audit["analysis_input_ready"] != run_file["payload"].get("analysis_input_ready"):
        raise DailyResearchHandoffError(
            "CHAIN_MISMATCH", "run and audit analysis_input_ready differ"
        )
    if audit.get("run_report_path") != run_file["relative_path"]:
        raise DailyResearchHandoffError("CHAIN_MISMATCH", "audit run report path differs")
    if audit.get("run_report_sha256") != run_file["sha256"]:
        raise DailyResearchHandoffError("HASH_MISMATCH", "audit run report SHA differs")
    if audit.get("symbol") != run_file["payload"].get("symbol"):
        raise DailyResearchHandoffError("CHAIN_MISMATCH", "run and audit symbols differ")
    if audit.get("as_of") != run_file["payload"].get("as_of"):
        raise DailyResearchHandoffError("CHAIN_MISMATCH", "run and audit as_of differ")
    _parse_datetime(audit.get("as_of"), label="run audit as_of")
    _parse_datetime(audit.get("received_at"), label="run audit received_at")
    if audit.get("received_at") != run_file["payload"].get("received_at"):
        raise DailyResearchHandoffError("CHAIN_MISMATCH", "run and audit received_at differ")
    if audit.get("decision_ready") is not False:
        raise DailyResearchHandoffError(
            "DECISION_GATE_INVALID", "run audit decision_ready must be false"
        )
    return _safe_issues(audit.get("issues"), label="run audit")


def _validate_admission_pair(
    admission: Mapping[str, Any], report: Mapping[str, Any], *, root: Path
) -> list[dict[str, str]]:
    for label, payload in (("daily admission", admission), ("daily admission report", report)):
        if set(payload) != _ADMISSION_FIELDS:
            raise DailyResearchHandoffError(
                "ADMISSION_FIELDS_INVALID", f"{label} fields are invalid"
            )
        _validate_self_hash(payload, label=label)
        if payload.get("admission_version") != DAILY_RESEARCH_ADMISSION_VERSION:
            raise DailyResearchHandoffError("VERSION_MISMATCH", f"{label} version is invalid")
        if payload.get("status") not in _ADMISSION_STATUSES:
            raise DailyResearchHandoffError("STATUS_INVALID", f"{label} status is invalid")
        if payload.get("freshness_status") != _ADMISSION_FRESHNESS[payload["status"]]:
            raise DailyResearchHandoffError("FIELD_MISMATCH", f"{label} freshness status differs")
        for field in ("audit_ready", "analysis_input_ready", "admission_ready"):
            if not isinstance(payload.get(field), bool):
                raise DailyResearchHandoffError("FIELD_INVALID", f"{label} {field} is invalid")
        if payload["admission_ready"] is not (payload["status"] == "ready"):
            raise DailyResearchHandoffError("FIELD_MISMATCH", f"{label} admission gate is invalid")
        if payload["status"] == "ready" and not (
            payload["audit_ready"] and payload["analysis_input_ready"]
        ):
            raise DailyResearchHandoffError("FIELD_MISMATCH", f"{label} ready gates are invalid")
        if payload.get("symbol") is not None and (
            not isinstance(payload["symbol"], str) or not payload["symbol"].strip()
        ):
            raise DailyResearchHandoffError("FIELD_INVALID", f"{label} symbol is invalid")
        if payload.get("as_of") is not None:
            _parse_datetime(payload["as_of"], label=f"{label}.as_of")
        _parse_datetime(payload.get("evaluation_at"), label=f"{label}.evaluation_at")
        _safe_issues(payload.get("issues"), label=label)
        if payload.get("decision_ready") is not False:
            raise DailyResearchHandoffError(
                "DECISION_GATE_INVALID", f"{label} decision_ready must be false"
            )
    for field in _ADMISSION_FIELDS:
        if admission.get(field) != report.get(field):
            raise DailyResearchHandoffError(
                "FIELD_MISMATCH", f"daily admission {field} differs"
            )
    has_refs = any(admission.get(field) is not None for field in _ADMISSION_PATH_FIELDS)
    if has_refs:
        for path_field, sha_field in (
            ("run_report_path", "run_report_sha256"),
            ("run_audit_report_path", "run_audit_report_sha256"),
            ("calendar_path", "calendar_sha256"),
            ("calendar_report_path", "calendar_report_sha256"),
        ):
            relative = _relative_path(
                admission.get(path_field), label=f"daily admission {path_field}"
            )
            file_info = _safe_file(
                root / relative, root=root, label=f"daily admission {path_field}"
            )
            if file_info["relative_path"] != relative:
                raise DailyResearchHandoffError(
                    "PATH_INVALID", f"daily admission {path_field} is not normalized"
                )
            if _validate_sha(
                admission.get(sha_field), label=f"daily admission {sha_field}"
            ) != file_info["sha256"]:
                raise DailyResearchHandoffError(
                    "HASH_MISMATCH", f"daily admission {path_field} hash differs"
                )
    elif any(admission.get(field) is not None for field in _ADMISSION_REFERENCE_FIELDS):
        raise DailyResearchHandoffError(
            "FIELD_MISMATCH", "daily admission references are incomplete"
        )
    return _safe_issues(admission.get("issues"), label="daily admission")


def _base_report() -> dict[str, Any]:
    return {
        "handoff_version": DAILY_RESEARCH_HANDOFF_VERSION,
        "status": "invalid",
        "run_report_path": None,
        "run_report_sha256": None,
        "run_audit_report_path": None,
        "run_audit_report_sha256": None,
        "admission_path": None,
        "admission_sha256": None,
        "admission_report_path": None,
        "admission_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "run_status": None,
        "audit_ready": False,
        "admission_status": None,
        "admission_ready": False,
        "handoff_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_outputs(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        report["output_sha256"] = sha256_bytes(_json_bytes(report))
        raw = _json_bytes(report)
        write_atomic(output_dir / "daily_research_handoff.json", raw)
        write_atomic(output_dir / "daily_research_handoff_report.json", raw)
    except OSError as exc:
        raise DailyResearchHandoffError(
            "OUTPUT_UNAVAILABLE", "handoff output is unavailable"
        ) from exc
    return report


def build_daily_research_handoff(
    *,
    run_report_path: Path,
    run_audit_report_path: Path,
    admission_path: Path,
    admission_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Aggregate existing reports without rerunning research or external I/O."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchHandoffError("ARTIFACT_ROOT_INVALID", "artifact root is unavailable")
    try:
        output_dir.resolve().relative_to(root)
    except ValueError as exc:
        raise DailyResearchHandoffError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir must be inside artifact-root"
        ) from exc
    result = _base_report()
    try:
        run_file = _safe_file(run_report_path, root=root, label="run report")
        run = _read_json(run_file["path"], label="run report")
        run_file["payload"] = run
        run_issues = _validate_run(run)
        audit_file = _safe_file(run_audit_report_path, root=root, label="run audit report")
        audit = _read_json(audit_file["path"], label="run audit report")
        audit_issues = _validate_audit(audit, run_file=run_file, root=root)
        admission_file = _safe_file(admission_path, root=root, label="daily admission")
        admission = _read_json(admission_file["path"], label="daily admission")
        admission_report_file = _safe_file(
            admission_report_path, root=root, label="daily admission report"
        )
        admission_report = _read_json(
            admission_report_file["path"], label="daily admission report"
        )
        admission_issues = _validate_admission_pair(admission, admission_report, root=root)
        if admission.get("run_report_path") not in {None, run_file["relative_path"]}:
            raise DailyResearchHandoffError(
                "CHAIN_MISMATCH", "admission run report path differs"
            )
        if admission.get("run_audit_report_path") not in {None, audit_file["relative_path"]}:
            raise DailyResearchHandoffError(
                "CHAIN_MISMATCH", "admission run audit report path differs"
            )
        if admission.get("symbol") is not None and admission["symbol"] != run["symbol"]:
            raise DailyResearchHandoffError("CHAIN_MISMATCH", "run and admission symbols differ")
        if admission.get("as_of") is not None and admission["as_of"] != run["as_of"]:
            raise DailyResearchHandoffError("CHAIN_MISMATCH", "run and admission as_of differ")
        evaluation_at = _parse_datetime(
            admission["evaluation_at"], label="admission.evaluation_at"
        )
        if evaluation_at < _parse_datetime(run["as_of"], label="run.as_of"):
            raise DailyResearchHandoffError(
                "CHAIN_MISMATCH", "admission evaluation_at precedes run as_of"
            )
        result.update(
            status="blocked",
            run_report_path=run_file["relative_path"],
            run_report_sha256=run_file["sha256"],
            run_audit_report_path=audit_file["relative_path"],
            run_audit_report_sha256=audit_file["sha256"],
            admission_path=admission_file["relative_path"],
            admission_sha256=admission_file["sha256"],
            admission_report_path=admission_report_file["relative_path"],
            admission_report_sha256=admission_report_file["sha256"],
            symbol=run["symbol"],
            as_of=run["as_of"],
            evaluation_at=admission["evaluation_at"],
            run_status=run["status"],
            audit_ready=audit["audit_ready"],
            admission_status=admission["status"],
            admission_ready=admission["admission_ready"],
            issues=[*run_issues, *audit_issues, *admission_issues],
        )
        if run["status"] == "ready" and audit["audit_ready"] and admission["admission_ready"]:
            result.update(status="ready", handoff_ready=True, issues=[])
        elif admission["status"] == "invalid":
            result["status"] = "invalid"
    except DailyResearchHandoffError as exc:
        result["status"] = "invalid"
        result["issues"] = [{"code": exc.code, "message": _safe_message(exc)}]
    return _write_outputs(result, output_dir=output_dir)
