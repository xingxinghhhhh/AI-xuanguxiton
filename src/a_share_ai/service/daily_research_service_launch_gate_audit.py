"""Independently audit a daily research service launch gate."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..analysis.market_aware_session_history_final_receipt import (
    MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
)
from ..market.replay import sha256_bytes, write_atomic
from ..runtime.daily_research_admission import DAILY_RESEARCH_ADMISSION_VERSION
from ..runtime.daily_research_handoff import DAILY_RESEARCH_HANDOFF_VERSION
from ..runtime.daily_research_handoff_audit import DAILY_RESEARCH_HANDOFF_AUDIT_VERSION
from .read_only_receipt_probe import READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION
from .read_only_receipt_server import READ_ONLY_RECEIPT_SERVICE_VERSION

DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION = "daily-research-service-launch-gate-audit-v1"
_LAUNCH_VERSION = "read-only-receipt-service-launch-v1"
_GATE_VERSION = "daily-research-service-launch-gate-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HANDOFF_STATUSES = {"ready", "blocked"}
_ADMISSION_STATUSES = {"ready", "stale", "calendar_unknown", "blocked"}
_GATE_STATUSES = {"ready", "stale", "blocked"}
_RECEIPT_STATUSES = {"ready", "stale", "blocked"}
_ALLOWED_HOSTS = {"127.0.0.1", "::1"}

_GATE_FIELDS = {
    "gate_version",
    "launch_manifest_path",
    "launch_manifest_sha256",
    "handoff_path",
    "handoff_sha256",
    "handoff_report_path",
    "handoff_report_sha256",
    "handoff_audit_report_path",
    "handoff_audit_report_sha256",
    "daily_admission_path",
    "daily_admission_sha256",
    "daily_admission_report_path",
    "daily_admission_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "status",
    "receipt_ready",
    "daily_admission_status",
    "daily_admission_ready",
    "audit_ready",
    "handoff_ready",
    "gate_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}
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
_HANDOFF_AUDIT_FIELDS = {
    "audit_version",
    "handoff_path",
    "handoff_sha256",
    "handoff_report_path",
    "handoff_report_sha256",
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
    "status",
    "handoff_ready",
    "audit_ready",
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
_LAUNCH_FIELDS = {
    "launch_version",
    "service_version",
    "probe_version",
    "receipt_path",
    "receipt_report_path",
    "receipt_sha256",
    "receipt_report_sha256",
    "host",
    "port",
    "decision_ready",
}
_AUDIT_OUTPUT_FIELDS = {
    "audit_version",
    "gate_path",
    "gate_sha256",
    "gate_report_path",
    "gate_report_sha256",
    "launch_manifest_path",
    "launch_manifest_sha256",
    "handoff_path",
    "handoff_sha256",
    "handoff_report_path",
    "handoff_report_sha256",
    "handoff_audit_report_path",
    "handoff_audit_report_sha256",
    "daily_admission_path",
    "daily_admission_sha256",
    "daily_admission_report_path",
    "daily_admission_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "status",
    "gate_ready",
    "audit_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceLaunchGateAuditError(ValueError):
    """A sanitized independent launch-gate audit failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _safe_message(value: Any) -> str:
    message = str(value)
    message = re.sub(r"(?i)\b[A-Z]:[\\/][^\s\"']*", "<redacted-path>", message)
    message = re.sub(r"(?<![A-Za-z0-9])/(?:[^\s\"']+/?)+", "<redacted-path>", message)
    return message


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceLaunchGateAuditError(
            "INPUT_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceLaunchGateAuditError(
            "INPUT_INVALID", f"{label} must be an object"
        )
    return value


def _validate_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceLaunchGateAuditError("HASH_INVALID", f"{label} is invalid")
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _validate_sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceLaunchGateAuditError(
            "HASH_MISMATCH", f"{label} self-hash differs"
        )


