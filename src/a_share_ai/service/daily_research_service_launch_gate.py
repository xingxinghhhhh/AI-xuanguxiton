"""Build and validate a versioned gate for daily read-only service startup."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from ..runtime.daily_research_handoff import DAILY_RESEARCH_HANDOFF_VERSION
from ..runtime.daily_research_handoff_audit import DAILY_RESEARCH_HANDOFF_AUDIT_VERSION
from .launch_config import (
    ReadOnlyReceiptLaunchConfig,
    ReadOnlyReceiptLaunchError,
    load_read_only_receipt_launch_config,
)
from .read_only_receipt_server import (
    ReadOnlyReceiptServiceError,
    load_daily_research_admission_summary,
)

DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION = "daily-research-service-launch-gate-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HANDOFF_STATUSES = {"ready", "blocked"}
_AUDIT_STATUSES = {"ready", "blocked", "invalid"}
_FIELDS = {
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
_AUDIT_FIELDS = {
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


class DailyResearchServiceLaunchGateError(ValueError):
    """A sanitized gate manifest or input validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DailyResearchServiceLaunchGateConfig:
    artifact_root: Path
    receipt_path: Path
    receipt_report_path: Path
    host: str
    port: int
    daily_admission_path: Path
    daily_admission_report_path: Path
    gate_ready: bool
    status: str
    report: dict[str, Any]


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _safe_message(exc: Any) -> str:
    return re.sub(r"(?i)(?:\b[A-Z]:[\\/][^\s\"']*|/[\w./\\-]+)", "<redacted-path>", str(exc))


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceLaunchGateError("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceLaunchGateError("INPUT_INVALID", f"{label} must be an object")
    return value


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceLaunchGateError("HASH_INVALID", f"{label} is invalid")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceLaunchGateError("HASH_MISMATCH", f"{label} self-hash differs")


def _safe_relative(value: Any, *, root: Path, label: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceLaunchGateError("PATH_INVALID", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceLaunchGateError("PATH_INVALID", f"{label} is invalid")
    relative = value.replace("\\", "/")
    if relative != "/".join(pure.parts):
        raise DailyResearchServiceLaunchGateError("PATH_INVALID", f"{label} is not normalized")
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceLaunchGateError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceLaunchGateError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    return candidate, relative


def _file_info(path: Path, *, root: Path, label: str) -> tuple[Path, str, str]:
    try:
        declared = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceLaunchGateError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc
    candidate, relative = _safe_relative(declared, root=root, label=label)
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceLaunchGateError(
            "INPUT_UNAVAILABLE", f"{label} is unavailable"
        ) from exc
    return candidate, relative, sha256_bytes(raw)


def _resolve_declared(
    value: Any, expected_sha: Any, *, root: Path, label: str
) -> tuple[Path, str, str]:
    candidate, relative = _safe_relative(value, root=root, label=label)
    actual = sha256_bytes(candidate.read_bytes())
    if _sha(expected_sha, label=f"{label} SHA") != actual:
        raise DailyResearchServiceLaunchGateError("HASH_MISMATCH", f"{label} hash differs")
    return candidate, relative, actual


def _parse_time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceLaunchGateError("TIME_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceLaunchGateError("TIME_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceLaunchGateError("TIME_INVALID", f"{label} must include timezone")
    return parsed


def _validate_pair(
    first: Mapping[str, Any], second: Mapping[str, Any], *, fields: set[str], label: str
) -> None:
    for name, payload in ((label, first), (f"{label} report", second)):
        if set(payload) != fields:
            raise DailyResearchServiceLaunchGateError(
                "SCHEMA_INVALID", f"{name} fields are invalid"
            )
        _self_hash(payload, label=name)
    if dict(first) != dict(second):
        raise DailyResearchServiceLaunchGateError("FIELD_MISMATCH", f"{label} pair differs")


def _base_report() -> dict[str, Any]:
    return {field: None for field in _FIELDS} | {
        "gate_version": DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION,
        "status": "invalid",
        "receipt_ready": False,
        "daily_admission_ready": False,
        "audit_ready": False,
        "handoff_ready": False,
        "gate_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_pair(report: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = _json_bytes({**report, "output_sha256": None})
        report["output_sha256"] = sha256_bytes(raw)
        raw = _json_bytes(report)
        write_atomic(output_dir / "daily_research_service_launch_gate.json", raw)
        write_atomic(output_dir / "daily_research_service_launch_gate_report.json", raw)
    except OSError as exc:
        raise DailyResearchServiceLaunchGateError(
            "OUTPUT_UNAVAILABLE", "gate output is unavailable"
        ) from exc
    return report


def _validate_inputs(
    *,
    handoff_path: Path,
    handoff_report_path: Path,
    audit_path: Path,
    launch_manifest_path: Path,
    root: Path,
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], ReadOnlyReceiptLaunchConfig, dict[str, Any]
]:
    launch = load_read_only_receipt_launch_config(
        manifest_path=launch_manifest_path, artifact_root=root
    )
    launch_file, launch_relative, launch_sha = _file_info(
        launch_manifest_path, root=root, label="launch manifest"
    )
    _ = launch_file
    handoff_file, handoff_relative, handoff_sha = _file_info(
        handoff_path, root=root, label="handoff"
    )
    handoff_report_file, handoff_report_relative, handoff_report_sha = _file_info(
        handoff_report_path, root=root, label="handoff report"
    )
    audit_file, audit_relative, audit_sha = _file_info(
        audit_path, root=root, label="handoff audit report"
    )
    handoff = _read_json(handoff_file, label="handoff")
    handoff_report = _read_json(handoff_report_file, label="handoff report")
    audit = _read_json(audit_file, label="handoff audit report")
    _validate_pair(handoff, handoff_report, fields=_HANDOFF_FIELDS, label="handoff")
    if handoff.get("handoff_version") != DAILY_RESEARCH_HANDOFF_VERSION:
        raise DailyResearchServiceLaunchGateError("VERSION_MISMATCH", "handoff version is invalid")
    if handoff.get("status") not in _HANDOFF_STATUSES or handoff.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateError("STATE_INVALID", "handoff state is invalid")
    for field in ("audit_ready", "admission_ready", "handoff_ready"):
        if not isinstance(handoff.get(field), bool):
            raise DailyResearchServiceLaunchGateError(
                "FIELD_INVALID", f"handoff {field} is invalid"
            )
    if handoff.get("run_status") not in {"ready", "blocked"}:
        raise DailyResearchServiceLaunchGateError("STATE_INVALID", "handoff run status is invalid")
    if handoff.get("admission_status") not in {"ready", "stale", "calendar_unknown", "blocked"}:
        raise DailyResearchServiceLaunchGateError(
            "STATE_INVALID", "handoff admission status is invalid"
        )
    _validate_pair(audit, audit, fields=_AUDIT_FIELDS, label="handoff audit")
    if audit.get("audit_version") != DAILY_RESEARCH_HANDOFF_AUDIT_VERSION:
        raise DailyResearchServiceLaunchGateError(
            "VERSION_MISMATCH", "handoff audit version is invalid"
        )
    if audit.get("status") not in _AUDIT_STATUSES or audit.get("decision_ready") is not False:
        raise DailyResearchServiceLaunchGateError("STATE_INVALID", "handoff audit state is invalid")
    if audit.get("status") == "invalid":
        raise DailyResearchServiceLaunchGateError("UPSTREAM_INVALID", "handoff audit is invalid")
    for field in ("audit_ready", "handoff_ready"):
        if not isinstance(audit.get(field), bool):
            raise DailyResearchServiceLaunchGateError("FIELD_INVALID", f"audit {field} is invalid")
    if audit.get("handoff_path") != handoff_relative or audit.get("handoff_sha256") != handoff_sha:
        raise DailyResearchServiceLaunchGateError(
            "CHAIN_MISMATCH", "audit handoff reference differs"
        )
    if (
        audit.get("handoff_report_path") != handoff_report_relative
        or audit.get("handoff_report_sha256") != handoff_report_sha
    ):
        raise DailyResearchServiceLaunchGateError(
            "CHAIN_MISMATCH", "audit handoff report reference differs"
        )
    if audit["handoff_ready"] != handoff["handoff_ready"]:
        raise DailyResearchServiceLaunchGateError(
            "CHAIN_MISMATCH", "audit handoff readiness differs"
        )
    if audit["status"] != handoff["status"]:
        raise DailyResearchServiceLaunchGateError("CHAIN_MISMATCH", "audit status differs")
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
            raise DailyResearchServiceLaunchGateError("CHAIN_MISMATCH", f"audit {field} differs")
    admission_path, admission_relative, admission_sha = _resolve_declared(
        handoff["admission_path"], handoff["admission_sha256"], root=root, label="daily admission"
    )
    admission_report_path, admission_report_relative, admission_report_sha = _resolve_declared(
        handoff["admission_report_path"],
        handoff["admission_report_sha256"],
        root=root,
        label="daily admission report",
    )
    try:
        daily = load_daily_research_admission_summary(
            admission_path=admission_path,
            admission_report_path=admission_report_path,
            artifact_root=root,
        )
    except ReadOnlyReceiptServiceError as exc:
        if (
            handoff["admission_status"] not in {"stale", "calendar_unknown", "blocked"}
            or exc.code != "REPORT_INVALID"
        ):
            raise
        daily = {
            "symbol": handoff["symbol"],
            "as_of": handoff["as_of"],
            "evaluation_at": handoff["evaluation_at"],
            "status": handoff["admission_status"],
            "admission_ready": False,
            "issues": handoff["issues"],
        }
    if (
        daily["symbol"] != handoff["symbol"]
        or daily["as_of"] != handoff["as_of"]
        or daily["evaluation_at"] != handoff["evaluation_at"]
    ):
        raise DailyResearchServiceLaunchGateError(
            "CHAIN_MISMATCH", "daily admission metadata differs"
        )
    if daily["status"] != handoff["admission_status"]:
        raise DailyResearchServiceLaunchGateError(
            "CHAIN_MISMATCH", "daily admission status differs"
        )
    if daily["admission_ready"] != handoff["admission_ready"]:
        raise DailyResearchServiceLaunchGateError(
            "CHAIN_MISMATCH", "daily admission readiness differs"
        )
    expected_handoff_status = (
        "ready"
        if handoff["run_status"] == "ready"
        and handoff["audit_ready"]
        and handoff["admission_status"] == "ready"
        and handoff["admission_ready"]
        else "blocked"
    )
    if handoff["status"] != expected_handoff_status:
        raise DailyResearchServiceLaunchGateError(
            "CHAIN_MISMATCH", "handoff status does not match its readiness fields"
        )
    if (
        audit.get("symbol") != handoff["symbol"]
        or audit.get("as_of") != handoff["as_of"]
        or audit.get("evaluation_at") != handoff["evaluation_at"]
    ):
        raise DailyResearchServiceLaunchGateError("CHAIN_MISMATCH", "audit metadata differs")
    _parse_time(handoff["as_of"], label="handoff.as_of")
    _parse_time(handoff["evaluation_at"], label="handoff.evaluation_at")
    return (
        handoff,
        audit,
        daily,
        launch,
        {
            "launch_manifest_path": launch_relative,
            "launch_manifest_sha256": launch_sha,
            "handoff_path": handoff_relative,
            "handoff_sha256": handoff_sha,
            "handoff_report_path": handoff_report_relative,
            "handoff_report_sha256": handoff_report_sha,
            "handoff_audit_report_path": audit_relative,
            "handoff_audit_report_sha256": audit_sha,
            "daily_admission_path": admission_relative,
            "daily_admission_sha256": admission_sha,
            "daily_admission_report_path": admission_report_relative,
            "daily_admission_report_sha256": admission_report_sha,
        },
    )


def _derive_status(
    *,
    handoff: Mapping[str, Any],
    audit: Mapping[str, Any],
    daily: Mapping[str, Any],
    launch: ReadOnlyReceiptLaunchConfig,
) -> str:
    if daily["status"] == "stale":
        return "stale"
    if (
        launch.receipt_ready
        and daily["status"] == "ready"
        and daily["admission_ready"]
        and audit["audit_ready"]
        and handoff["handoff_ready"]
    ):
        return "ready"
    return "blocked"


def build_daily_research_service_launch_gate(
    *,
    handoff_path: Path,
    handoff_report_path: Path,
    handoff_audit_report_path: Path,
    launch_manifest_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceLaunchGateError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        output_dir.resolve().relative_to(root)
    except ValueError as exc:
        raise DailyResearchServiceLaunchGateError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir escapes artifact root"
        ) from exc
    result = _base_report()
    try:
        handoff, audit, daily, launch, refs = _validate_inputs(
            handoff_path=handoff_path,
            handoff_report_path=handoff_report_path,
            audit_path=handoff_audit_report_path,
            launch_manifest_path=launch_manifest_path,
            root=root,
        )
        result.update(
            refs,
            symbol=handoff["symbol"],
            as_of=handoff["as_of"],
            evaluation_at=handoff["evaluation_at"],
            receipt_ready=launch.receipt_ready,
            daily_admission_status=daily["status"],
            daily_admission_ready=daily["admission_ready"],
            audit_ready=audit["audit_ready"],
            handoff_ready=handoff["handoff_ready"],
        )
        status = _derive_status(handoff=handoff, audit=audit, daily=daily, launch=launch)
        result.update(
            status=status,
            gate_ready=(status == "ready"),
            issues=[] if status == "ready" else daily["issues"],
        )
    except (
        DailyResearchServiceLaunchGateError,
        ReadOnlyReceiptLaunchError,
        ReadOnlyReceiptServiceError,
    ) as exc:
        code = exc.code if hasattr(exc, "code") else "INPUT_INVALID"
        result["issues"] = [{"code": code, "message": _safe_message(exc)}]
    return _write_pair(result, output_dir=output_dir)


def load_daily_research_service_launch_gate(
    *, gate_path: Path, artifact_root: Path, require_ready: bool = False
) -> DailyResearchServiceLaunchGateConfig:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceLaunchGateError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    gate_file, _, gate_sha = _file_info(gate_path, root=root, label="daily launch gate")
    report = _read_json(gate_file, label="daily launch gate")
    if set(report) != _FIELDS:
        raise DailyResearchServiceLaunchGateError(
            "SCHEMA_INVALID", "daily launch gate fields are invalid"
        )
    _self_hash(report, label="daily launch gate")
    if not isinstance(report["issues"], list):
        raise DailyResearchServiceLaunchGateError("FIELD_INVALID", "gate issues are invalid")
    if report["gate_version"] != DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION:
        raise DailyResearchServiceLaunchGateError(
            "VERSION_MISMATCH", "daily launch gate version is invalid"
        )
    if report["decision_ready"] is not False:
        raise DailyResearchServiceLaunchGateError(
            "DECISION_GATE_INVALID", "decision_ready must be false"
        )
    _, _, launch = _resolve_declared(
        report["launch_manifest_path"],
        report["launch_manifest_sha256"],
        root=root,
        label="launch manifest",
    )
    _ = launch
    peer_name = (
        "daily_research_service_launch_gate_report.json"
        if gate_file.name == "daily_research_service_launch_gate.json"
        else "daily_research_service_launch_gate.json"
        if gate_file.name == "daily_research_service_launch_gate_report.json"
        else None
    )
    if peer_name is None:
        raise DailyResearchServiceLaunchGateError(
            "PATH_INVALID", "daily launch gate filename is invalid"
        )
    peer = gate_file.with_name(peer_name)
    try:
        if not peer.is_file() or peer.read_bytes() != gate_file.read_bytes():
            raise DailyResearchServiceLaunchGateError(
                "FIELD_MISMATCH", "daily launch gate pair differs"
            )
    except OSError as exc:
        raise DailyResearchServiceLaunchGateError(
            "INPUT_UNAVAILABLE", "daily launch gate pair is unavailable"
        ) from exc
    handoff, audit, daily, old, refs = _validate_inputs(
        handoff_path=root / report["handoff_path"],
        handoff_report_path=root / report["handoff_report_path"],
        audit_path=root / report["handoff_audit_report_path"],
        launch_manifest_path=root / report["launch_manifest_path"],
        root=root,
    )
    expected = {
        **refs,
        "symbol": handoff["symbol"],
        "as_of": handoff["as_of"],
        "evaluation_at": handoff["evaluation_at"],
        "receipt_ready": old.receipt_ready,
        "daily_admission_status": daily["status"],
        "daily_admission_ready": daily["admission_ready"],
        "audit_ready": audit["audit_ready"],
        "handoff_ready": handoff["handoff_ready"],
        "status": _derive_status(handoff=handoff, audit=audit, daily=daily, launch=old),
    }
    if any(report[key] != value for key, value in expected.items()):
        raise DailyResearchServiceLaunchGateError(
            "HASH_MISMATCH", "daily launch gate references differ"
        )
    if report["status"] not in {"ready", "stale", "blocked", "invalid"}:
        raise DailyResearchServiceLaunchGateError(
            "STATE_INVALID", "daily launch gate status is invalid"
        )
    if not isinstance(report["gate_ready"], bool) or report["gate_ready"] != (
        report["status"] == "ready"
    ):
        raise DailyResearchServiceLaunchGateError(
            "STATE_INVALID", "daily launch gate readiness is invalid"
        )
    if require_ready and not report["gate_ready"]:
        raise DailyResearchServiceLaunchGateError(
            "GATE_NOT_READY", "daily launch gate is not ready"
        )
    return DailyResearchServiceLaunchGateConfig(
        artifact_root=root,
        receipt_path=old.receipt_path,
        receipt_report_path=old.receipt_report_path,
        host=old.host,
        port=old.port,
        daily_admission_path=root / report["daily_admission_path"],
        daily_admission_report_path=root / report["daily_admission_report_path"],
        gate_ready=report["gate_ready"],
        status=report["status"],
        report=report,
    )


def check_daily_research_service_launch_gate(
    *, gate_path: Path, artifact_root: Path
) -> DailyResearchServiceLaunchGateConfig:
    return load_daily_research_service_launch_gate(
        gate_path=gate_path, artifact_root=artifact_root, require_ready=False
    )


def daily_research_service_launch_gate_check_report(
    config: DailyResearchServiceLaunchGateConfig,
) -> dict[str, Any]:
    report = config.report.copy()
    report.pop("output_sha256", None)
    report["validation"] = "passed"
    return report
