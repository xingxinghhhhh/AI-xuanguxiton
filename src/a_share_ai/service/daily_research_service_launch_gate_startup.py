"""Bind the independent daily launch-gate audit to service startup."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes
from .daily_research_service_launch_gate import (
    DailyResearchServiceLaunchGateConfig,
    load_daily_research_service_launch_gate,
)
from .daily_research_service_launch_gate_audit import (
    DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AUDIT_STATUSES = {"ready", "stale", "blocked", "invalid"}
_AUDIT_FIELDS = {
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


class DailyResearchServiceLaunchGateStartupError(ValueError):
    """A sanitized gate/audit startup validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DailyResearchServiceLaunchGateStartupConfig:
    gate_config: DailyResearchServiceLaunchGateConfig
    audit_path: Path
    audit_report: dict[str, Any]
    launch_ready: bool


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceLaunchGateStartupError("HASH_INVALID", f"{label} is invalid")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceLaunchGateStartupError(
            "HASH_MISMATCH", f"{label} self-hash differs"
        )


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceLaunchGateStartupError(
            "INPUT_INVALID", f"{label} is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceLaunchGateStartupError(
            "INPUT_INVALID", f"{label} must be an object"
        )
    return value


def _safe_file(path: Path, *, root: Path, label: str) -> tuple[Path, str, str]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceLaunchGateStartupError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc
    pure = PureWindowsPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or pure.drive
        or any(part in {"", ".", ".."} for part in pure.parts)
        or relative != "/".join(pure.parts)
        or not candidate.is_file()
    ):
        raise DailyResearchServiceLaunchGateStartupError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        )
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceLaunchGateStartupError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return candidate, relative, sha256_bytes(raw)


def _validate_audit_report(
    *,
    audit_path: Path,
    root: Path,
    gate_config: DailyResearchServiceLaunchGateConfig,
    gate_path: Path,
) -> tuple[Path, dict[str, Any], bool]:
    audit_file, audit_relative, audit_sha = _safe_file(
        audit_path, root=root, label="launch gate audit report"
    )
    if audit_file.name != "daily_research_service_launch_gate_audit_report.json":
        raise DailyResearchServiceLaunchGateStartupError(
            "PATH_INVALID", "launch gate audit filename is invalid"
        )
    gate_file, gate_relative, gate_sha = _safe_file(
        gate_path, root=root, label="daily launch gate"
    )
    if gate_file.name == "daily_research_service_launch_gate_report.json":
        gate_file = gate_file.with_name("daily_research_service_launch_gate.json")
        gate_relative = gate_file.relative_to(root.resolve()).as_posix()
        if not gate_file.is_file():
            raise DailyResearchServiceLaunchGateStartupError(
                "INPUT_UNAVAILABLE", "daily launch gate is unavailable"
            )
        gate_sha = sha256_bytes(gate_file.read_bytes())
    if gate_file == audit_file:
        raise DailyResearchServiceLaunchGateStartupError(
            "PATH_INVALID", "launch gate and audit report must be distinct"
        )
    gate_report_file = gate_file.with_name("daily_research_service_launch_gate_report.json")
    try:
        gate_report_relative = gate_report_file.relative_to(root.resolve()).as_posix()
        gate_report_sha = sha256_bytes(gate_report_file.read_bytes())
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceLaunchGateStartupError(
            "INPUT_UNAVAILABLE", "daily launch gate report is unavailable"
        ) from exc
    report = _read_json(audit_file, label="launch gate audit report")
    if set(report) != _AUDIT_FIELDS:
        raise DailyResearchServiceLaunchGateStartupError(
            "SCHEMA_INVALID", "launch gate audit report fields are invalid"
        )
    _self_hash(report, label="launch gate audit report")
    if report["audit_version"] != DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION:
        raise DailyResearchServiceLaunchGateStartupError(
            "VERSION_MISMATCH", "launch gate audit version is invalid"
        )
    if report["decision_ready"] is not False:
        raise DailyResearchServiceLaunchGateStartupError(
            "DECISION_GATE_INVALID", "launch gate audit decision_ready must be false"
        )
    if report["status"] not in _AUDIT_STATUSES:
        raise DailyResearchServiceLaunchGateStartupError(
            "STATE_INVALID", "launch gate audit status is invalid"
        )
    for field in ("gate_ready", "audit_ready"):
        if not isinstance(report[field], bool):
            raise DailyResearchServiceLaunchGateStartupError(
                "FIELD_INVALID", f"launch gate audit {field} is invalid"
            )
    if report["gate_path"] != gate_relative or report["gate_sha256"] != gate_sha:
        raise DailyResearchServiceLaunchGateStartupError(
            "CHAIN_MISMATCH", "launch gate audit gate reference differs"
        )
    if (
        report["gate_report_path"] != gate_report_relative
        or report["gate_report_sha256"] != gate_report_sha
    ):
        raise DailyResearchServiceLaunchGateStartupError(
            "CHAIN_MISMATCH", "launch gate audit report reference differs"
        )
    for field in ("symbol", "as_of", "evaluation_at", "status", "gate_ready"):
        if report[field] != gate_config.report[field]:
            raise DailyResearchServiceLaunchGateStartupError(
                "CHAIN_MISMATCH", f"launch gate audit {field} differs"
            )
    launch_ready = (
        report["audit_ready"] is True
        and report["gate_ready"] is True
        and report["status"] == "ready"
        and gate_config.gate_ready is True
        and gate_config.status == "ready"
    )
    return audit_file, report, launch_ready