def _parse_time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceLaunchGateAuditError("TIME_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceLaunchGateAuditError(
            "TIME_INVALID", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceLaunchGateAuditError("TIME_INVALID", f"{label} needs timezone")
    return parsed


def _validate_issues(value: Any, *, label: str) -> None:
    if not isinstance(value, list):
        raise DailyResearchServiceLaunchGateAuditError(
            "ISSUES_INVALID", f"{label} issues are invalid"
        )
    for index, issue in enumerate(value):
        if (
            not isinstance(issue, Mapping)
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("message"), str)
        ):
            raise DailyResearchServiceLaunchGateAuditError(
                "ISSUES_INVALID", f"{label} issues[{index}] is invalid"
            )


def _safe_relative(value: Any, *, root: Path, label: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceLaunchGateAuditError("PATH_INVALID", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceLaunchGateAuditError("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise DailyResearchServiceLaunchGateAuditError("PATH_INVALID", f"{label} is not normalized")
    try:
        candidate = (root / normalized).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceLaunchGateAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceLaunchGateAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        )
    if candidate.relative_to(root.resolve()).as_posix() != normalized:
        raise DailyResearchServiceLaunchGateAuditError("PATH_INVALID", f"{label} is not normalized")
    return candidate, normalized


def _safe_file(path: Path, *, root: Path, label: str) -> tuple[Path, str, str]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceLaunchGateAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceLaunchGateAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceLaunchGateAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return candidate, relative, sha256_bytes(raw)


def _resolve_declared(
    value: Any, expected_sha: Any, *, root: Path, label: str
) -> tuple[Path, str, str]:
    candidate, relative = _safe_relative(value, root=root, label=label)
    actual = sha256_bytes(candidate.read_bytes())
    if _validate_sha(expected_sha, label=f"{label} SHA") != actual:
        raise DailyResearchServiceLaunchGateAuditError("HASH_MISMATCH", f"{label} hash differs")
    return candidate, relative, actual


def _validate_pair(
    first: Mapping[str, Any], second: Mapping[str, Any], *, fields: set[str], label: str
) -> None:
    for name, payload in ((label, first), (f"{label} report", second)):
        if set(payload) != fields:
            raise DailyResearchServiceLaunchGateAuditError(
                "SCHEMA_INVALID", f"{name} fields are invalid"
            )
        _validate_self_hash(payload, label=name)
    if dict(first) != dict(second):
        raise DailyResearchServiceLaunchGateAuditError("FIELD_MISMATCH", f"{label} pair differs")


def _validate_receipt_pair(
    receipt: Mapping[str, Any], report: Mapping[str, Any], *, receipt_sha: str, root: Path
) -> bool:
    _validate_self_hash(receipt, label="receipt")
    _validate_self_hash(report, label="receipt report")
    if report.get("receipt_sha256") != receipt_sha:
        raise DailyResearchServiceLaunchGateAuditError(
            "HASH_MISMATCH", "receipt report hash differs"
        )
    for payload, label in ((receipt, "receipt"), (report, "receipt report")):
        if payload.get("receipt_version") != MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION:
            raise DailyResearchServiceLaunchGateAuditError(
                "VERSION_MISMATCH", f"{label} version is invalid"
            )
    for field in (
        "status",
        "symbol",
        "package_count",
        "first_as_of",
        "last_as_of",
        "admission_ready",
        "render_ready",
        "audit_ready",
        "receipt_ready",
        "decision_ready",
        "issues",
    ):
        if receipt.get(field) != report.get(field):
            raise DailyResearchServiceLaunchGateAuditError(
                "FIELD_MISMATCH", f"receipt/report {field} differs"
            )
    if receipt.get("status") not in _RECEIPT_STATUSES:
        raise DailyResearchServiceLaunchGateAuditError("STATE_INVALID", "receipt status is invalid")
    if not isinstance(receipt.get("symbol"), str) or not receipt["symbol"].strip():
        raise DailyResearchServiceLaunchGateAuditError("FIELD_INVALID", "receipt symbol is invalid")
    if isinstance(receipt.get("package_count"), bool) or not isinstance(
        receipt.get("package_count"), int
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "FIELD_INVALID", "receipt package_count is invalid"
        )
    for field in ("admission_ready", "render_ready", "audit_ready", "receipt_ready"):
        if not isinstance(receipt.get(field), bool):
            raise DailyResearchServiceLaunchGateAuditError(
                "FIELD_INVALID", f"receipt {field} is invalid"
            )
    if receipt.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateAuditError(
            "DECISION_GATE_INVALID", "receipt decision_ready must be false"
        )
    if receipt["receipt_ready"] is not (receipt["status"] == "ready"):
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "receipt readiness differs from status"
        )
    _validate_issues(receipt.get("issues"), label="receipt")
    inputs = report.get("inputs")
    if not isinstance(inputs, Mapping):
        raise DailyResearchServiceLaunchGateAuditError(
            "SCHEMA_INVALID", "receipt report inputs are invalid"
        )
    for role, item in inputs.items():
        if not isinstance(role, str) or not isinstance(item, Mapping):
            raise DailyResearchServiceLaunchGateAuditError(
                "SCHEMA_INVALID", "receipt report inputs are invalid"
            )
        value = item.get("path")
        if not isinstance(value, str) or Path(value).is_absolute():
            raise DailyResearchServiceLaunchGateAuditError(
                "PATH_INVALID", "receipt report input path is invalid"
            )
        try:
            resolved = (root / value).resolve()
            resolved.relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise DailyResearchServiceLaunchGateAuditError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", "receipt report input escapes artifact root"
            ) from exc
    return receipt["receipt_ready"]


