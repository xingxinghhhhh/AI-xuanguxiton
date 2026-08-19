"""Independently audit a Node71 daily research service release run receipt."""

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
from .daily_research_service_release import (
    DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
)
from .daily_research_service_release_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION,
)
from .daily_research_service_release_run import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION,
)
from .daily_research_service_release_startup import DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION
from .daily_research_service_run import (
    DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_VERSION,
)
from .daily_research_service_run_audit import (
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
)

DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION = "daily-research-service-release-run-audit-v1"
DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME = (
    "daily_research_service_release_run_audit_report.json"
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
_NODE66_FIELDS = {
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
_NODE67_FIELDS = {
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
_RELEASE_FIELDS = {
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
_RELEASE_REPORT_FIELDS = _RELEASE_FIELDS | {"manifest_path", "manifest_sha256"}
_RELEASE_AUDIT_FIELDS = {
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


class DailyResearchServiceReleaseRunAuditError(ValueError):
    """A sanitized, fail-closed release-run audit error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": str(message)}


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseRunAuditError("PATH_OUTSIDE_ROOT", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceReleaseRunAuditError("PATH_OUTSIDE_ROOT", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise DailyResearchServiceReleaseRunAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is not normalized"
        )
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAuditError(
            "PATH_OUTSIDE_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceReleaseRunAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAuditError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceReleaseRunAuditError(
            "JSON_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceReleaseRunAuditError("JSON_INVALID", f"{label} must be an object")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceReleaseRunAuditError(
            "SELF_HASH_MISMATCH", f"{label} self-hash differs"
        )


def _time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseRunAuditError("TIME_MISMATCH", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceReleaseRunAuditError(
            "TIME_MISMATCH", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceReleaseRunAuditError("TIME_MISMATCH", f"{label} needs timezone")
    return parsed


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", f"{label} is invalid")
        result.append(_issue(item["code"], item["message"]))
    return result


def _validate_common(
    payload: Mapping[str, Any], fields: set[str], *, label: str
) -> list[dict[str, str]]:
    if set(payload) != fields:
        raise DailyResearchServiceReleaseRunAuditError(
            "UNKNOWN_FIELD", f"{label} fields are invalid"
        )
    _self_hash(payload, label=label)
    if payload.get("decision_ready") is not False:
        raise DailyResearchServiceReleaseRunAuditError(
            "DECISION_GATE_INVALID", f"{label} decision_ready must be false"
        )
    if not isinstance(payload.get("symbol"), str) or not payload["symbol"].strip():
        raise DailyResearchServiceReleaseRunAuditError(
            "FIELD_MISMATCH", f"{label} symbol is invalid"
        )
    as_of = _time(payload.get("as_of"), label=f"{label} as_of")
    evaluation_at = _time(payload.get("evaluation_at"), label=f"{label} evaluation_at")
    if as_of > evaluation_at:
        raise DailyResearchServiceReleaseRunAuditError(
            "TIME_MISMATCH", f"{label} as_of is after evaluation_at"
        )
    return _issues(payload.get("issues"), label=f"{label} issues")


def _validate_node71(run: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(run) != _RUN_FIELDS:
        raise DailyResearchServiceReleaseRunAuditError(
            "UNKNOWN_FIELD", "Node71 run report fields are invalid"
        )
    _self_hash(run, label="Node71 run report")
    if run["decision_ready"] is not False:
        raise DailyResearchServiceReleaseRunAuditError(
            "DECISION_GATE_INVALID", "Node71 decision_ready must be false"
        )
    issues = _issues(run["issues"], label="Node71 run issues")
    if run["run_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION:
        raise DailyResearchServiceReleaseRunAuditError(
            "VERSION_MISMATCH", "Node71 run version is invalid"
        )
    if run["release_version"] != DAILY_RESEARCH_SERVICE_RELEASE_VERSION:
        raise DailyResearchServiceReleaseRunAuditError(
            "VERSION_MISMATCH", "release version is invalid"
        )
    if run["startup_version"] != DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION:
        raise DailyResearchServiceReleaseRunAuditError(
            "VERSION_MISMATCH", "startup version is invalid"
        )
    if run["startup_status"] not in {"ready", "blocked", "failed"}:
        raise DailyResearchServiceReleaseRunAuditError(
            "FIELD_MISMATCH", "startup_status is invalid"
        )
    if run["startup_status"] == "blocked" and run["symbol"] is None:
        if run["as_of"] is not None or run["evaluation_at"] is not None:
            raise DailyResearchServiceReleaseRunAuditError(
                "FIELD_MISMATCH", "blocked Node71 identity fields are invalid"
            )
    else:
        if not isinstance(run["symbol"], str) or not run["symbol"].strip():
            raise DailyResearchServiceReleaseRunAuditError(
                "FIELD_MISMATCH", "Node71 symbol is invalid"
            )
        _time(run["as_of"], label="Node71 as_of")
        _time(run["evaluation_at"], label="Node71 evaluation_at")
    if not isinstance(run["startup_ready"], bool):
        raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", "startup_ready is invalid")
    if run["probe_status"] not in {"not_started", "ready", "failed", "timeout", "service_exited"}:
        raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", "probe_status is invalid")
    if run["probe_exit_code"] is not None and (
        isinstance(run["probe_exit_code"], bool)
        or not isinstance(run["probe_exit_code"], int)
        or run["probe_exit_code"] < 0
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "FIELD_MISMATCH", "probe_exit_code is invalid"
        )
    if run["stop_status"] not in {"not_attempted", "controlled", "uncontrolled_exit", "failed"}:
        raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", "stop_status is invalid")
    for field in ("service_stopped", "run_ready"):
        if not isinstance(run[field], bool):
            raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", f"{field} is invalid")
    if run["run_status"] not in {"ready", "blocked", "failed"}:
        raise DailyResearchServiceReleaseRunAuditError("FIELD_MISMATCH", "run_status is invalid")
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
    return issues


def _validate_release_chain(
    run: Mapping[str, Any], *, root: Path
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    bool,
    dict[str, str],
]:
    manifest_file = _safe_file(
        root / Path(_relative(run["release_manifest_path"], label="release manifest path")),
        root=root,
        label="release manifest",
    )
    report_file = _safe_file(
        root / Path(_relative(run["release_report_path"], label="release report path")),
        root=root,
        label="release report",
    )
    release_audit_file = _safe_file(
        root / Path(_relative(run["release_audit_report_path"], label="release audit report path")),
        root=root,
        label="release audit report",
    )
    if manifest_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME:
        raise DailyResearchServiceReleaseRunAuditError(
            "SCHEMA_INVALID", "release manifest filename is invalid"
        )
    if report_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME:
        raise DailyResearchServiceReleaseRunAuditError(
            "SCHEMA_INVALID", "release report filename is invalid"
        )
    if release_audit_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME:
        raise DailyResearchServiceReleaseRunAuditError(
            "SCHEMA_INVALID", "release audit filename is invalid"
        )
    if run["release_manifest_sha256"] != manifest_file["sha256"]:
        raise DailyResearchServiceReleaseRunAuditError(
            "HASH_MISMATCH", "release manifest SHA differs"
        )
    if run["release_report_sha256"] != report_file["sha256"]:
        raise DailyResearchServiceReleaseRunAuditError(
            "HASH_MISMATCH", "release report SHA differs"
        )
    if run["release_audit_report_sha256"] != release_audit_file["sha256"]:
        raise DailyResearchServiceReleaseRunAuditError(
            "HASH_MISMATCH", "release audit report SHA differs"
        )
    manifest = _read_json(manifest_file["raw"], label="release manifest")
    report = _read_json(report_file["raw"], label="release report")
    release_audit = _read_json(release_audit_file["raw"], label="release audit report")
    for payload, fields, label, version in (
        (manifest, _RELEASE_FIELDS, "release manifest", DAILY_RESEARCH_SERVICE_RELEASE_VERSION),
        (report, _RELEASE_REPORT_FIELDS, "release report", DAILY_RESEARCH_SERVICE_RELEASE_VERSION),
        (
            release_audit,
            _RELEASE_AUDIT_FIELDS,
            "release audit report",
            DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION,
        ),
    ):
        _validate_common(payload, fields, label=label)
        if payload["release_version"] != DAILY_RESEARCH_SERVICE_RELEASE_VERSION:
            raise DailyResearchServiceReleaseRunAuditError(
                "VERSION_MISMATCH", f"{label} version is invalid"
            )
        if label == "release audit report" and payload["audit_version"] != version:
            raise DailyResearchServiceReleaseRunAuditError(
                "VERSION_MISMATCH", f"{label} version is invalid"
            )
    if report["manifest_path"] != manifest_file["relative_path"]:
        raise DailyResearchServiceReleaseRunAuditError(
            "CHAIN_MISMATCH", "release manifest path differs"
        )
    if report["manifest_sha256"] != manifest_file["sha256"]:
        raise DailyResearchServiceReleaseRunAuditError(
            "HASH_MISMATCH", "release manifest SHA binding differs"
        )
    if (
        release_audit["manifest_path"] != manifest_file["relative_path"]
        or release_audit["report_path"] != report_file["relative_path"]
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "CHAIN_MISMATCH", "release audit paths differ"
        )
    if (
        release_audit["manifest_sha256"] != manifest_file["sha256"]
        or release_audit["report_sha256"] != report_file["sha256"]
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "HASH_MISMATCH", "release audit SHA binding differs"
        )
    for payload in (manifest, report, release_audit):
        for field in ("symbol", "as_of", "evaluation_at"):
            if run[field] is not None and payload[field] != run[field]:
                raise DailyResearchServiceReleaseRunAuditError(
                    "FIELD_MISMATCH", f"release {field} differs"
                )
    for field in (
        "run_report_path",
        "run_report_sha256",
        "run_audit_report_path",
        "run_audit_report_sha256",
    ):
        if manifest[field] != report[field] or manifest[field] != release_audit[field]:
            raise DailyResearchServiceReleaseRunAuditError(
                "FIELD_MISMATCH", f"release {field} differs"
            )
    run_path = _relative(manifest["run_report_path"], label="Node66 run report path")
    run_audit_path = _relative(manifest["run_audit_report_path"], label="Node67 audit path")
    node66_file = _safe_file(root / Path(run_path), root=root, label="Node66 run report")
    node67_file = _safe_file(root / Path(run_audit_path), root=root, label="Node67 audit report")
    if node66_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME:
        raise DailyResearchServiceReleaseRunAuditError(
            "SCHEMA_INVALID", "Node66 run filename is invalid"
        )
    if node67_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME:
        raise DailyResearchServiceReleaseRunAuditError(
            "SCHEMA_INVALID", "Node67 audit filename is invalid"
        )
    if (
        manifest["run_report_sha256"] != node66_file["sha256"]
        or manifest["run_audit_report_sha256"] != node67_file["sha256"]
    ):
        raise DailyResearchServiceReleaseRunAuditError("HASH_MISMATCH", "upstream run SHA differs")
    node66 = _read_json(node66_file["raw"], label="Node66 run report")
    node67 = _read_json(node67_file["raw"], label="Node67 audit report")
    node66_issues = _validate_common(node66, _NODE66_FIELDS, label="Node66 run report")
    _validate_common(node67, _NODE67_FIELDS, label="Node67 audit report")
    if (
        node66["run_version"] != DAILY_RESEARCH_SERVICE_RUN_VERSION
        or node67["audit_version"] != DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "VERSION_MISMATCH", "Node66/67 version is invalid"
        )
    if (
        node67["run_report_path"] != run_path
        or node67["run_report_sha256"] != node66_file["sha256"]
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "CHAIN_MISMATCH", "Node67 run binding differs"
        )
    for field in (
        "symbol",
        "as_of",
        "evaluation_at",
        "gate_path",
        "gate_sha256",
        "audit_path",
        "audit_sha256",
    ):
        if node67[field] != node66[field]:
            raise DailyResearchServiceReleaseRunAuditError(
                "FIELD_MISMATCH", f"Node66/67 {field} differs"
            )
    if (
        node67["run_ready"] != node66["run_ready"]
        or node67["service_stopped"] != node66["service_stopped"]
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "FIELD_MISMATCH", "Node66/67 readiness differs"
        )
    gate_path = _relative(node66["gate_path"], label="Node65 gate path")
    gate_audit_path = _relative(node66["audit_path"], label="Node65 audit path")
    gate_file = _safe_file(root / Path(gate_path), root=root, label="Node65 gate")
    gate_audit_file = _safe_file(root / Path(gate_audit_path), root=root, label="Node65 audit")
    if (
        gate_file["path"].name != "daily_research_service_launch_gate.json"
        or gate_audit_file["path"].name != "daily_research_service_launch_gate_audit_report.json"
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "SCHEMA_INVALID", "Node65 filename is invalid"
        )
    if (
        node66["gate_sha256"] != gate_file["sha256"]
        or node66["audit_sha256"] != gate_audit_file["sha256"]
    ):
        raise DailyResearchServiceReleaseRunAuditError("HASH_MISMATCH", "Node65 SHA differs")
    try:
        startup = load_daily_research_service_launch_gate_startup(
            gate_path=gate_file["path"], audit_path=gate_audit_file["path"], artifact_root=root
        )
        node65_ready = startup.launch_ready
    except DailyResearchServiceLaunchGateStartupError:
        node65_ready = False
    expected_node66_status = (
        "blocked"
        if node66["startup_status"] == "blocked"
        else "ready"
        if node66["run_ready"]
        else "failed"
    )
    if node67["run_status"] != expected_node66_status or node66_issues != _issues(
        node67["issues"], label="Node67 issues"
    ):
        raise DailyResearchServiceReleaseRunAuditError("STATE_MISMATCH", "Node66/67 state differs")
    expected_audit_ready = node67["audit_ready"] is True
    expected_release_ready = (
        expected_audit_ready
        and expected_node66_status == "ready"
        and node66["startup_status"] == "ready"
        and node66["probe_status"] == "ready"
        and node66["probe_exit_code"] == 0
        and node66["run_ready"] is True
        and node66["service_stopped"] is True
        and not node66_issues
        and node65_ready
    )
    expected_release_status = (
        "ready" if expected_release_ready else "blocked" if expected_audit_ready else "invalid"
    )
    if (
        manifest["release_ready"] != expected_release_ready
        or report["release_ready"] != expected_release_ready
        or release_audit["release_ready"] != expected_release_ready
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "STATE_MISMATCH", "release_ready is inconsistent"
        )
    if (
        manifest["status"] != expected_release_status
        or report["status"] != expected_release_status
        or release_audit["status"] != expected_release_status
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "STATE_MISMATCH", "release status is inconsistent"
        )
    if (
        not isinstance(release_audit["audit_ready"], bool)
        or manifest["audit_ready"] != expected_audit_ready
        or report["audit_ready"] != expected_audit_ready
        or release_audit["audit_ready"] != expected_audit_ready
    ):
        raise DailyResearchServiceReleaseRunAuditError(
            "STATE_MISMATCH", "release audit_ready is inconsistent"
        )
    return (
        manifest_file,
        report_file,
        release_audit_file,
        node66_file,
        node67_file,
        gate_file,
        gate_audit_file,
        expected_release_ready,
        {
            "symbol": manifest["symbol"],
            "as_of": manifest["as_of"],
            "evaluation_at": manifest["evaluation_at"],
        },
    )


def _base_report() -> dict[str, Any]:
    return {
        "audit_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION,
        "run_version": None,
        "release_version": None,
        "startup_version": None,
        "run_report_path": None,
        "run_report_sha256": None,
        "release_manifest_path": None,
        "release_manifest_sha256": None,
        "release_report_path": None,
        "release_report_sha256": None,
        "release_audit_report_path": None,
        "release_audit_report_sha256": None,
        "node66_run_report_path": None,
        "node66_run_report_sha256": None,
        "node67_run_audit_report_path": None,
        "node67_run_audit_report_sha256": None,
        "node65_gate_path": None,
        "node65_gate_sha256": None,
        "node65_audit_path": None,
        "node65_audit_sha256": None,
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
        "status": "invalid",
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
        write_atomic(
            output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME, _json_bytes(report)
        )
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAuditError(
            "OUTPUT_UNAVAILABLE", "audit output is unavailable"
        ) from exc
    return report


def audit_daily_research_service_release_run(
    *, run_report_path: Path, artifact_root: Path, output_dir: Path
) -> dict[str, Any]:
    """Audit Node71 and its upstream chain without starting or probing services."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAuditError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAuditError(
            "OUTPUT_DIR_INVALID", "output directory escapes artifact root"
        ) from exc
    result = _base_report()
    try:
        run_file = _safe_file(run_report_path, root=root, label="Node71 run report")
        if run_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME:
            raise DailyResearchServiceReleaseRunAuditError(
                "SCHEMA_INVALID", "Node71 run filename is invalid"
            )
        result["run_report_path"] = run_file["relative_path"]
        result["run_report_sha256"] = run_file["sha256"]
        run = _read_json(run_file["raw"], label="Node71 run report")
        run_issues = _validate_node71(run)
        (
            manifest_file,
            report_file,
            release_audit_file,
            node66_file,
            node67_file,
            gate_file,
            gate_audit_file,
            release_ready,
            release_identity,
        ) = _validate_release_chain(run, root=root)
        if run["startup_status"] == "blocked":
            if (
                release_ready
                or run["startup_ready"]
                or run["probe_status"] != "not_started"
                or run["stop_status"] != "not_attempted"
                or run["service_stopped"]
                or run["run_ready"]
                or run["run_status"] != "blocked"
                or not run_issues
            ):
                raise DailyResearchServiceReleaseRunAuditError(
                    "STATE_MISMATCH", "blocked Node71 state is inconsistent"
                )
            status = "blocked"
        else:
            if not release_ready or not run["startup_ready"]:
                raise DailyResearchServiceReleaseRunAuditError(
                    "STATE_MISMATCH", "Node71 startup admission is inconsistent"
                )
            expected_ready = (
                run["startup_status"] == "ready"
                and run["probe_status"] == "ready"
                and run["probe_exit_code"] == 0
                and run["stop_status"] == "controlled"
                and run["service_stopped"] is True
                and run["run_ready"] is True
                and not run_issues
            )
            if run["run_ready"] != expected_ready or run["run_status"] != (
                "ready" if expected_ready else "failed"
            ):
                raise DailyResearchServiceReleaseRunAuditError(
                    "STATE_MISMATCH", "Node71 run state is inconsistent"
                )
            if not expected_ready and not run_issues:
                raise DailyResearchServiceReleaseRunAuditError(
                    "STATE_MISMATCH", "failed Node71 run has no issue"
                )
            status = "ready" if expected_ready else "failed"
        result.update(
            {
                "run_version": run["run_version"],
                "release_version": run["release_version"],
                "startup_version": run["startup_version"],
                "release_manifest_path": manifest_file["relative_path"],
                "release_manifest_sha256": manifest_file["sha256"],
                "release_report_path": report_file["relative_path"],
                "release_report_sha256": report_file["sha256"],
                "release_audit_report_path": release_audit_file["relative_path"],
                "release_audit_report_sha256": release_audit_file["sha256"],
                "node66_run_report_path": node66_file["relative_path"],
                "node66_run_report_sha256": node66_file["sha256"],
                "node67_run_audit_report_path": node67_file["relative_path"],
                "node67_run_audit_report_sha256": node67_file["sha256"],
                "node65_gate_path": gate_file["relative_path"],
                "node65_gate_sha256": gate_file["sha256"],
                "node65_audit_path": gate_audit_file["relative_path"],
                "node65_audit_sha256": gate_audit_file["sha256"],
                "symbol": run["symbol"] or release_identity["symbol"],
                "as_of": run["as_of"] or release_identity["as_of"],
                "evaluation_at": run["evaluation_at"] or release_identity["evaluation_at"],
                "startup_status": run["startup_status"],
                "startup_ready": run["startup_ready"],
                "probe_status": run["probe_status"],
                "probe_exit_code": run["probe_exit_code"],
                "stop_status": run["stop_status"],
                "service_stopped": run["service_stopped"],
                "run_status": run["run_status"],
                "run_ready": run["run_ready"],
                "status": status,
                "audit_ready": True,
                "issues": run_issues,
            }
        )
    except DailyResearchServiceReleaseRunAuditError as exc:
        result["issues"] = [_issue(exc.code, str(exc))]
    return _finalize(result, output_dir=output_dir)


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_VERSION",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME",
    "DailyResearchServiceReleaseRunAuditError",
    "audit_daily_research_service_release_run",
]
