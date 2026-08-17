"""Independently audit one bounded daily research run and its artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..evidence.contracts import (
    BUNDLE_VERSION_V2,
    MARKET_CONTEXT_SUMMARY_VERSION,
    RELATIVE_STRENGTH_VERSION,
)
from ..market.replay import sha256_bytes, write_atomic
from .daily_research_run import DAILY_RESEARCH_RUN_VERSION

DAILY_RESEARCH_RUN_AUDIT_VERSION = "daily-research-run-audit-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_STATUSES = {"ready", "blocked"}
_STAGE_STATUSES = {"ready", "failed", "skipped"}
_STAGE_FIELDS = {"name", "status", "skipped", "error_code", "artifacts", "issues"}
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
_STAGES = (
    (
        "daily_bars",
        (
            "market/request.json",
            "market/raw_response.json",
            "market/normalized_daily.jsonl",
            "market/capture_report.json",
        ),
    ),
    (
        "market_context",
        (
            "market-context/request.json",
            "market-context/raw_response.json",
            "market-context/market_context_snapshot.json",
            "market-context/market_context_report.json",
        ),
    ),
    ("data_quality", ("quality/health_report.json", "quality/coverage_report.json")),
    (
        "technical_features",
        ("technical/technical_features.jsonl", "technical/technical_report.json"),
    ),
    ("price_plan", ("price-plan/technical_price_plan.json", "price-plan/price_plan_report.json")),
    (
        "profitability",
        (
            "fundamentals/profitability/request.json",
            "fundamentals/profitability/raw_response.json",
            "fundamentals/profitability/profitability_snapshot.json",
            "fundamentals/profitability/profitability_report.json",
        ),
    ),
    (
        "growth",
        (
            "fundamentals/growth/request.json",
            "fundamentals/growth/raw_response.json",
            "fundamentals/growth/growth_snapshot.json",
            "fundamentals/growth/growth_report.json",
        ),
    ),
    (
        "announcements",
        (
            "announcements/request.json",
            "announcements/raw_response.json",
            "announcements/announcements_snapshot.json",
            "announcements/announcements_report.json",
        ),
    ),
    (
        "analysis_input_v2",
        ("analysis-input/analysis_input_bundle.json", "analysis-input/analysis_input_report.json"),
    ),
)
_EXPECTED_STAGE_NAMES = tuple(name for name, _ in _STAGES)
_FALSE_DECISION_ARTIFACTS = {
    "market/capture_report.json",
    "market-context/market_context_snapshot.json",
    "market-context/market_context_report.json",
    "price-plan/technical_price_plan.json",
    "price-plan/price_plan_report.json",
    "fundamentals/profitability/profitability_snapshot.json",
    "fundamentals/profitability/profitability_report.json",
    "fundamentals/growth/growth_snapshot.json",
    "fundamentals/growth/growth_report.json",
    "announcements/announcements_snapshot.json",
    "announcements/announcements_report.json",
    "analysis-input/analysis_input_bundle.json",
    "analysis-input/analysis_input_report.json",
}


class DailyResearchRunAuditError(ValueError):
    """A fail-closed daily research run audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchRunAuditError("INPUT_JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchRunAuditError("INPUT_ROOT_INVALID", f"{label} must be an object")
    return value


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchRunAuditError("HASH_INVALID", f"{label} must be a SHA-256 string")
    return value


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchRunAuditError("TIME_INVALID", f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchRunAuditError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchRunAuditError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _parse_date(value: Any, *, label: str) -> date:
    if not isinstance(value, str):
        raise DailyResearchRunAuditError("DATE_INVALID", f"{label} is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DailyResearchRunAuditError("DATE_INVALID", f"{label} is invalid") from exc


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchRunAuditError("PATH_INVALID", f"{label} must be relative")
    windows_path = PureWindowsPath(value)
    path = Path(value)
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise DailyResearchRunAuditError("PATH_INVALID", f"{label} must be relative")
    if any(part in {"", ".", ".."} for part in windows_path.parts):
        raise DailyResearchRunAuditError("PATH_INVALID", f"{label} is not normalized")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(windows_path.parts):
        raise DailyResearchRunAuditError("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise DailyResearchRunAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchRunAuditError("INPUT_UNAVAILABLE", f"{label} is not a file")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchRunAuditError("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _resolve_artifact_file(relative: str, *, root: Path, label: str) -> dict[str, Any]:
    """Resolve output-relative paths and Node56 input-root-prefixed paths."""

    parts = PureWindowsPath(relative).parts
    candidate_relative = "/".join(parts[1:]) if parts and parts[0] == root.name else relative
    return _safe_file(root / Path(candidate_relative), root=root, label=label)


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise DailyResearchRunAuditError("ISSUES_INVALID", f"{label} must be a list")
    for index, issue in enumerate(value):
        if (
            not isinstance(issue, Mapping)
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("message"), str)
        ):
            raise DailyResearchRunAuditError("ISSUES_INVALID", f"{label}[{index}] is invalid")


def _validate_decision_false(payload: Mapping[str, Any], *, label: str) -> None:
    if payload.get("decision_ready") is not False:
        raise DailyResearchRunAuditError(
            "DECISION_GATE_INVALID", f"{label}.decision_ready must be false"
        )


def _load_artifacts(
    report: Mapping[str, Any], *, root: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    stages = report.get("stages")
    if not isinstance(stages, list) or len(stages) != len(_STAGES):
        raise DailyResearchRunAuditError("STAGES_INVALID", "run report must contain nine stages")
    by_stage: dict[str, dict[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}
    for index, (expected_name, expected_paths) in enumerate(_STAGES):
        stage = stages[index]
        if not isinstance(stage, Mapping) or set(stage) != _STAGE_FIELDS:
            raise DailyResearchRunAuditError(
                "STAGE_FIELDS_INVALID", f"stage {index} fields are invalid"
            )
        if stage.get("name") != expected_name or expected_name in by_stage:
            raise DailyResearchRunAuditError("STAGE_ORDER_INVALID", "stage order is invalid")
        status = stage.get("status")
        if status not in _STAGE_STATUSES or stage.get("skipped") is not (status == "skipped"):
            raise DailyResearchRunAuditError(
                "STAGE_STATUS_INVALID", f"stage {expected_name} status is invalid"
            )
        _validate_issues(stage.get("issues"), label=f"stage {expected_name}.issues")
        if status == "ready" and stage.get("error_code") is not None:
            raise DailyResearchRunAuditError(
                "STAGE_STATUS_INVALID", f"ready stage {expected_name} has an error"
            )
        if status != "ready" and not isinstance(stage.get("error_code"), str):
            raise DailyResearchRunAuditError(
                "STAGE_STATUS_INVALID", f"stage {expected_name} error code is invalid"
            )
        artifacts = stage.get("artifacts")
        if not isinstance(artifacts, list):
            raise DailyResearchRunAuditError(
                "ARTIFACT_INVALID", f"stage {expected_name} artifacts are invalid"
            )
        if status == "skipped" and artifacts:
            raise DailyResearchRunAuditError(
                "ARTIFACT_INVALID", f"skipped stage {expected_name} has artifacts"
            )
        known_paths = set(expected_paths)
        seen_paths: set[str] = set()
        resolved_paths: set[str] = set()
        for artifact_index, artifact in enumerate(artifacts):
            if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
                raise DailyResearchRunAuditError(
                    "ARTIFACT_INVALID", f"stage {expected_name} artifact is invalid"
                )
            relative = _relative_path(
                artifact.get("path"), label=f"stage {expected_name} artifact path"
            )
            declared_parts = PureWindowsPath(relative).parts
            canonical_declared = (
                "/".join(declared_parts[1:])
                if declared_parts and declared_parts[0] == root.name
                else relative
            )
            if canonical_declared not in known_paths or canonical_declared in seen_paths:
                raise DailyResearchRunAuditError(
                    "ARTIFACT_INVALID", f"stage {expected_name} artifact path is invalid"
                )
            seen_paths.add(canonical_declared)
            expected_sha = _validate_sha(
                artifact.get("sha256"), label=f"stage {expected_name} artifact SHA"
            )
            file_info = _resolve_artifact_file(
                relative, root=root, label=f"stage {expected_name} artifact"
            )
            canonical_relative = file_info["relative_path"]
            if canonical_relative not in known_paths or file_info["sha256"] != expected_sha:
                raise DailyResearchRunAuditError(
                    "HASH_MISMATCH", f"stage {expected_name} artifact hash is invalid"
                )
            if canonical_relative in resolved_paths or canonical_relative in files:
                raise DailyResearchRunAuditError("ARTIFACT_INVALID", "artifacts must be distinct")
            resolved_paths.add(canonical_relative)
            files[canonical_relative] = file_info
        if status == "ready" and resolved_paths != known_paths:
            raise DailyResearchRunAuditError(
                "ARTIFACT_INCOMPLETE", f"ready stage {expected_name} artifacts are incomplete"
            )
        by_stage[expected_name] = dict(stage)
    return by_stage, files


def _validate_stage_flow(report: Mapping[str, Any]) -> str | None:
    stages = report["stages"]
    statuses = [stage["status"] for stage in stages]
    if report["status"] == "ready":
        if statuses != ["ready"] * len(_STAGES):
            raise DailyResearchRunAuditError(
                "STAGE_FLOW_INVALID", "ready run stages are not all ready"
            )
        return None
    failed = [index for index, status in enumerate(statuses) if status == "failed"]
    if (
        len(failed) != 1
        or any(status != "ready" for status in statuses[: failed[0]])
        or any(status != "skipped" for status in statuses[failed[0] + 1 :])
    ):
        raise DailyResearchRunAuditError("STAGE_FLOW_INVALID", "blocked run stage flow is invalid")
    return _EXPECTED_STAGE_NAMES[failed[0]]


def _validate_run_report(report: Mapping[str, Any]) -> None:
    if set(report) != _RUN_FIELDS:
        raise DailyResearchRunAuditError("RUN_FIELDS_INVALID", "run report fields are invalid")
    if report.get("run_version") != DAILY_RESEARCH_RUN_VERSION:
        raise DailyResearchRunAuditError("VERSION_MISMATCH", "run report version is invalid")
    if report.get("source_mode") != "public-read-only":
        raise DailyResearchRunAuditError("SOURCE_MODE_INVALID", "run source mode is invalid")
    if not isinstance(report.get("symbol"), str) or not report["symbol"].strip():
        raise DailyResearchRunAuditError("FIELD_INVALID", "run symbol is invalid")
    start = _parse_date(report.get("start_date"), label="run.start_date")
    end = _parse_date(report.get("end_date"), label="run.end_date")
    if start > end:
        raise DailyResearchRunAuditError("DATE_INVALID", "run date range is reversed")
    as_of = _parse_datetime(report.get("as_of"), label="run.as_of")
    received_at = _parse_datetime(report.get("received_at"), label="run.received_at")
    if received_at != as_of:
        raise DailyResearchRunAuditError("TIME_MISMATCH", "run received_at differs from as_of")
    if report.get("status") not in _RUN_STATUSES:
        raise DailyResearchRunAuditError("STATUS_INVALID", "run status is invalid")
    if not isinstance(report.get("analysis_input_ready"), bool):
        raise DailyResearchRunAuditError("FIELD_INVALID", "analysis_input_ready must be boolean")
    if report.get("decision_ready") is not False:
        raise DailyResearchRunAuditError(
            "DECISION_GATE_INVALID", "run decision_ready must be false"
        )
    _validate_issues(report.get("issues"), label="run.issues")


def _validate_ready_chain(
    report: Mapping[str, Any], files: Mapping[str, Mapping[str, Any]], *, root: Path
) -> None:
    if report.get("analysis_input_ready") is not True:
        raise DailyResearchRunAuditError(
            "READY_STATE_INVALID", "ready run analysis_input_ready must be true"
        )
    declared_path = _relative_path(report.get("analysis_input_path"), label="analysis_input_path")
    analysis_file = _resolve_artifact_file(
        declared_path, root=root, label="analysis input artifact"
    )
    path = analysis_file["relative_path"]
    if path != "analysis-input/analysis_input_bundle.json":
        raise DailyResearchRunAuditError("CHAIN_MISMATCH", "analysis input path is invalid")
    if path not in files:
        raise DailyResearchRunAuditError("CHAIN_MISMATCH", "analysis input artifact is missing")
    if (
        _validate_sha(report.get("analysis_input_sha256"), label="analysis_input_sha256")
        != files[path]["sha256"]
    ):
        raise DailyResearchRunAuditError("HASH_MISMATCH", "analysis input SHA is invalid")
    if report.get("market_context_summary_version") != MARKET_CONTEXT_SUMMARY_VERSION:
        raise DailyResearchRunAuditError(
            "VERSION_MISMATCH", "market context summary version is invalid"
        )
    if report.get("relative_strength_version") != RELATIVE_STRENGTH_VERSION:
        raise DailyResearchRunAuditError("VERSION_MISMATCH", "relative strength version is invalid")
    bundle = _read_json(files[path]["raw"], label="analysis input bundle")
    if bundle.get("bundle_version") != BUNDLE_VERSION_V2:
        raise DailyResearchRunAuditError(
            "VERSION_MISMATCH", "analysis input bundle version is invalid"
        )
    _validate_decision_false(bundle, label="analysis input bundle")
    try:
        context = bundle["summaries"]["market"]["market_context"]
        relative = bundle["summaries"]["technical"]["relative_strength"]
    except (KeyError, TypeError) as exc:
        raise DailyResearchRunAuditError(
            "CHAIN_MISMATCH", "analysis input summaries are incomplete"
        ) from exc
    if context.get("market_context_summary_version") != MARKET_CONTEXT_SUMMARY_VERSION:
        raise DailyResearchRunAuditError(
            "VERSION_MISMATCH", "bundle market context version is invalid"
        )
    if relative.get("version") != RELATIVE_STRENGTH_VERSION:
        raise DailyResearchRunAuditError(
            "VERSION_MISMATCH", "bundle relative strength version is invalid"
        )
    analysis_report = _read_json(
        files["analysis-input/analysis_input_report.json"]["raw"], label="analysis input report"
    )
    if analysis_report.get("analysis_input_ready") is not True:
        raise DailyResearchRunAuditError(
            "READY_STATE_INVALID", "analysis input report is not ready"
        )
    _validate_decision_false(analysis_report, label="analysis input report")


def _validate_decision_artifacts(files: Mapping[str, Mapping[str, Any]]) -> None:
    for relative in sorted(_FALSE_DECISION_ARTIFACTS & set(files)):
        payload = _read_json(files[relative]["raw"], label=relative)
        _validate_decision_false(payload, label=relative)


def _base_report(*, run_path: str | None, run_sha: str | None) -> dict[str, Any]:
    return {
        "audit_version": DAILY_RESEARCH_RUN_AUDIT_VERSION,
        "run_report_path": run_path,
        "run_report_sha256": run_sha,
        "status": "invalid",
        "audit_ready": False,
        "run_status": None,
        "symbol": None,
        "as_of": None,
        "received_at": None,
        "stage_count": 0,
        "failed_stage": None,
        "analysis_input_path": None,
        "analysis_input_sha256": None,
        "analysis_input_ready": False,
        "market_context_summary_version": None,
        "relative_strength_version": None,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _finalize(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        report["output_sha256"] = sha256_bytes(_json_bytes(report))
        write_atomic(output_dir / "daily_research_run_audit_report.json", _json_bytes(report))
    except OSError as exc:
        raise DailyResearchRunAuditError(
            "OUTPUT_UNAVAILABLE", "audit output is unavailable"
        ) from exc
    return report


def audit_daily_research_run(
    *, run_report_path: Path, artifact_root: Path, output_dir: Path
) -> dict[str, Any]:
    """Audit a previously produced run without reading external services or API keys."""

    result = _base_report(run_path=None, run_sha=None)
    try:
        root = artifact_root.resolve()
        if not root.is_dir():
            raise DailyResearchRunAuditError("INPUT_UNAVAILABLE", "artifact root is unavailable")
        run_file = _safe_file(run_report_path, root=root, label="run report")
        result["run_report_path"] = run_file["relative_path"]
        result["run_report_sha256"] = run_file["sha256"]
        report = _read_json(run_file["raw"], label="run report")
        _validate_run_report(report)
        result.update(
            run_status=report["status"],
            symbol=report["symbol"],
            as_of=report["as_of"],
            received_at=report["received_at"],
            stage_count=len(report["stages"]),
            analysis_input_path=report["analysis_input_path"],
            analysis_input_sha256=report["analysis_input_sha256"],
            analysis_input_ready=report["analysis_input_ready"],
            market_context_summary_version=report["market_context_summary_version"],
            relative_strength_version=report["relative_strength_version"],
        )
        by_stage, files = _load_artifacts(report, root=root)
        failed_stage = _validate_stage_flow(report)
        result["failed_stage"] = failed_stage
        _validate_decision_artifacts(files)
        if report["status"] == "ready":
            _validate_ready_chain(report, files, root=root)
        else:
            if report["analysis_input_ready"] or report["analysis_input_path"] is not None:
                raise DailyResearchRunAuditError(
                    "READY_STATE_INVALID", "blocked run has analysis input"
                )
            if report["analysis_input_sha256"] is not None:
                raise DailyResearchRunAuditError(
                    "FIELD_INVALID", "blocked run has analysis input SHA"
                )
            if (
                report["market_context_summary_version"] is not None
                or report["relative_strength_version"] is not None
            ):
                raise DailyResearchRunAuditError(
                    "FIELD_INVALID", "blocked run has analysis summary versions"
                )
            result["status"] = "blocked"
            result["issues"] = [{"code": "UPSTREAM_NOT_READY", "message": "run status is blocked"}]
        if report["status"] == "ready":
            result["status"] = "ready"
            result["audit_ready"] = True
    except DailyResearchRunAuditError as exc:
        result["issues"] = [{"code": exc.code, "message": str(exc)}]
    return _finalize(result, output_dir=output_dir)