def _validate_launch_manifest(
    launch_path: Path, *, root: Path
) -> tuple[dict[str, Any], dict[str, str]]:
    launch_file, launch_relative, launch_sha = _safe_file(
        launch_path, root=root, label="launch manifest"
    )
    launch = _read_json(launch_file, label="launch manifest")
    if set(launch) != _LAUNCH_FIELDS:
        raise DailyResearchServiceLaunchGateAuditError(
            "SCHEMA_INVALID", "launch manifest fields are invalid"
        )
    if launch.get("launch_version") != _LAUNCH_VERSION:
        raise DailyResearchServiceLaunchGateAuditError(
            "VERSION_MISMATCH", "launch version is invalid"
        )
    if launch.get("service_version") != READ_ONLY_RECEIPT_SERVICE_VERSION:
        raise DailyResearchServiceLaunchGateAuditError(
            "VERSION_MISMATCH", "service version is invalid"
        )
    if launch.get("probe_version") != READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION:
        raise DailyResearchServiceLaunchGateAuditError(
            "VERSION_MISMATCH", "probe version is invalid"
        )
    if launch.get("host") not in _ALLOWED_HOSTS:
        raise DailyResearchServiceLaunchGateAuditError("FIELD_INVALID", "launch host is invalid")
    if (
        isinstance(launch.get("port"), bool)
        or not isinstance(launch.get("port"), int)
        or not 1 <= launch["port"] <= 65535
    ):
        raise DailyResearchServiceLaunchGateAuditError("FIELD_INVALID", "launch port is invalid")
    if launch.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateAuditError(
            "DECISION_GATE_INVALID", "launch decision_ready must be false"
        )
    receipt_path, receipt_relative, receipt_sha = _resolve_declared(
        launch.get("receipt_path"), launch.get("receipt_sha256"), root=root, label="receipt"
    )
    report_path, report_relative, report_sha = _resolve_declared(
        launch.get("receipt_report_path"),
        launch.get("receipt_report_sha256"),
        root=root,
        label="receipt report",
    )
    if receipt_path == report_path:
        raise DailyResearchServiceLaunchGateAuditError(
            "PATH_INVALID", "receipt inputs must be distinct"
        )
    receipt = _read_json(receipt_path, label="receipt")
    receipt_report = _read_json(report_path, label="receipt report")
    receipt_ready = _validate_receipt_pair(
        receipt, receipt_report, receipt_sha=receipt_sha, root=root
    )
    return {
        "receipt_ready": receipt_ready,
        "launch_status": receipt["status"],
    }, {
        "launch_manifest_path": launch_relative,
        "launch_manifest_sha256": launch_sha,
        "receipt_path": receipt_relative,
        "receipt_sha256": receipt_sha,
        "receipt_report_path": report_relative,
        "receipt_report_sha256": report_sha,
    }


