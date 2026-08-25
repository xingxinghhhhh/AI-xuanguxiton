"""Build a read-only freshness admission for a daily research run."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any
from zoneinfo import ZoneInfo

from ..market.calendar import CalendarError, JsonTradingCalendarSource, TradingCalendar
from ..market.replay import sha256_bytes, write_atomic
from .daily_research_run import DAILY_RESEARCH_RUN_VERSION
from .daily_research_run_audit import DAILY_RESEARCH_RUN_AUDIT_VERSION

DAILY_RESEARCH_ADMISSION_VERSION = "daily-research-admission-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
CLOSE_HOUR = 15
CLOSE_MINUTE = 0
_STATUSES = {"ready", "stale", "calendar_unknown", "blocked", "invalid"}
_SHA256_LENGTH = 64
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


class DailyResearchAdmissionError(ValueError):
    """A fail-closed daily research admission error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchAdmissionError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchAdmissionError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return value, raw


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchAdmissionError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchAdmissionError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchAdmissionError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchAdmissionError("PATH_INVALID", f"{label} must be relative")
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or any(part in {"", ".", ".."} for part in path.parts):
        raise DailyResearchAdmissionError("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(path.parts):
        raise DailyResearchAdmissionError("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise DailyResearchAdmissionError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchAdmissionError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchAdmissionError("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _input_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        relative_value = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise DailyResearchAdmissionError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    relative = _relative_path(relative_value, label=label)
    return _safe_file(root / relative, root=root, label=label)


def _validate_sha(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise DailyResearchAdmissionError("HASH_INVALID", f"{label} is invalid")
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _validate_sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchAdmissionError("HASH_MISMATCH", f"{label} self-hash is invalid")


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise DailyResearchAdmissionError("ISSUES_INVALID", f"{label} must be a list")
    for index, issue in enumerate(value):
        if (
            not isinstance(issue, Mapping)
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("message"), str)
        ):
            raise DailyResearchAdmissionError("ISSUES_INVALID", f"{label}[{index}] is invalid")


def _latest_completed_trading_date(calendar: TradingCalendar, evaluation_at: datetime) -> Any:
    local = evaluation_at.astimezone(SHANGHAI)
    evaluation_date = local.date()
    if evaluation_date < calendar.covered_start or evaluation_date > calendar.covered_end:
        return None
    after_close = (local.hour, local.minute, local.second, local.microsecond) >= (
        CLOSE_HOUR,
        CLOSE_MINUTE,
        0,
        0,
    )
    candidates = (
        (day for day in calendar.trading_dates if day <= evaluation_date)
        if after_close
        else (day for day in calendar.trading_dates if day < evaluation_date)
    )
    return max(candidates, default=None)


def _base_report(*, evaluation_at: datetime) -> dict[str, Any]:
    return {
        "admission_version": DAILY_RESEARCH_ADMISSION_VERSION,
        "run_report_path": None,
        "run_audit_report_path": None,
        "calendar_path": None,
        "calendar_report_path": None,
        "run_report_sha256": None,
        "run_audit_report_sha256": None,
        "calendar_sha256": None,
        "calendar_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": evaluation_at.isoformat(),
        "expected_latest_trading_date": None,
        "status": "invalid",
        "freshness_status": "invalid",
        "audit_ready": False,
        "analysis_input_ready": False,
        "admission_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        report["output_sha256"] = sha256_bytes(_json_bytes(report))
        write_atomic(output_dir / "daily_research_admission.json", _json_bytes(report))
        write_atomic(output_dir / "daily_research_admission_report.json", _json_bytes(report))
    except OSError as exc:
        raise DailyResearchAdmissionError(
            "OUTPUT_UNAVAILABLE", "admission output is unavailable"
        ) from exc
    return report


def _load_chain(
    *, run_report_path: Path, run_audit_report_path: Path, root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    run_file = _input_file(run_report_path, root=root, label="run report")
    audit_file = _input_file(run_audit_report_path, root=root, label="run audit report")
    run, _ = _read_json(run_file["path"], label="run report")
    audit, _ = _read_json(audit_file["path"], label="run audit report")
    if audit.get("audit_version") != DAILY_RESEARCH_RUN_AUDIT_VERSION:
        raise DailyResearchAdmissionError("VERSION_MISMATCH", "run audit version is invalid")
    if set(audit) != _AUDIT_FIELDS:
        raise DailyResearchAdmissionError("AUDIT_FIELDS_INVALID", "run audit fields are invalid")
    _validate_self_hash(audit, label="run audit report")
    if audit.get("audit_ready") is not True or audit.get("run_status") != "ready":
        raise DailyResearchAdmissionError("UPSTREAM_NOT_READY", "run audit is not ready")
    if audit.get("decision_ready") is not False:
        raise DailyResearchAdmissionError(
            "DECISION_GATE_INVALID", "run audit decision_ready must be false"
        )
    if audit.get("run_report_sha256") != run_file["sha256"]:
        raise DailyResearchAdmissionError("HASH_MISMATCH", "run audit does not match run report")
    if audit.get("run_report_path") != run_file["relative_path"]:
        raise DailyResearchAdmissionError(
            "CHAIN_MISMATCH", "run audit path does not match run report"
        )
    if run.get("run_version") != DAILY_RESEARCH_RUN_VERSION or run.get("status") != "ready":
        raise DailyResearchAdmissionError("UPSTREAM_NOT_READY", "daily research run is not ready")
    if run.get("decision_ready") is not False or run.get("analysis_input_ready") is not True:
        raise DailyResearchAdmissionError("DECISION_GATE_INVALID", "daily run is not safely ready")
    if run.get("symbol") != audit.get("symbol") or run.get("as_of") != audit.get("as_of"):
        raise DailyResearchAdmissionError("CHAIN_MISMATCH", "run and audit metadata differ")
    _parse_datetime(run.get("as_of"), label="run.as_of")
    return run, audit, {"run": run_file, "audit": audit_file}


def build_daily_research_admission(
    *,
    run_report_path: Path,
    run_audit_report_path: Path,
    calendar_path: Path,
    calendar_report_path: Path,
    evaluation_at: datetime,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic freshness admission without external I/O."""

    report = _base_report(evaluation_at=evaluation_at)
    try:
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise DailyResearchAdmissionError("TIME_INVALID", "evaluation_at must include timezone")
        root = artifact_root.resolve()
        if not root.is_dir():
            raise DailyResearchAdmissionError(
                "ARTIFACT_ROOT_INVALID", "artifact root is unavailable"
            )
        try:
            output_dir.resolve().relative_to(root)
        except ValueError as exc:
            raise DailyResearchAdmissionError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir must be inside artifact-root"
            ) from exc
        run, audit, chain_files = _load_chain(
            run_report_path=run_report_path,
            run_audit_report_path=run_audit_report_path,
            root=root,
        )
        calendar_file = _input_file(calendar_path, root=root, label="calendar")
        calendar_report_file = _input_file(calendar_report_path, root=root, label="calendar report")
        calendar_report, _ = _read_json(calendar_report_file["path"], label="calendar report")
        try:
            calendar = JsonTradingCalendarSource(calendar_file["path"]).load_calendar()
        except (CalendarError, OSError) as exc:
            raise DailyResearchAdmissionError("CALENDAR_INVALID", "calendar is invalid") from exc
        if (
            calendar_report.get("status") != "complete"
            or calendar_report.get("decision_ready") is not True
        ):
            raise DailyResearchAdmissionError(
                "CALENDAR_NOT_READY", "calendar report is not complete"
            )
        if calendar_report.get("calendar_sha256") != calendar_file["sha256"]:
            raise DailyResearchAdmissionError(
                "HASH_MISMATCH", "calendar report does not match calendar"
            )
        if calendar_report.get("calendar_version") != calendar.calendar_version:
            raise DailyResearchAdmissionError("CHAIN_MISMATCH", "calendar version is inconsistent")

        report.update(
            run_report_path=chain_files["run"]["relative_path"],
            run_audit_report_path=chain_files["audit"]["relative_path"],
            calendar_path=calendar_file["relative_path"],
            calendar_report_path=calendar_report_file["relative_path"],
            run_report_sha256=chain_files["run"]["sha256"],
            run_audit_report_sha256=chain_files["audit"]["sha256"],
            calendar_sha256=calendar_file["sha256"],
            calendar_report_sha256=calendar_report_file["sha256"],
            symbol=run["symbol"],
            as_of=run["as_of"],
            audit_ready=True,
            analysis_input_ready=True,
        )
        as_of = _parse_datetime(run["as_of"], label="run.as_of")
        if as_of > evaluation_at:
            raise DailyResearchAdmissionError(
                "RUN_AFTER_EVALUATION", "run as_of is after evaluation_at"
            )
        expected_latest = _latest_completed_trading_date(calendar, evaluation_at)
        if expected_latest is None:
            report.update(
                status="calendar_unknown",
                freshness_status="calendar_unknown",
                issues=[
                    {
                        "code": "CALENDAR_UNKNOWN",
                        "message": "calendar does not cover evaluation date",
                    }
                ],
            )
        else:
            report["expected_latest_trading_date"] = expected_latest.isoformat()
            as_of_date = as_of.astimezone(SHANGHAI).date()
            if as_of_date not in calendar.trading_dates:
                raise DailyResearchAdmissionError(
                    "RUN_DATE_NON_TRADING", "run as_of date is not a trading date"
                )
            fresh = as_of_date == expected_latest
            report.update(
                status="ready" if fresh else "stale",
                freshness_status="fresh" if fresh else "stale",
                admission_ready=fresh,
                issues=[]
                if fresh
                else [
                    {
                        "code": "RESEARCH_STALE",
                        "message": "run is before the latest completed trading date",
                    }
                ],
            )
    except DailyResearchAdmissionError as exc:
        status = "blocked" if exc.code == "UPSTREAM_NOT_READY" else "invalid"
        report.update(
            status=status,
            freshness_status=status,
            issues=[{"code": exc.code, "message": str(exc)}],
        )
    return _write_report(report, output_dir=output_dir)
