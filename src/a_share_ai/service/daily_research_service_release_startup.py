"""Bind a ready Node68/69 release chain to the existing Node65 startup."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes
from .daily_research_service_launch_gate import DailyResearchServiceLaunchGateConfig
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
from .daily_research_service_run import (
    DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_VERSION,
)
from .daily_research_service_run_audit import (
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
)

DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION = "daily-research-service-release-startup-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
_RUN_AUDIT_FIELDS = {
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
_COMMON_RELEASE_FIELDS = (
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
)


class DailyResearchServiceReleaseStartupError(ValueError):
    """A sanitized, fail-closed release startup validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DailyResearchServiceReleaseStartupConfig:
    """Validated release startup values; absolute paths remain internal."""

    artifact_root: Path
    manifest_path: Path
    report_path: Path
    audit_report_path: Path
    manifest_sha256: str
    report_sha256: str
    audit_sha256: str
    release_version: str
    symbol: str
    as_of: str
    evaluation_at: str
    status: str
    release_ready: bool
    audit_ready: bool
    startup_ready: bool
    gate_path: Path
    gate_audit_path: Path
    decision_ready: bool
    gate_config: DailyResearchServiceLaunchGateConfig


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", f"{label} is invalid")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceReleaseStartupError(
            "SELF_HASH_MISMATCH", f"{label} self-hash differs"
        )


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseStartupError(
            "PATH_OUTSIDE_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceReleaseStartupError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceReleaseStartupError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return {"path": candidate, "relative_path": relative, "raw": raw, "sha256": sha256_bytes(raw)}


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseStartupError("PATH_OUTSIDE_ROOT", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceReleaseStartupError("PATH_OUTSIDE_ROOT", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise DailyResearchServiceReleaseStartupError(
            "PATH_OUTSIDE_ROOT", f"{label} is not normalized"
        )
    return normalized


def _declared_file(value: Any, *, root: Path, label: str) -> dict[str, Any]:
    relative = _relative(value, label=label)
    return _safe_file(root / Path(relative), root=root, label=label)


def _read_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceReleaseStartupError(
            "INPUT_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceReleaseStartupError("INPUT_INVALID", f"{label} must be an object")
    return value


def _time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseStartupError("FIELD_MISMATCH", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceReleaseStartupError(
            "FIELD_MISMATCH", f"{label} is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceReleaseStartupError("FIELD_MISMATCH", f"{label} needs timezone")
    return parsed


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchServiceReleaseStartupError("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise DailyResearchServiceReleaseStartupError("FIELD_MISMATCH", f"{label} is invalid")
        result.append({"code": item["code"], "message": item["message"]})
    return result


def _validate_payload(
    payload: Mapping[str, Any], *, fields: set[str], version_field: str, version: str, label: str
) -> None:
    if set(payload) != fields:
        raise DailyResearchServiceReleaseStartupError(
            "SCHEMA_INVALID", f"{label} fields are invalid"
        )
    if payload.get(version_field) != version:
        raise DailyResearchServiceReleaseStartupError(
            "VERSION_MISMATCH", f"{label} version is invalid"
        )
    _self_hash(payload, label=label)
    if payload.get("decision_ready") is not False:
        raise DailyResearchServiceReleaseStartupError(
            "DECISION_GATE_INVALID", f"{label} decision_ready must be false"
        )
    _issues(payload.get("issues"), label=f"{label}.issues")


def _validate_release_pair(
    manifest_file: Mapping[str, Any],
    report_file: Mapping[str, Any],
    audit_file: Mapping[str, Any],
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> None:
    _validate_payload(
        manifest,
        fields=_MANIFEST_FIELDS,
        version_field="release_version",
        version=DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
        label="release manifest",
    )
    _validate_payload(
        report,
        fields=_REPORT_FIELDS,
        version_field="release_version",
        version=DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
        label="release report",
    )
    _validate_payload(
        audit,
        fields=_RELEASE_AUDIT_FIELDS,
        version_field="audit_version",
        version=DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION,
        label="release audit",
    )
    if manifest_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME:
        raise DailyResearchServiceReleaseStartupError(
            "SCHEMA_INVALID", "manifest filename is invalid"
        )
    if report_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME:
        raise DailyResearchServiceReleaseStartupError(
            "SCHEMA_INVALID", "report filename is invalid"
        )
    if audit_file["path"].name != DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_REPORT_NAME:
        raise DailyResearchServiceReleaseStartupError("SCHEMA_INVALID", "audit filename is invalid")
    if report["manifest_path"] != manifest_file["relative_path"]:
        raise DailyResearchServiceReleaseStartupError(
            "CHAIN_MISMATCH", "report manifest path differs"
        )
    if report["manifest_sha256"] != manifest_file["sha256"]:
        raise DailyResearchServiceReleaseStartupError(
            "HASH_MISMATCH", "report manifest SHA differs"
        )
    if audit["manifest_path"] != manifest_file["relative_path"]:
        raise DailyResearchServiceReleaseStartupError(
            "CHAIN_MISMATCH", "audit manifest path differs"
        )
    if audit["report_path"] != report_file["relative_path"]:
        raise DailyResearchServiceReleaseStartupError("CHAIN_MISMATCH", "audit report path differs")
    if audit["manifest_sha256"] != manifest_file["sha256"]:
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "audit manifest SHA differs")
    if audit["report_sha256"] != report_file["sha256"]:
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "audit report SHA differs")
    if manifest["release_version"] != audit["release_version"]:
        raise DailyResearchServiceReleaseStartupError("VERSION_MISMATCH", "release versions differ")
    for field in _COMMON_RELEASE_FIELDS:
        if manifest[field] != report[field]:
            raise DailyResearchServiceReleaseStartupError(
                "FIELD_MISMATCH", f"release field {field} differs"
            )
        if field in audit and manifest[field] != audit[field]:
            raise DailyResearchServiceReleaseStartupError(
                "FIELD_MISMATCH", f"audit field {field} differs"
            )
    if manifest["run_report_sha256"] != audit["run_report_sha256"]:
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "run report SHA differs")
    if manifest["run_audit_report_sha256"] != audit["run_audit_report_sha256"]:
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "run audit SHA differs")


def _validate_upstream(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    run_file = _declared_file(manifest["run_report_path"], root=root, label="Node66 run report")
    if run_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME:
        raise DailyResearchServiceReleaseStartupError(
            "SCHEMA_INVALID", "run report filename is invalid"
        )
    if (
        run_file["sha256"] != manifest["run_report_sha256"]
        or run_file["sha256"] != audit["run_report_sha256"]
    ):
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "run report SHA differs")
    run = _read_json(run_file["raw"], label="Node66 run report")
    _validate_payload(
        run,
        fields=_RUN_FIELDS,
        version_field="run_version",
        version=DAILY_RESEARCH_SERVICE_RUN_VERSION,
        label="Node66 run report",
    )

    run_audit_file = _declared_file(
        manifest["run_audit_report_path"], root=root, label="Node67 audit report"
    )
    if run_audit_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME:
        raise DailyResearchServiceReleaseStartupError(
            "SCHEMA_INVALID", "run audit filename is invalid"
        )
    if (
        run_audit_file["sha256"] != manifest["run_audit_report_sha256"]
        or run_audit_file["sha256"] != audit["run_audit_report_sha256"]
    ):
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "run audit SHA differs")
    run_audit = _read_json(run_audit_file["raw"], label="Node67 audit report")
    _validate_payload(
        run_audit,
        fields=_RUN_AUDIT_FIELDS,
        version_field="audit_version",
        version=DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
        label="Node67 audit report",
    )
    if run_audit["run_report_path"] != manifest["run_report_path"]:
        raise DailyResearchServiceReleaseStartupError("CHAIN_MISMATCH", "Node67 run path differs")
    if run_audit["run_report_sha256"] != run_file["sha256"]:
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "Node67 run SHA differs")

    for payload, label in ((run, "run"), (run_audit, "run audit")):
        if not isinstance(payload["symbol"], str) or not payload["symbol"].strip():
            raise DailyResearchServiceReleaseStartupError(
                "FIELD_MISMATCH", f"{label} symbol is invalid"
            )
        _time(payload["as_of"], label=f"{label}.as_of")
        _time(payload["evaluation_at"], label=f"{label}.evaluation_at")
    if _time(run["as_of"], label="as_of") > _time(run["evaluation_at"], label="evaluation_at"):
        raise DailyResearchServiceReleaseStartupError(
            "FIELD_MISMATCH", "as_of is after evaluation_at"
        )
    for field in ("symbol", "as_of", "evaluation_at"):
        if (
            run[field] != run_audit[field]
            or run[field] != manifest[field]
            or run[field] != audit[field]
        ):
            raise DailyResearchServiceReleaseStartupError(
                "FIELD_MISMATCH", f"upstream {field} differs"
            )
    for field in (
        "startup_status",
        "probe_status",
        "probe_exit_code",
        "service_stopped",
        "run_ready",
    ):
        if run_audit[field] != run[field]:
            raise DailyResearchServiceReleaseStartupError(
                "FIELD_MISMATCH", f"upstream {field} differs"
            )
    expected_run_status = (
        "blocked"
        if run["startup_status"] == "blocked"
        else "ready"
        if run["run_ready"]
        else "failed"
    )
    if (
        run_audit["run_status"] != expected_run_status
        or manifest["run_status"] != expected_run_status
    ):
        raise DailyResearchServiceReleaseStartupError(
            "STATE_MISMATCH", "run status is inconsistent"
        )
    if run_audit["audit_ready"] is not True:
        raise DailyResearchServiceReleaseStartupError(
            "STATE_MISMATCH", "run audit is not audit_ready"
        )
    if run["decision_ready"] is not False or run_audit["decision_ready"] is not False:
        raise DailyResearchServiceReleaseStartupError(
            "DECISION_GATE_INVALID", "upstream decision gate is invalid"
        )

    gate_file = _declared_file(run["gate_path"], root=root, label="Node65 launch gate")
    gate_audit_file = _declared_file(run["audit_path"], root=root, label="Node65 launch gate audit")
    if gate_file["path"].name != "daily_research_service_launch_gate.json":
        raise DailyResearchServiceReleaseStartupError(
            "SCHEMA_INVALID", "launch gate filename is invalid"
        )
    if gate_audit_file["path"].name != "daily_research_service_launch_gate_audit_report.json":
        raise DailyResearchServiceReleaseStartupError(
            "SCHEMA_INVALID", "launch gate audit filename is invalid"
        )
    if (
        gate_file["sha256"] != run["gate_sha256"]
        or gate_audit_file["sha256"] != run["audit_sha256"]
    ):
        raise DailyResearchServiceReleaseStartupError("HASH_MISMATCH", "Node65 input SHA differs")
    return run, run_audit, gate_file, gate_audit_file


def load_daily_research_service_release_startup(
    *, manifest_path: Path, report_path: Path, audit_report_path: Path, artifact_root: Path
) -> DailyResearchServiceReleaseStartupConfig:
    """Validate Node68/69 and then reuse Node65 startup validation."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceReleaseStartupError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    manifest_file = _safe_file(manifest_path, root=root, label="release manifest")
    report_file = _safe_file(report_path, root=root, label="release report")
    audit_file = _safe_file(audit_report_path, root=root, label="release audit report")
    manifest = _read_json(manifest_file["raw"], label="release manifest")
    report = _read_json(report_file["raw"], label="release report")
    audit = _read_json(audit_file["raw"], label="release audit report")
    _validate_release_pair(manifest_file, report_file, audit_file, manifest, report, audit)
    run, run_audit, gate_file, gate_audit_file = _validate_upstream(
        root=root, manifest=manifest, audit=audit
    )
    if (
        manifest["status"] != "ready"
        or manifest["release_ready"] is not True
        or manifest["audit_ready"] is not True
        or audit["status"] != "ready"
        or audit["release_ready"] is not True
        or audit["audit_ready"] is not True
        or run["run_ready"] is not True
        or run["service_stopped"] is not True
        or run["startup_status"] != "ready"
        or run["probe_status"] != "ready"
        or run["probe_exit_code"] != 0
        or _issues(run["issues"], label="run issues")
        or _issues(run_audit["issues"], label="run audit issues")
        or _issues(manifest["issues"], label="manifest issues")
        or _issues(audit["issues"], label="release audit issues")
    ):
        raise DailyResearchServiceReleaseStartupError(
            "RELEASE_NOT_READY", "release admission is not ready"
        )
    try:
        startup = load_daily_research_service_launch_gate_startup(
            gate_path=gate_file["path"],
            audit_path=gate_audit_file["path"],
            artifact_root=root,
        )
    except DailyResearchServiceLaunchGateStartupError as exc:
        raise DailyResearchServiceReleaseStartupError(exc.code, str(exc)) from exc
    if not startup.launch_ready:
        raise DailyResearchServiceReleaseStartupError(
            "GATE_NOT_READY", "Node65 launch gate is not ready"
        )
    return DailyResearchServiceReleaseStartupConfig(
        artifact_root=root,
        manifest_path=manifest_file["path"],
        report_path=report_file["path"],
        audit_report_path=audit_file["path"],
        manifest_sha256=manifest_file["sha256"],
        report_sha256=report_file["sha256"],
        audit_sha256=audit_file["sha256"],
        release_version=manifest["release_version"],
        symbol=manifest["symbol"],
        as_of=manifest["as_of"],
        evaluation_at=manifest["evaluation_at"],
        status=manifest["status"],
        release_ready=True,
        audit_ready=True,
        startup_ready=True,
        gate_path=gate_file["path"],
        gate_audit_path=gate_audit_file["path"],
        decision_ready=False,
        gate_config=startup.gate_config,
    )


def daily_research_service_release_startup_check_report(
    config: DailyResearchServiceReleaseStartupConfig,
) -> dict[str, Any]:
    """Return a deterministic, path-redacted check-only summary."""

    root = config.artifact_root.resolve()
    return {
        "startup_version": DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION,
        "validation": "passed",
        "manifest_path": config.manifest_path.relative_to(root).as_posix(),
        "report_path": config.report_path.relative_to(root).as_posix(),
        "audit_report_path": config.audit_report_path.relative_to(root).as_posix(),
        "manifest_sha256": config.manifest_sha256,
        "report_sha256": config.report_sha256,
        "audit_report_sha256": config.audit_sha256,
        "symbol": config.symbol,
        "as_of": config.as_of,
        "evaluation_at": config.evaluation_at,
        "status": config.status,
        "release_ready": config.release_ready,
        "audit_ready": config.audit_ready,
        "startup_ready": config.startup_ready,
        "gate_path": config.gate_path.relative_to(root).as_posix(),
        "gate_audit_path": config.gate_audit_path.relative_to(root).as_posix(),
        "decision_ready": False,
    }


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION",
    "DailyResearchServiceReleaseStartupConfig",
    "DailyResearchServiceReleaseStartupError",
    "daily_research_service_release_startup_check_report",
    "load_daily_research_service_release_startup",
]