def _validate_admission_pair(
    admission_path: Path, admission_report_path: Path, *, root: Path
) -> tuple[dict[str, Any], dict[str, str]]:
    admission_file, admission_relative, admission_sha = _safe_file(
        admission_path, root=root, label="daily admission"
    )
    report_file, report_relative, report_sha = _safe_file(
        admission_report_path, root=root, label="daily admission report"
    )
    if admission_file == report_file:
        raise DailyResearchServiceLaunchGateAuditError(
            "PATH_INVALID", "daily admission inputs must be distinct"
        )
    admission = _read_json(admission_file, label="daily admission")
    report = _read_json(report_file, label="daily admission report")
    _validate_pair(admission, report, fields=_ADMISSION_FIELDS, label="daily admission")
    if admission.get("admission_version") != DAILY_RESEARCH_ADMISSION_VERSION:
        raise DailyResearchServiceLaunchGateAuditError(
            "VERSION_MISMATCH", "admission version is invalid"
        )
    if admission.get("status") not in _ADMISSION_STATUSES:
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "admission status is invalid"
        )
    expected_freshness = "fresh" if admission["status"] == "ready" else admission["status"]
    if admission.get("freshness_status") != expected_freshness:
        raise DailyResearchServiceLaunchGateAuditError(
            "FIELD_MISMATCH", "admission freshness differs"
        )
    for field in ("audit_ready", "analysis_input_ready", "admission_ready"):
        if not isinstance(admission.get(field), bool):
            raise DailyResearchServiceLaunchGateAuditError(
                "FIELD_INVALID", f"admission {field} is invalid"
            )
    if admission["admission_ready"] is not (admission["status"] == "ready"):
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "admission readiness differs"
        )
    if admission["status"] == "ready" and not (
        admission["audit_ready"] and admission["analysis_input_ready"]
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "ready admission gates are invalid"
        )
    if admission.get("symbol") is not None and (
        not isinstance(admission["symbol"], str) or not admission["symbol"].strip()
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "FIELD_INVALID", "admission symbol is invalid"
        )
    if admission.get("as_of") is not None:
        _parse_time(admission["as_of"], label="admission.as_of")
    _parse_time(admission.get("evaluation_at"), label="admission.evaluation_at")
    if admission.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateAuditError(
            "DECISION_GATE_INVALID", "admission decision_ready must be false"
        )
    _validate_issues(admission.get("issues"), label="admission")
    if admission["status"] == "ready" and not all(
        admission.get(field) is not None
        for field in (
            "run_report_path",
            "run_audit_report_path",
            "calendar_path",
            "calendar_report_path",
            "run_report_sha256",
            "run_audit_report_sha256",
            "calendar_sha256",
            "calendar_report_sha256",
        )
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "ready admission references are incomplete"
        )
    for path_field, sha_field in (
        ("run_report_path", "run_report_sha256"),
        ("run_audit_report_path", "run_audit_report_sha256"),
        ("calendar_path", "calendar_sha256"),
        ("calendar_report_path", "calendar_report_sha256"),
    ):
        if admission.get(path_field) is None and admission.get(sha_field) is None:
            continue
        _resolve_declared(
            admission.get(path_field),
            admission.get(sha_field),
            root=root,
            label=f"admission {path_field}",
        )
    return admission, {
        "daily_admission_path": admission_relative,
        "daily_admission_sha256": admission_sha,
        "daily_admission_report_path": report_relative,
        "daily_admission_report_sha256": report_sha,
    }