def load_daily_research_service_launch_gate_startup(
    *, gate_path: Path, audit_path: Path, artifact_root: Path
) -> DailyResearchServiceLaunchGateStartupConfig:
    """Validate the gate and its independent audit before any socket is opened."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceLaunchGateStartupError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        gate_config = load_daily_research_service_launch_gate(
            gate_path=gate_path, artifact_root=root, require_ready=False
        )
    except Exception as exc:  # noqa: BLE001 - normalize upstream validation errors
        code = getattr(exc, "code", "INPUT_INVALID")
        raise DailyResearchServiceLaunchGateStartupError(
            code, "daily launch gate is invalid"
        ) from exc
    audit_file, audit_report, launch_ready = _validate_audit_report(
        audit_path=audit_path,
        root=root,
        gate_config=gate_config,
        gate_path=gate_path,
    )
    return DailyResearchServiceLaunchGateStartupConfig(
        gate_config=gate_config,
        audit_path=audit_file,
        audit_report=audit_report,
        launch_ready=launch_ready,
    )


def daily_research_service_launch_gate_startup_check_report(
    config: DailyResearchServiceLaunchGateStartupConfig,
) -> dict[str, Any]:
    """Return a deterministic, non-sensitive check-only summary."""

    gate = config.gate_config
    return {
        "validation": "passed",
        "gate_path": config.audit_report["gate_path"],
        "gate_sha256": config.audit_report["gate_sha256"],
        "audit_path": config.audit_path.relative_to(gate.artifact_root).as_posix(),
        "audit_sha256": sha256_bytes(config.audit_path.read_bytes()),
        "symbol": config.audit_report["symbol"],
        "as_of": config.audit_report["as_of"],
        "evaluation_at": config.audit_report["evaluation_at"],
        "status": config.audit_report["status"],
        "gate_ready": config.audit_report["gate_ready"],
        "audit_ready": config.audit_report["audit_ready"],
        "launch_ready": config.launch_ready,
        "decision_ready": False,
    }


__all__ = [
    "DailyResearchServiceLaunchGateStartupConfig",
    "DailyResearchServiceLaunchGateStartupError",
    "daily_research_service_launch_gate_startup_check_report",
    "load_daily_research_service_launch_gate_startup",
]
