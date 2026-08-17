"""Independently audit a daily research handoff without rebuilding it."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_admission import DAILY_RESEARCH_ADMISSION_VERSION
from .daily_research_handoff import DAILY_RESEARCH_HANDOFF_VERSION
from .daily_research_run import DAILY_RESEARCH_RUN_VERSION
from .daily_research_run_audit import DAILY_RESEARCH_RUN_AUDIT_VERSION

DAILY_RESEARCH_HANDOFF_AUDIT_VERSION = "daily-research-handoff-audit-v1"
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
_HANDOFF_STATUSES = {"ready", "blocked"}
_HANDOFF_FIELDS = {
    "handoff_version",
    "status",
    "run_report_path",
    "run_report_sha256",
    "run_audit_report_path",
    "run_audit_report_sha256",
    "admission_path",
    "admission_sha256",
    "admission_report_path",
    "admission_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "run_status",
    "audit_ready",
    "admission_status",
    "admission_ready",
    "handoff_ready",
    "issues",
    "decision_ready",
    "output_sha256",
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


class DailyResearchHandoffAuditError(ValueError):
    """A fail-closed handoff audit error."""

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
        raise DailyResearchHandoffAuditError("ISSUES_INVALID", f"{label} issues are invalid")
    result: list[dict[str, str]] = []
    for index, issue in enumerate(value):
        if (
            not isinstance(issue, Mapping)
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("message"), str)
        ):
            raise DailyResearchHandoffAuditError(
                "ISSUES_INVALID", f"{label} issues[{index}] is invalid"
            )
        result.append({"code": issue["code"], "message": _safe_message(issue["message"])})
    return result


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchHandoffAuditError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchHandoffAuditError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return value


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchHandoffAuditError("HASH_INVALID", f"{label} is invalid")
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _validate_sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchHandoffAuditError("HASH_MISMATCH", f"{label} self-hash is invalid")


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchHandoffAuditError("TIME_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchHandoffAuditError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchHandoffAuditError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchHandoffAuditError("PATH_INVALID", f"{label} must be relative")
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or any(part in {"", ".", ".."} for part in path.parts):
        raise DailyResearchHandoffAuditError("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(path.parts):
        raise DailyResearchHandoffAuditError("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchHandoffAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchHandoffAuditError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchHandoffAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _resolve_declared(
    value: Any, *, root: Path, label: str, expected_sha: Any
) -> dict[str, Any]:
    relative = _relative_path(value, label=label)
    file_info = _safe_file(root / relative, root=root, label=label)
    if file_info["relative_path"] != relative:
        raise DailyResearchHandoffAuditError("PATH_INVALID", f"{label} is not normalized")
    if _validate_sha(expected_sha, label=f"{label} SHA") != file_info["sha256"]:
        raise DailyResearchHandoffAuditError("HASH_MISMATCH", f"{label} hash differs")
    return file_info


def _validate_issues(value: Any, *, label: str) -> list[dict[str, str]]:
    return _safe_issues(value, label=label)


def _validate_run(run: Mapping[str, Any]) -> datetime:
    if set(run) != _RUN_FIELDS:
        raise DailyResearchHandoffAuditError("RUN_FIELDS_INVALID", "run report fields are invalid")
    if run.get("run_version") != DAILY_RESEARCH_RUN_VERSION:
        raise DailyResearchHandoffAuditError("VERSION_MISMATCH", "run report version is invalid")
    if run.get("source_mode") != "public-read-only":
        raise DailyResearchHandoffAuditError("SOURCE_MODE_INVALID", "run source mode is invalid")
    if not isinstance(run.get("symbol"), str) or not run["symbol"].strip():
        raise DailyResearchHandoffAuditError("FIELD_INVALID", "run symbol is invalid")
    if not isinstance(run.get("status"), str) or run["status"] not in _RUN_STATUSES:
        raise DailyResearchHandoffAuditError("STATUS_INVALID", "run status is invalid")
    if not isinstance(run.get("analysis_input_ready"), bool):
        raise DailyResearchHandoffAuditError("FIELD_INVALID", "run readiness is invalid")
    if run["status"] == "ready" and run["analysis_input_ready"] is not True:
        raise DailyResearchHandoffAuditError(
            "READY_STATE_INVALID", "ready run is not analysis-ready"
        )
    try:
        start_date = datetime.fromisoformat(str(run.get("start_date")))
        end_date = datetime.fromisoformat(str(run.get("end_date")))
    except ValueError as exc:
        raise DailyResearchHandoffAuditError(
            "DATE_INVALID", "run date range is invalid"
        ) from exc
    if start_date.date() > end_date.date():
        raise DailyResearchHandoffAuditError("DATE_INVALID", "run date range is reversed")
    if not isinstance(run.get("stages"), list) or len(run["stages"]) != 9:
        raise DailyResearchHandoffAuditError("STAGES_INVALID", "run stage list is invalid")
    as_of = _parse_datetime(run.get("as_of"), label="run.as_of")
    if _parse_datetime(run.get("received_at"), label="run.received_at") != as_of:
        raise DailyResearchHandoffAuditError("TIME_MISMATCH", "run received_at differs from as_of")
    if run.get("decision_ready") is not False:
        raise DailyResearchHandoffAuditError(
            "DECISION_GATE_INVALID", "run decision_ready must be false"
        )
    _validate_issues(run.get("issues"), label="run")
    return as_of


def _validate_audit(
    audit: Mapping[str, Any], *, run: Mapping[str, Any], run_file: Mapping[str, Any]
) -> list[dict[str, str]]:
    if set(audit) != _AUDIT_FIELDS:
        raise DailyResearchHandoffAuditError("AUDIT_FIELDS_INVALID", "run audit fields are invalid")
    _validate_self_hash(audit, label="run audit report")
    if audit.get("audit_version") != DAILY_RESEARCH_RUN_AUDIT_VERSION:
        raise DailyResearchHandoffAuditError("VERSION_MISMATCH", "run audit version is invalid")
    if audit.get("status") not in _RUN_STATUSES or audit.get("run_status") != audit.get("status"):
        raise DailyResearchHandoffAuditError("STATUS_INVALID", "run audit status is invalid")
    if audit.get("audit_ready") is not (audit.get("status") == "ready"):
        raise DailyResearchHandoffAuditError(
            "FIELD_MISMATCH", "audit readiness differs from status"
        )
    if audit.get("stage_count") != 9:
        raise DailyResearchHandoffAuditError("STAGES_INVALID", "audit stage count is invalid")
    if not isinstance(audit.get("analysis_input_ready"), bool):
        raise DailyResearchHandoffAuditError(
            "FIELD_INVALID", "audit analysis readiness is invalid"
        )
    if audit.get("analysis_input_ready") != run.get("analysis_input_ready"):
        raise DailyResearchHandoffAuditError(
            "CHAIN_MISMATCH", "audit analysis readiness differs"
        )
    if audit.get("run_report_path") != run_file["relative_path"]:
        raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "audit run path differs")
    if audit.get("run_report_sha256") != run_file["sha256"]:
        raise DailyResearchHandoffAuditError("HASH_MISMATCH", "audit run hash differs")
    if audit.get("run_status") != run.get("status"):
        raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "audit run status differs")
    if audit.get("symbol") != run.get("symbol") or audit.get("as_of") != run.get("as_of"):
        raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "run audit metadata differs")
    if audit.get("received_at") != run.get("received_at"):
        raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "run audit received_at differs")
    if audit.get("decision_ready") is not False:
        raise DailyResearchHandoffAuditError(
            "DECISION_GATE_INVALID", "run audit decision_ready must be false"
        )
    _validate_issues(audit.get("issues"), label="run audit")
    return _parse_datetime(audit.get("as_of"), label="run audit as_of")


def _validate_admission(
    admission: Mapping[str, Any], report: Mapping[str, Any], *, root: Path
) -> list[dict[str, str]]:
    for label, payload in (("daily admission", admission), ("daily admission report", report)):
        if set(payload) != _ADMISSION_FIELDS:
            raise DailyResearchHandoffAuditError(
                "ADMISSION_FIELDS_INVALID", f"{label} fields are invalid"
            )
        _validate_self_hash(payload, label=label)
        if payload.get("admission_version") != DAILY_RESEARCH_ADMISSION_VERSION:
            raise DailyResearchHandoffAuditError("VERSION_MISMATCH", f"{label} version is invalid")
        status = payload.get("status")
        if status not in _ADMISSION_STATUSES:
            raise DailyResearchHandoffAuditError("STATUS_INVALID", f"{label} status is invalid")
        if payload.get("freshness_status") != _ADMISSION_FRESHNESS[status]:
            raise DailyResearchHandoffAuditError("FIELD_MISMATCH", f"{label} freshness differs")
        for field in ("audit_ready", "analysis_input_ready", "admission_ready"):
            if not isinstance(payload.get(field), bool):
                raise DailyResearchHandoffAuditError("FIELD_INVALID", f"{label} {field} is invalid")
        if payload["admission_ready"] is not (status == "ready"):
            raise DailyResearchHandoffAuditError("FIELD_MISMATCH", f"{label} gate differs")
        if status == "ready" and (
            not payload["audit_ready"] or not payload["analysis_input_ready"]
        ):
            raise DailyResearchHandoffAuditError(
                "READY_STATE_INVALID", f"{label} audit is not ready"
            )
        if payload.get("symbol") is not None and (
            not isinstance(payload["symbol"], str) or not payload["symbol"].strip()
        ):
            raise DailyResearchHandoffAuditError("FIELD_INVALID", f"{label} symbol is invalid")
        if payload.get("as_of") is not None:
            _parse_datetime(payload["as_of"], label=f"{label}.as_of")
        _parse_datetime(payload.get("evaluation_at"), label=f"{label}.evaluation_at")
        if payload.get("decision_ready") is not False:
            raise DailyResearchHandoffAuditError(
                "DECISION_GATE_INVALID", f"{label} decision_ready must be false"
            )
        _validate_issues(payload.get("issues"), label=label)
    for field in _ADMISSION_FIELDS:
        if admission.get(field) != report.get(field):
            raise DailyResearchHandoffAuditError("FIELD_MISMATCH", f"admission {field} differs")
    has_refs = any(admission.get(field) is not None for field in _ADMISSION_PATH_FIELDS)
    if admission.get("status") == "ready" and not all(
        admission.get(field) is not None for field in _ADMISSION_REFERENCE_FIELDS
    ):
        raise DailyResearchHandoffAuditError(
            "ADMISSION_REFERENCES_MISSING", "ready daily admission references are incomplete"
        )
    if has_refs:
        for path_field, sha_field in (
            ("run_report_path", "run_report_sha256"),
            ("run_audit_report_path", "run_audit_report_sha256"),
            ("calendar_path", "calendar_sha256"),
            ("calendar_report_path", "calendar_report_sha256"),
        ):
            _resolve_declared(
                admission.get(path_field),
                root=root,
                label=f"daily admission {path_field}",
                expected_sha=admission.get(sha_field),
            )
    elif any(admission.get(field) is not None for field in _ADMISSION_REFERENCE_FIELDS):
        raise DailyResearchHandoffAuditError(
            "FIELD_MISMATCH", "daily admission references are incomplete"
        )
    return _safe_issues(admission.get("issues"), label="daily admission")


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": DAILY_RESEARCH_HANDOFF_AUDIT_VERSION,
        "handoff_path": None,
        "handoff_sha256": None,
        "handoff_report_path": None,
        "handoff_report_sha256": None,
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
        "status": "invalid",
        "handoff_ready": False,
        "audit_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        report["output_sha256"] = sha256_bytes(_json_bytes(report))
        write_atomic(output_dir / "daily_research_handoff_audit_report.json", _json_bytes(report))
    except OSError as exc:
        raise DailyResearchHandoffAuditError(
            "OUTPUT_UNAVAILABLE", "handoff audit output is unavailable"
        ) from exc
    return report


def audit_daily_research_handoff(
    *, handoff_path: Path, handoff_report_path: Path, artifact_root: Path, output_dir: Path
) -> dict[str, Any]:
    """Audit an existing handoff and its referenced bytes without rebuilding it."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchHandoffAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is unavailable"
        )
    try:
        output_dir.resolve().relative_to(root)
    except ValueError as exc:
        raise DailyResearchHandoffAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir must be inside artifact-root"
        ) from exc
    result = _base_report()
    try:
        handoff_file = _safe_file(handoff_path, root=root, label="handoff")
        handoff_report_file = _safe_file(handoff_report_path, root=root, label="handoff report")
        result.update(
            handoff_path=handoff_file["relative_path"],
            handoff_sha256=handoff_file["sha256"],
            handoff_report_path=handoff_report_file["relative_path"],
            handoff_report_sha256=handoff_report_file["sha256"],
        )
        if handoff_file["relative_path"] == handoff_report_file["relative_path"]:
            raise DailyResearchHandoffAuditError("PATH_INVALID", "handoff inputs must be distinct")
        handoff = _read_json(handoff_file["path"], label="handoff")
        handoff_report = _read_json(handoff_report_file["path"], label="handoff report")
        for label, payload in (("handoff", handoff), ("handoff report", handoff_report)):
            if set(payload) != _HANDOFF_FIELDS:
                raise DailyResearchHandoffAuditError(
                    "HANDOFF_FIELDS_INVALID", f"{label} fields are invalid"
                )
            _validate_self_hash(payload, label=label)
            if payload.get("handoff_version") != DAILY_RESEARCH_HANDOFF_VERSION:
                raise DailyResearchHandoffAuditError(
                    "VERSION_MISMATCH", f"{label} version is invalid"
                )
            if payload.get("decision_ready") is not False:
                raise DailyResearchHandoffAuditError(
                    "DECISION_GATE_INVALID", f"{label} decision_ready must be false"
                )
            if payload.get("status") not in _HANDOFF_STATUSES:
                raise DailyResearchHandoffAuditError("STATUS_INVALID", f"{label} status is invalid")
            if not isinstance(payload.get("handoff_ready"), bool):
                raise DailyResearchHandoffAuditError(
                    "FIELD_INVALID", f"{label} handoff_ready is invalid"
                )
            _safe_issues(payload.get("issues"), label=label)
        if handoff != handoff_report:
            raise DailyResearchHandoffAuditError("FIELD_MISMATCH", "handoff and report differ")
        run_file = _resolve_declared(
            handoff["run_report_path"],
            root=root,
            label="handoff run report",
            expected_sha=handoff["run_report_sha256"],
        )
        run = _read_json(run_file["path"], label="run report")
        run_as_of = _validate_run(run)
        audit_file = _resolve_declared(
            handoff["run_audit_report_path"],
            root=root,
            label="handoff run audit report",
            expected_sha=handoff["run_audit_report_sha256"],
        )
        audit = _read_json(audit_file["path"], label="run audit report")
        audit_as_of = _validate_audit(audit, run=run, run_file=run_file)
        if audit_as_of != run_as_of:
            raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "run and audit as_of differ")
        admission_file = _resolve_declared(
            handoff["admission_path"],
            root=root,
            label="handoff admission",
            expected_sha=handoff["admission_sha256"],
        )
        admission_report_file = _resolve_declared(
            handoff["admission_report_path"],
            root=root,
            label="handoff admission report",
            expected_sha=handoff["admission_report_sha256"],
        )
        admission = _read_json(admission_file["path"], label="daily admission")
        admission_report = _read_json(
            admission_report_file["path"], label="daily admission report"
        )
        admission_issues = _validate_admission(admission, admission_report, root=root)
        if admission["status"] == "invalid":
            raise DailyResearchHandoffAuditError(
                "UPSTREAM_INVALID", "invalid daily admission cannot be audited"
            )
        if admission["status"] == "ready" and (
            run["status"] != "ready"
            or not audit["audit_ready"]
            or not run["analysis_input_ready"]
            or not admission["analysis_input_ready"]
        ):
            raise DailyResearchHandoffAuditError(
                "CHAIN_MISMATCH", "ready admission has non-ready upstream state"
            )
        if admission["analysis_input_ready"] != run["analysis_input_ready"]:
            raise DailyResearchHandoffAuditError(
                "CHAIN_MISMATCH", "admission analysis readiness differs"
            )
        if admission.get("run_report_path") not in {None, run_file["relative_path"]}:
            raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "admission run path differs")
        if admission.get("run_audit_report_path") not in {None, audit_file["relative_path"]}:
            raise DailyResearchHandoffAuditError(
                "CHAIN_MISMATCH", "admission audit path differs"
            )
        if handoff["symbol"] != run["symbol"]:
            raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "handoff symbol differs")
        if handoff["as_of"] != run["as_of"]:
            raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "handoff as_of differs")
        if admission.get("symbol") is not None and admission["symbol"] != run["symbol"]:
            raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "admission symbol differs")
        if admission.get("as_of") is not None and admission["as_of"] != run["as_of"]:
            raise DailyResearchHandoffAuditError("CHAIN_MISMATCH", "admission as_of differs")
        if handoff["evaluation_at"] != admission["evaluation_at"]:
            raise DailyResearchHandoffAuditError(
                "CHAIN_MISMATCH", "handoff evaluation_at differs"
            )
        if _parse_datetime(handoff["evaluation_at"], label="handoff.evaluation_at") < run_as_of:
            raise DailyResearchHandoffAuditError(
                "CHAIN_MISMATCH", "handoff evaluation_at precedes run as_of"
            )
        expected_chain = {
            "run_status": run["status"],
            "audit_ready": audit["audit_ready"],
            "admission_status": admission["status"],
            "admission_ready": admission["admission_ready"],
        }
        for field, expected in expected_chain.items():
            if handoff[field] != expected:
                raise DailyResearchHandoffAuditError(
                    "CHAIN_MISMATCH", f"handoff {field} differs"
                )
        expected_handoff_ready = (
            run["status"] == "ready"
            and audit["audit_ready"] is True
            and admission["status"] == "ready"
            and admission["admission_ready"] is True
        )
        expected_handoff_status = "ready" if expected_handoff_ready else "blocked"
        if handoff["status"] != expected_handoff_status:
            raise DailyResearchHandoffAuditError(
                "CHAIN_MISMATCH", "handoff status does not match upstream state"
            )
        if handoff["handoff_ready"] is not expected_handoff_ready:
            raise DailyResearchHandoffAuditError(
                "CHAIN_MISMATCH", "handoff readiness does not match upstream state"
            )
        result.update(
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
            status=handoff["status"],
            handoff_ready=handoff["handoff_ready"],
            issues=admission_issues,
        )
        if handoff["status"] == "ready" and (
            not handoff["handoff_ready"]
            or run["status"] != "ready"
            or not audit["audit_ready"]
            or not admission["admission_ready"]
        ):
            raise DailyResearchHandoffAuditError(
                "READY_STATE_INVALID", "ready handoff chain is invalid"
            )
        if handoff["status"] == "blocked" and handoff["handoff_ready"]:
            raise DailyResearchHandoffAuditError(
                "READY_STATE_INVALID", "blocked handoff cannot be ready"
            )
        result["audit_ready"] = True
        result["issues"] = _safe_issues(handoff["issues"], label="handoff")
    except DailyResearchHandoffAuditError as exc:
        result["status"] = "invalid"
        result["audit_ready"] = False
        result["issues"] = [{"code": exc.code, "message": _safe_message(exc)}]
    return _write_report(result, output_dir=output_dir)