def _validate_handoff_pair(
    handoff_path: Path, handoff_report_path: Path, *, root: Path
) -> tuple[dict[str, Any], dict[str, str]]:
    handoff_file, handoff_relative, handoff_sha = _safe_file(
        handoff_path, root=root, label="handoff"
    )
    report_file, report_relative, report_sha = _safe_file(
        handoff_report_path, root=root, label="handoff report"
    )
    if handoff_file == report_file:
        raise DailyResearchServiceLaunchGateAuditError(
            "PATH_INVALID", "handoff inputs must be distinct"
        )
    handoff = _read_json(handoff_file, label="handoff")
    report = _read_json(report_file, label="handoff report")
    _validate_pair(handoff, report, fields=_HANDOFF_FIELDS, label="handoff")
    if handoff.get("handoff_version") != DAILY_RESEARCH_HANDOFF_VERSION:
        raise DailyResearchServiceLaunchGateAuditError(
            "VERSION_MISMATCH", "handoff version is invalid"
        )
    if handoff.get("status") not in _HANDOFF_STATUSES:
        raise DailyResearchServiceLaunchGateAuditError("STATE_INVALID", "handoff status is invalid")
    if handoff.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateAuditError(
            "DECISION_GATE_INVALID", "handoff decision_ready must be false"
        )
    if not isinstance(handoff.get("symbol"), str) or not handoff["symbol"].strip():
        raise DailyResearchServiceLaunchGateAuditError("FIELD_INVALID", "handoff symbol is invalid")
    _parse_time(handoff.get("as_of"), label="handoff.as_of")
    _parse_time(handoff.get("evaluation_at"), label="handoff.evaluation_at")
    for field in ("audit_ready", "admission_ready", "handoff_ready"):
        if not isinstance(handoff.get(field), bool):
            raise DailyResearchServiceLaunchGateAuditError(
                "FIELD_INVALID", f"handoff {field} is invalid"
            )
    if handoff.get("run_status") not in _HANDOFF_STATUSES:
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "handoff run status is invalid"
        )
    if handoff.get("admission_status") not in _ADMISSION_STATUSES:
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "handoff admission status is invalid"
        )
    _validate_issues(handoff.get("issues"), label="handoff")
    expected_ready = (
        handoff["run_status"] == "ready"
        and handoff["audit_ready"]
        and handoff["admission_status"] == "ready"
        and handoff["admission_ready"]
    )
    if handoff["handoff_ready"] is not expected_ready or handoff["status"] != (
        "ready" if expected_ready else "blocked"
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "handoff readiness differs from status"
        )
    refs: dict[str, str] = {
        "handoff_path": handoff_relative,
        "handoff_sha256": handoff_sha,
        "handoff_report_path": report_relative,
        "handoff_report_sha256": report_sha,
    }
    for path_field, sha_field in (
        ("run_report_path", "run_report_sha256"),
        ("run_audit_report_path", "run_audit_report_sha256"),
        ("admission_path", "admission_sha256"),
        ("admission_report_path", "admission_report_sha256"),
    ):
        _, relative, actual_sha = _resolve_declared(
            handoff.get(path_field),
            handoff.get(sha_field),
            root=root,
            label=f"handoff {path_field}",
        )
        if handoff[path_field] != relative or handoff[sha_field] != actual_sha:
            raise DailyResearchServiceLaunchGateAuditError(
                "CHAIN_MISMATCH", f"handoff {path_field} differs"
            )
    return handoff, refs


