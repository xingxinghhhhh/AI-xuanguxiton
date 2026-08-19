"""Build a read-only release admission from Node66 and Node67 artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_run import (
    DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_VERSION,
)
from .daily_research_service_run_audit import (
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
)

DAILY_RESEARCH_SERVICE_RELEASE_VERSION = "daily-research-service-release-v1"
DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME = "daily_research_service_release_manifest.json"
DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME = "daily_research_service_release_report.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_FIELDS = {
    "run_version", "gate_path", "gate_sha256", "audit_path", "audit_sha256", "symbol",
    "as_of", "evaluation_at", "startup_status", "probe_status", "probe_exit_code",
    "service_stopped", "run_ready", "issues", "decision_ready", "output_sha256",
}
_AUDIT_FIELDS = {
    "audit_version", "run_report_path", "run_report_sha256", "gate_path", "gate_sha256",
    "audit_path", "audit_sha256", "symbol", "as_of", "evaluation_at", "run_status",
    "startup_status", "probe_status", "probe_exit_code", "service_stopped", "run_ready",
    "audit_ready", "issues", "decision_ready", "output_sha256",
}
_MANIFEST_FIELDS = {
    "release_version", "run_report_path", "run_report_sha256", "run_audit_report_path",
    "run_audit_report_sha256", "symbol", "as_of", "evaluation_at", "run_status",
    "run_ready", "service_stopped", "audit_status", "audit_ready", "status",
    "release_ready", "issues", "decision_ready", "output_sha256",
}
_REPORT_FIELDS = {
    "release_version", "manifest_path", "manifest_sha256", "run_report_path",
    "run_report_sha256", "run_audit_report_path", "run_audit_report_sha256", "symbol",
    "as_of", "evaluation_at", "run_status", "run_ready", "service_stopped", "audit_status",
    "audit_ready", "status", "release_ready", "issues", "decision_ready", "output_sha256",
}


class DailyResearchServiceReleaseError(ValueError):
    """A sanitized release-admission configuration or artifact error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": str(message)}


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", f"{label} is invalid")
    return value


def _relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise DailyResearchServiceReleaseError("PATH_OUTSIDE_ROOT", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise DailyResearchServiceReleaseError("PATH_OUTSIDE_ROOT", f"{label} is not normalized")
    return normalized


def _safe_file(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseError(
            "PATH_OUTSIDE_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise DailyResearchServiceReleaseError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise DailyResearchServiceReleaseError(
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
        raise DailyResearchServiceReleaseError("JSON_INVALID", f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DailyResearchServiceReleaseError("JSON_INVALID", f"{label} must be an object")
    return value


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = payload.get("output_sha256")
    if not isinstance(declared, str) or _SHA256_RE.fullmatch(declared) is None:
        raise DailyResearchServiceReleaseError(
            "SELF_HASH_MISMATCH", f"{label} self-hash is invalid"
        )
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise DailyResearchServiceReleaseError("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _time(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DailyResearchServiceReleaseError("TIME_MISMATCH", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DailyResearchServiceReleaseError("TIME_MISMATCH", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DailyResearchServiceReleaseError("TIME_MISMATCH", f"{label} needs timezone")
    return parsed


def _issues(value: Any, *, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", f"{label} is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "message"}
            or not isinstance(item["code"], str)
            or not isinstance(item["message"], str)
        ):
            raise DailyResearchServiceReleaseError("FIELD_MISMATCH", f"{label} is invalid")
        result.append(_issue(item["code"], item["message"]))
    return result


def _validate_run(run: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(run) != _RUN_FIELDS:
        raise DailyResearchServiceReleaseError("UNKNOWN_FIELD", "run report fields are invalid")
    if run["run_version"] != DAILY_RESEARCH_SERVICE_RUN_VERSION:
        raise DailyResearchServiceReleaseError("VERSION_MISMATCH", "run report version is invalid")
    _self_hash(run, label="run report")
    if run["decision_ready"] is not False:
        raise DailyResearchServiceReleaseError("UPSTREAM_INVALID", "run decision gate is invalid")
    if not isinstance(run["symbol"], str) or not run["symbol"].strip():
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "run symbol is invalid")
    _time(run["as_of"], label="run as_of")
    _time(run["evaluation_at"], label="run evaluation_at")
    if not isinstance(run["startup_status"], str) or run["startup_status"] not in {
        "ready", "blocked", "invalid"
    }:
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "run startup status is invalid")
    if run["probe_status"] is not None and (
        not isinstance(run["probe_status"], str)
        or run["probe_status"] not in {"ready", "invalid"}
    ):
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "run probe status is invalid")
    if not isinstance(run["service_stopped"], bool) or not isinstance(run["run_ready"], bool):
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "run readiness fields are invalid")
    return _issues(run["issues"], label="run issues")


def _validate_audit(audit: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(audit) != _AUDIT_FIELDS:
        raise DailyResearchServiceReleaseError("UNKNOWN_FIELD", "run audit fields are invalid")
    if audit["audit_version"] != DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION:
        raise DailyResearchServiceReleaseError("VERSION_MISMATCH", "run audit version is invalid")
    _self_hash(audit, label="run audit")
    if audit["decision_ready"] is not False:
        raise DailyResearchServiceReleaseError("UPSTREAM_INVALID", "audit decision gate is invalid")
    if not isinstance(audit["symbol"], str) or not audit["symbol"].strip():
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "audit symbol is invalid")
    _time(audit["as_of"], label="audit as_of")
    _time(audit["evaluation_at"], label="audit evaluation_at")
    if not isinstance(audit["run_status"], str) or audit["run_status"] not in {
        "ready", "failed", "blocked", "invalid"
    }:
        raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "audit run status is invalid")
    for field in ("run_ready", "service_stopped", "audit_ready"):
        if not isinstance(audit[field], bool):
            raise DailyResearchServiceReleaseError("FIELD_MISMATCH", f"audit {field} is invalid")
    return _issues(audit["issues"], label="audit issues")