def _validate_handoff_audit(
    audit_path: Path, *, root: Path, handoff: Mapping[str, Any], handoff_refs: Mapping[str, str]
) -> tuple[dict[str, Any], str]:
    audit_file, audit_relative, audit_sha = _safe_file(
        audit_path, root=root, label="handoff audit report"
    )
    audit = _read_json(audit_file, label="handoff audit report")
    if set(audit) != _HANDOFF_AUDIT_FIELDS:
        raise DailyResearchServiceLaunchGateAuditError(
            "SCHEMA_INVALID", "handoff audit fields are invalid"
        )
    _validate_self_hash(audit, label="handoff audit")
    if audit.get("audit_version") != DAILY_RESEARCH_HANDOFF_AUDIT_VERSION:
        raise DailyResearchServiceLaunchGateAuditError(
            "VERSION_MISMATCH", "handoff audit version is invalid"
        )
    if audit.get("status") not in _HANDOFF_STATUSES:
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "handoff audit status is invalid"
        )
    if audit.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateAuditError(
            "DECISION_GATE_INVALID", "handoff audit decision_ready must be false"
        )
    for field in ("audit_ready", "handoff_ready"):
        if not isinstance(audit.get(field), bool):
            raise DailyResearchServiceLaunchGateAuditError(
                "FIELD_INVALID", f"handoff audit {field} is invalid"
            )
    _validate_issues(audit.get("issues"), label="handoff audit")
    for field in ("handoff_path", "handoff_sha256", "handoff_report_path", "handoff_report_sha256"):
        if audit.get(field) != handoff_refs[field]:
            raise DailyResearchServiceLaunchGateAuditError(
                "CHAIN_MISMATCH", f"handoff audit {field} differs"
            )
    for field in (
        "run_report_path",
        "run_report_sha256",
        "run_audit_report_path",
        "run_audit_report_sha256",
        "admission_path",
        "admission_sha256",
        "admission_report_path",
        "admission_report_sha256",
    ):
        if audit.get(field) != handoff.get(field):
            raise DailyResearchServiceLaunchGateAuditError(
                "CHAIN_MISMATCH", f"handoff audit {field} differs"
            )
    if audit.get("symbol") != handoff.get("symbol") or audit.get("as_of") != handoff.get("as_of"):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "handoff audit metadata differs"
        )
    if audit.get("evaluation_at") != handoff.get("evaluation_at"):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "handoff audit evaluation differs"
        )
    if audit.get("status") != handoff.get("status") or audit.get("handoff_ready") != handoff.get(
        "handoff_ready"
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "handoff audit state differs"
        )
    return audit, audit_relative


def _audit_inputs(*, gate_path: Path, gate_report_path: Path, root: Path) -> dict[str, Any]:
    gate_file, gate_relative, gate_sha = _safe_file(gate_path, root=root, label="daily launch gate")
    report_file, report_relative, report_sha = _safe_file(
        gate_report_path, root=root, label="daily launch gate report"
    )
    if gate_file == report_file:
        raise DailyResearchServiceLaunchGateAuditError(
            "PATH_INVALID", "gate inputs must be distinct"
        )
    gate = _read_json(gate_file, label="daily launch gate")
    gate_report = _read_json(report_file, label="daily launch gate report")
    _validate_pair(gate, gate_report, fields=_GATE_FIELDS, label="daily launch gate")
    if gate_file.name != "daily_research_service_launch_gate.json" or report_file.name != (
        "daily_research_service_launch_gate_report.json"
    ):
        raise DailyResearchServiceLaunchGateAuditError("PATH_INVALID", "gate filenames are invalid")
    if gate.get("gate_version") != _GATE_VERSION:
        raise DailyResearchServiceLaunchGateAuditError(
            "VERSION_MISMATCH", "gate version is invalid"
        )
    if gate.get("status") not in _GATE_STATUSES:
        raise DailyResearchServiceLaunchGateAuditError("STATE_INVALID", "gate status is invalid")
    if gate.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateAuditError(
            "DECISION_GATE_INVALID", "gate decision_ready must be false"
        )
    for field in (
        "receipt_ready",
        "daily_admission_ready",
        "audit_ready",
        "handoff_ready",
        "gate_ready",
    ):
        if not isinstance(gate.get(field), bool):
            raise DailyResearchServiceLaunchGateAuditError(
                "FIELD_INVALID", f"gate {field} is invalid"
            )
    _validate_issues(gate.get("issues"), label="gate")
    launch_path, _ = _safe_relative(
        gate.get("launch_manifest_path"), root=root, label="gate launch manifest"
    )
    handoff_path, _ = _safe_relative(gate.get("handoff_path"), root=root, label="gate handoff")
    handoff_report_path, _ = _safe_relative(
        gate.get("handoff_report_path"), root=root, label="gate handoff report"
    )
    handoff_audit_path, _ = _safe_relative(
        gate.get("handoff_audit_report_path"),
        root=root,
        label="gate handoff audit report",
    )
    admission_path, _ = _safe_relative(
        gate.get("daily_admission_path"), root=root, label="gate daily admission"
    )
    admission_report_path, _ = _safe_relative(
        gate.get("daily_admission_report_path"),
        root=root,
        label="gate daily admission report",
    )
    launch, launch_refs = _validate_launch_manifest(launch_path, root=root)
    handoff, handoff_refs = _validate_handoff_pair(handoff_path, handoff_report_path, root=root)
    audit, audit_relative = _validate_handoff_audit(
        handoff_audit_path,
        root=root,
        handoff=handoff,
        handoff_refs=handoff_refs,
    )
    admission, admission_refs = _validate_admission_pair(
        admission_path, admission_report_path, root=root
    )
    expected_gate_refs = {
        "launch_manifest_path": launch_refs["launch_manifest_path"],
        "launch_manifest_sha256": launch_refs["launch_manifest_sha256"],
        **handoff_refs,
        "handoff_audit_report_path": audit_relative,
        "handoff_audit_report_sha256": sha256_bytes((root / audit_relative).read_bytes()),
        **admission_refs,
    }
    for field, expected in expected_gate_refs.items():
        if gate.get(field) != expected:
            raise DailyResearchServiceLaunchGateAuditError(
                "CHAIN_MISMATCH", f"gate {field} differs"
            )
    if (
        handoff["admission_path"] != admission_refs["daily_admission_path"]
        or handoff["admission_sha256"] != admission_refs["daily_admission_sha256"]
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "handoff admission differs"
        )
    if (
        handoff["admission_report_path"] != admission_refs["daily_admission_report_path"]
        or handoff["admission_report_sha256"] != admission_refs["daily_admission_report_sha256"]
    ):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "handoff admission report differs"
        )
    if admission.get("symbol") is not None and admission.get("symbol") != handoff.get("symbol"):
        raise DailyResearchServiceLaunchGateAuditError("CHAIN_MISMATCH", "admission symbol differs")
    if admission.get("as_of") is not None and admission.get("as_of") != handoff.get("as_of"):
        raise DailyResearchServiceLaunchGateAuditError("CHAIN_MISMATCH", "admission as_of differs")
    if admission.get("evaluation_at") != handoff.get("evaluation_at"):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "admission evaluation differs"
        )
    if gate.get("symbol") != handoff.get("symbol") or gate.get("as_of") != handoff.get("as_of"):
        raise DailyResearchServiceLaunchGateAuditError("CHAIN_MISMATCH", "gate metadata differs")
    if gate.get("evaluation_at") != handoff.get("evaluation_at"):
        raise DailyResearchServiceLaunchGateAuditError("CHAIN_MISMATCH", "gate evaluation differs")
    if gate.get("receipt_ready") != launch["receipt_ready"]:
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "gate receipt readiness differs"
        )
    if gate.get("daily_admission_status") != handoff.get("admission_status") or gate.get(
        "daily_admission_status"
    ) != admission.get("status"):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "gate admission status differs"
        )
    if gate.get("daily_admission_ready") != handoff.get("admission_ready") or gate.get(
        "daily_admission_ready"
    ) != admission.get("admission_ready"):
        raise DailyResearchServiceLaunchGateAuditError(
            "CHAIN_MISMATCH", "gate admission readiness differs"
        )
    if gate.get("audit_ready") != audit.get("audit_ready") or gate.get(
        "handoff_ready"
    ) != handoff.get("handoff_ready"):
        raise DailyResearchServiceLaunchGateAuditError("CHAIN_MISMATCH", "gate audit state differs")
    expected_status = (
        "stale"
        if admission["status"] == "stale"
        else "ready"
        if launch["receipt_ready"]
        and admission["status"] == "ready"
        and admission["admission_ready"]
        and audit["audit_ready"]
        and handoff["handoff_ready"]
        else "blocked"
    )
    if gate["status"] != expected_status or gate["gate_ready"] is not (expected_status == "ready"):
        raise DailyResearchServiceLaunchGateAuditError(
            "STATE_INVALID", "gate status/readiness differs"
        )
    return {
        "gate_path": gate_relative,
        "gate_sha256": gate_sha,
        "gate_report_path": report_relative,
        "gate_report_sha256": report_sha,
        "launch_manifest_path": launch_refs["launch_manifest_path"],
        "launch_manifest_sha256": launch_refs["launch_manifest_sha256"],
        "handoff_path": handoff_refs["handoff_path"],
        "handoff_sha256": handoff_refs["handoff_sha256"],
        "handoff_report_path": handoff_refs["handoff_report_path"],
        "handoff_report_sha256": handoff_refs["handoff_report_sha256"],
        "handoff_audit_report_path": audit_relative,
        "handoff_audit_report_sha256": expected_gate_refs["handoff_audit_report_sha256"],
        "daily_admission_path": admission_refs["daily_admission_path"],
        "daily_admission_sha256": admission_refs["daily_admission_sha256"],
        "daily_admission_report_path": admission_refs["daily_admission_report_path"],
        "daily_admission_report_sha256": admission_refs["daily_admission_report_sha256"],
        "symbol": handoff["symbol"],
        "as_of": handoff["as_of"],
        "evaluation_at": handoff["evaluation_at"],
        "status": gate["status"],
        "gate_ready": gate["gate_ready"],
    }