def _base_manifest() -> dict[str, Any]:
    return {
        "release_version": DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
        "run_report_path": None,
        "run_report_sha256": None,
        "run_audit_report_path": None,
        "run_audit_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "run_status": "invalid",
        "run_ready": False,
        "service_stopped": False,
        "audit_status": "invalid",
        "audit_ready": False,
        "status": "invalid",
        "release_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _base_report() -> dict[str, Any]:
    return {
        "release_version": DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
        "manifest_path": None,
        "manifest_sha256": None,
        "run_report_path": None,
        "run_report_sha256": None,
        "run_audit_report_path": None,
        "run_audit_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "run_status": "invalid",
        "run_ready": False,
        "service_stopped": False,
        "audit_status": "invalid",
        "audit_ready": False,
        "status": "invalid",
        "release_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_hashed(payload: dict[str, Any], path: Path) -> bytes:
    canonical = dict(payload)
    canonical["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    raw = _json_bytes(payload)
    write_atomic(path, raw)
    return raw


def build_daily_research_service_release(
    *,
    run_report_path: Path,
    run_audit_report_path: Path,
    artifact_root: Path,
    output_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Build a deterministic release manifest and bound report without side effects."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceReleaseError("ARTIFACT_ROOT_INVALID", "artifact root is invalid")
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseError(
            "OUTPUT_DIR_INVALID", "output directory escapes artifact root"
        ) from exc

    manifest = _base_manifest()
    report = _base_report()
    try:
        run_file = _safe_file(run_report_path, root=root, label="run report")
        audit_file = _safe_file(run_audit_report_path, root=root, label="run audit report")
        if run_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME:
            raise DailyResearchServiceReleaseError(
                "FIELD_MISMATCH", "run report filename is invalid"
            )
        if audit_file["path"].name != DAILY_RESEARCH_SERVICE_RUN_AUDIT_REPORT_NAME:
            raise DailyResearchServiceReleaseError(
                "FIELD_MISMATCH", "run audit filename is invalid"
            )
        run = _read_json(run_file["raw"], label="run report")
        audit = _read_json(audit_file["raw"], label="run audit report")
        run_issues = _validate_run(run)
        audit_issues = _validate_audit(audit)
        run_path = run_file["relative_path"]
        audit_path = audit_file["relative_path"]
        audit_run_path = _relative_path(audit["run_report_path"], label="audit run_report_path")
        audit_gate_path = _relative_path(audit["gate_path"], label="audit gate_path")
        audit_chain_path = _relative_path(audit["audit_path"], label="audit audit_path")
        run_gate_path = _relative_path(run["gate_path"], label="run gate_path")
        run_audit_path = _relative_path(run["audit_path"], label="run audit_path")
        gate_file = _safe_file(root / Path(run_gate_path), root=root, label="daily launch gate")
        chain_file = _safe_file(
            root / Path(run_audit_path), root=root, label="launch gate audit report"
        )
        if gate_file["path"].name != "daily_research_service_launch_gate.json":
            raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "gate filename is invalid")
        if chain_file["path"].name != "daily_research_service_launch_gate_audit_report.json":
            raise DailyResearchServiceReleaseError(
                "FIELD_MISMATCH", "launch gate audit filename is invalid"
            )
        if audit_run_path != run_path or audit["run_report_sha256"] != run_file["sha256"]:
            raise DailyResearchServiceReleaseError("HASH_MISMATCH", "audit run reference differs")
        if audit_gate_path != run_gate_path or audit_chain_path != run_audit_path:
            raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "audit path reference differs")
        if (
            _sha(run["gate_sha256"], label="run gate_sha256") != gate_file["sha256"]
            or _sha(audit["gate_sha256"], label="audit gate_sha256") != gate_file["sha256"]
            or _sha(run["audit_sha256"], label="run audit_sha256") != chain_file["sha256"]
            or _sha(audit["audit_sha256"], label="audit audit_sha256") != chain_file["sha256"]
        ):
            raise DailyResearchServiceReleaseError("HASH_MISMATCH", "gate reference differs")
        for field in ("symbol", "as_of", "evaluation_at"):
            if run[field] != audit[field]:
                raise DailyResearchServiceReleaseError("FIELD_MISMATCH", f"{field} differs")
        if _time(run["as_of"], label="as_of") > _time(run["evaluation_at"], label="evaluation_at"):
            raise DailyResearchServiceReleaseError("TIME_MISMATCH", "as_of is after evaluation_at")
        if audit["audit_ready"] is not True:
            raise DailyResearchServiceReleaseError(
                "UPSTREAM_INVALID", "run audit is not audit_ready"
            )
        expected_run_status = (
            "blocked"
            if run["startup_status"] == "blocked"
            else "ready"
            if run["run_ready"]
            else "failed"
        )
        if audit["run_status"] != expected_run_status or audit["run_ready"] != run["run_ready"]:
            raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "run readiness differs")
        if audit["service_stopped"] != run["service_stopped"]:
            raise DailyResearchServiceReleaseError("FIELD_MISMATCH", "service stop state differs")
        for field in ("startup_status", "probe_status", "probe_exit_code"):
            if audit[field] != run[field]:
                raise DailyResearchServiceReleaseError("FIELD_MISMATCH", f"{field} differs")
        manifest.update(
            {
                "run_report_path": run_path,
                "run_report_sha256": run_file["sha256"],
                "run_audit_report_path": audit_path,
                "run_audit_report_sha256": audit_file["sha256"],
                "symbol": run["symbol"],
                "as_of": run["as_of"],
                "evaluation_at": run["evaluation_at"],
                "run_status": audit["run_status"],
                "run_ready": audit["run_ready"],
                "service_stopped": audit["service_stopped"],
                "audit_status": "ready",
                "audit_ready": True,
            }
        )
        release_ready = (
            audit["run_status"] == "ready"
            and audit["run_ready"] is True
            and audit["service_stopped"] is True
            and not run_issues
            and not audit_issues
        )
        manifest["status"] = "ready" if release_ready else "blocked"
        manifest["release_ready"] = release_ready
        manifest["issues"] = [] if release_ready else (audit_issues or run_issues)
    except DailyResearchServiceReleaseError as exc:
        manifest["issues"] = [_issue(exc.code, str(exc))]

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME
    manifest_raw = _write_hashed(manifest, manifest_path)
    report.update(
        {
            "manifest_path": manifest_path.resolve().relative_to(root).as_posix(),
            "manifest_sha256": sha256_bytes(manifest_raw),
            "run_report_path": manifest["run_report_path"],
            "run_report_sha256": manifest["run_report_sha256"],
            "run_audit_report_path": manifest["run_audit_report_path"],
            "run_audit_report_sha256": manifest["run_audit_report_sha256"],
            "symbol": manifest["symbol"],
            "as_of": manifest["as_of"],
            "evaluation_at": manifest["evaluation_at"],
            "run_status": manifest["run_status"],
            "run_ready": manifest["run_ready"],
            "service_stopped": manifest["service_stopped"],
            "audit_status": manifest["audit_status"],
            "audit_ready": manifest["audit_ready"],
            "status": manifest["status"],
            "release_ready": manifest["release_ready"],
            "issues": manifest["issues"],
        }
    )
    report_path = output_dir / DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME
    _write_hashed(report, report_path)
    return manifest, report, 0 if manifest["release_ready"] else 1


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_VERSION",
    "DAILY_RESEARCH_SERVICE_RELEASE_MANIFEST_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_REPORT_NAME",
    "DailyResearchServiceReleaseError",
    "build_daily_research_service_release",
]