def _base_report() -> dict[str, Any]:
    return {field: None for field in _AUDIT_OUTPUT_FIELDS} | {
        "audit_version": DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION,
        "status": "invalid",
        "gate_ready": False,
        "audit_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = _json_bytes({**report, "output_sha256": None})
        report["output_sha256"] = sha256_bytes(raw)
        write_atomic(
            output_dir / "daily_research_service_launch_gate_audit_report.json", _json_bytes(report)
        )
    except OSError as exc:
        raise DailyResearchServiceLaunchGateAuditError(
            "OUTPUT_UNAVAILABLE", "launch gate audit output is unavailable"
        ) from exc
    return report


def audit_daily_research_service_launch_gate(
    *, gate_path: Path, gate_report_path: Path, artifact_root: Path, output_dir: Path
) -> dict[str, Any]:
    """Audit an existing gate and its referenced bytes without rebuilding or serving."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceLaunchGateAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        output_dir.resolve().relative_to(root)
    except ValueError as exc:
        raise DailyResearchServiceLaunchGateAuditError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir escapes artifact root"
        ) from exc
    result = _base_report()
    try:
        result.update(
            _audit_inputs(gate_path=gate_path, gate_report_path=gate_report_path, root=root)
        )
        result.update(audit_ready=True, issues=[])
    except DailyResearchServiceLaunchGateAuditError as exc:
        result.update(audit_ready=False, issues=[{"code": exc.code, "message": _safe_message(exc)}])
    return _write_report(result, output_dir=output_dir)


__all__ = [
    "DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION",
    "DailyResearchServiceLaunchGateAuditError",
    "audit_daily_research_service_launch_gate",
]
