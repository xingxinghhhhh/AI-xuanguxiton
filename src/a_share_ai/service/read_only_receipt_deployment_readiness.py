"""Check a read-only receipt deployment specification without starting a service."""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_release_run_admission_startup_gate_smoke import (
    _REPORT_FIELDS as _SMOKE_REPORT_FIELDS,
)
from .daily_research_service_release_run_admission_startup_gate_smoke import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION,
)
from .daily_research_service_release_run_admission_startup_gate_smoke_audit import (
    _AUDIT_FIELDS,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_VERSION,
)
from .read_only_receipt_server import READ_ONLY_RECEIPT_SERVICE_VERSION

READ_ONLY_RECEIPT_DEPLOYMENT_VERSION = "read-only-receipt-deployment-v1"
READ_ONLY_RECEIPT_DEPLOYMENT_MODE = "node80-gated-loopback-v1"
READ_ONLY_RECEIPT_DEPLOYMENT_REPORT_NAME = (
    "read_only_receipt_service_deployment_readiness_report.json"
)
READ_ONLY_RECEIPT_DEPLOYMENT_ENTRYPOINT = "python -m a_share_ai.cli serve-research-receipt"
READ_ONLY_RECEIPT_DEPLOYMENT_HOST_PORT_SOURCE = "deployment-spec-only"
READ_ONLY_RECEIPT_DEPLOYMENT_PORT_CHECK_SCOPE = "loopback-bind-close-at-check-time"
READ_ONLY_RECEIPT_DEPLOYMENT_ROLLBACK_POLICY = "manual-previous-validated-spec-v1"
_SPEC_FIELDS = {
    "deployment_version",
    "mode",
    "smoke_report_path",
    "smoke_report_sha256",
    "smoke_audit_report_path",
    "smoke_audit_report_sha256",
    "configured_host",
    "configured_port",
    "decision_ready",
}
_REPORT_FIELDS = {
    "deployment_version",
    "mode",
    "spec_path",
    "spec_sha256",
    "smoke_report_path",
    "smoke_report_sha256",
    "smoke_audit_report_path",
    "smoke_audit_report_sha256",
    "service_version",
    "entrypoint",
    "configured_host",
    "configured_port",
    "host_port_source",
    "smoke_status",
    "smoke_ready",
    "audit_status",
    "audit_ready",
    "port_available_at_check",
    "port_check_scope",
    "deployment_status",
    "deployment_ready",
    "rollback_policy",
    "issues",
    "decision_ready",
    "output_sha256",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|(?:^|\s)/)")
_STATUS_VALUES = {"ready", "blocked", "failed", "invalid"}


class ReadOnlyReceiptDeploymentReadinessError(ValueError):
    """A sanitized deployment readiness configuration failure."""

    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


class _ReadinessFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _ReadinessFailure("HASH_INVALID", f"{label} is invalid")
    return value


def _relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _ReadinessFailure("PATH_INVALID", f"{label} is invalid")
    pure = PureWindowsPath(value)
    if pure.is_absolute() or pure.drive or any(part in {"", ".", ".."} for part in pure.parts):
        raise _ReadinessFailure("PATH_INVALID", f"{label} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != "/".join(pure.parts):
        raise _ReadinessFailure("PATH_INVALID", f"{label} is not normalized")
    return normalized


def _rooted_file(
    value: Any, *, root: Path, label: str, expected_name: str | None = None
) -> tuple[str, Path, str]:
    try:
        if isinstance(value, Path):
            candidate = value.resolve()
            relative = candidate.relative_to(root).as_posix()
        else:
            relative = _relative(value, label=label)
            candidate = (root / PureWindowsPath(relative)).resolve()
        actual_relative = candidate.relative_to(root).as_posix()
        raw = candidate.read_bytes()
    except (OSError, ValueError) as exc:
        raise _ReadinessFailure("INPUT_UNAVAILABLE", f"{label} is unavailable") from exc
    if actual_relative != relative or not candidate.is_file():
        raise _ReadinessFailure("PATH_INVALID", f"{label} is outside artifact root")
    if expected_name is not None and candidate.name != expected_name:
        raise _ReadinessFailure("FILENAME_INVALID", f"{label} filename is invalid")
    return relative, candidate, raw


def _self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = _sha(payload.get("output_sha256"), label=f"{label}.output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise _ReadinessFailure("SELF_HASH_MISMATCH", f"{label} self-hash differs")


def _read_json(
    path: Path,
    raw: bytes,
    *,
    label: str,
    expected_fields: set[str],
    self_hashed: bool = True,
) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        if _ABSOLUTE_PATH_RE.search(text):
            raise _ReadinessFailure("SENSITIVE_OUTPUT", f"{label} contains an absolute path")
        payload = json.loads(text)
    except _ReadinessFailure:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _ReadinessFailure("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise _ReadinessFailure("SCHEMA_INVALID", f"{label} fields are invalid")
    if self_hashed:
        _self_hash(payload, label=label)
    return payload


def _validate_spec(payload: Mapping[str, Any]) -> None:
    if set(payload) != _SPEC_FIELDS:
        raise _ReadinessFailure("SCHEMA_INVALID", "deployment spec fields are invalid")
    if payload["deployment_version"] != READ_ONLY_RECEIPT_DEPLOYMENT_VERSION:
        raise _ReadinessFailure("VERSION_MISMATCH", "deployment version is invalid")
    if payload["mode"] != READ_ONLY_RECEIPT_DEPLOYMENT_MODE:
        raise _ReadinessFailure("MODE_INVALID", "deployment mode is invalid")
    if payload["decision_ready"] is not False:
        raise _ReadinessFailure("DECISION_GATE_INVALID", "decision_ready must be false")
    if not isinstance(payload["configured_host"], str) or payload["configured_host"] not in {
        "127.0.0.1",
        "::1",
    }:
        raise _ReadinessFailure("HOST_INVALID", "configured host must be loopback")
    port = payload["configured_port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise _ReadinessFailure("PORT_INVALID", "configured port is invalid")
    _sha(payload["smoke_report_sha256"], label="smoke report SHA-256")
    _sha(payload["smoke_audit_report_sha256"], label="smoke audit report SHA-256")


def _validate_upstream(
    smoke: Mapping[str, Any], audit: Mapping[str, Any], *, smoke_relative: str, smoke_sha: str
) -> tuple[str, bool, str, bool]:
    if (
        smoke["smoke_version"]
        != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION
    ):
        raise _ReadinessFailure("VERSION_MISMATCH", "smoke version is invalid")
    if (
        audit["audit_version"]
        != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_VERSION
    ):
        raise _ReadinessFailure("VERSION_MISMATCH", "smoke audit version is invalid")
    if audit["smoke_version"] != smoke["smoke_version"]:
        raise _ReadinessFailure("VERSION_MISMATCH", "smoke versions are inconsistent")
    if audit["smoke_report_path"] != smoke_relative or audit["smoke_report_sha256"] != smoke_sha:
        raise _ReadinessFailure("BINDING_MISMATCH", "smoke and audit bindings differ")
    if smoke["decision_ready"] is not False or audit["decision_ready"] is not False:
        raise _ReadinessFailure("DECISION_GATE_INVALID", "upstream decision_ready must be false")
    if not isinstance(smoke["smoke_status"], str) or smoke["smoke_status"] not in _STATUS_VALUES:
        raise _ReadinessFailure("STATE_INVALID", "smoke status is invalid")
    if not isinstance(smoke["smoke_ready"], bool) or not isinstance(audit["audit_ready"], bool):
        raise _ReadinessFailure("FIELD_INVALID", "upstream readiness fields are invalid")
    if not isinstance(audit["status"], str) or audit["status"] not in _STATUS_VALUES:
        raise _ReadinessFailure("STATE_INVALID", "audit status is invalid")
    if audit["status"] != ("ready" if smoke["smoke_status"] == "ready" else smoke["smoke_status"]):
        raise _ReadinessFailure("STATE_INVALID", "upstream statuses are inconsistent")
    if audit["audit_ready"] is not True:
        raise _ReadinessFailure("AUDIT_NOT_READY", "smoke audit is not ready")
    if smoke["smoke_ready"] != (smoke["smoke_status"] == "ready"):
        raise _ReadinessFailure("STATE_INVALID", "smoke readiness is inconsistent")
    return smoke["smoke_status"], smoke["smoke_ready"], audit["status"], audit["audit_ready"]


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    address = (host, port, 0, 0) if family == socket.AF_INET6 else (host, port)
    with socket.socket(family, socket.SOCK_STREAM) as probe_socket:
        try:
            probe_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe_socket.bind(address)
        except OSError:
            return False
    return True


def _base_report() -> dict[str, Any]:
    return {
        "deployment_version": READ_ONLY_RECEIPT_DEPLOYMENT_VERSION,
        "mode": READ_ONLY_RECEIPT_DEPLOYMENT_MODE,
        "spec_path": None,
        "spec_sha256": None,
        "smoke_report_path": None,
        "smoke_report_sha256": None,
        "smoke_audit_report_path": None,
        "smoke_audit_report_sha256": None,
        "service_version": READ_ONLY_RECEIPT_SERVICE_VERSION,
        "entrypoint": READ_ONLY_RECEIPT_DEPLOYMENT_ENTRYPOINT,
        "configured_host": None,
        "configured_port": None,
        "host_port_source": READ_ONLY_RECEIPT_DEPLOYMENT_HOST_PORT_SOURCE,
        "smoke_status": "invalid",
        "smoke_ready": False,
        "audit_status": "invalid",
        "audit_ready": False,
        "port_available_at_check": False,
        "port_check_scope": READ_ONLY_RECEIPT_DEPLOYMENT_PORT_CHECK_SCOPE,
        "deployment_status": "invalid",
        "deployment_ready": False,
        "rollback_policy": READ_ONLY_RECEIPT_DEPLOYMENT_ROLLBACK_POLICY,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> None:
    canonical = dict(report)
    canonical["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(output_dir / READ_ONLY_RECEIPT_DEPLOYMENT_REPORT_NAME, _json_bytes(report))
    except OSError as exc:
        raise ReadOnlyReceiptDeploymentReadinessError(
            "OUTPUT_UNAVAILABLE",
            "deployment readiness report output is unavailable",
            configuration=True,
        ) from exc


def check_read_only_receipt_deployment(
    *, spec_path: Path, artifact_root: Path, output_dir: Path
) -> tuple[dict[str, Any], int]:
    """Validate a deployment spec and upstream receipts without starting a service."""

    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise ReadOnlyReceiptDeploymentReadinessError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise ReadOnlyReceiptDeploymentReadinessError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    try:
        output = output_dir.resolve()
        output.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ReadOnlyReceiptDeploymentReadinessError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir escapes artifact root", configuration=True
        ) from exc
    if output.exists() and not output.is_dir():
        raise ReadOnlyReceiptDeploymentReadinessError(
            "OUTPUT_DIR_INVALID", "output-dir is invalid", configuration=True
        )

    result = _base_report()
    try:
        spec_relative, spec_file, spec_raw = _rooted_file(
            spec_path, root=root, label="deployment spec"
        )
        spec_sha = sha256_bytes(spec_raw)
        spec = _read_json(
            spec_file,
            spec_raw,
            label="deployment spec",
            expected_fields=_SPEC_FIELDS,
            self_hashed=False,
        )
        _validate_spec(spec)
        result.update(
            {
                "spec_path": spec_relative,
                "spec_sha256": spec_sha,
                "configured_host": spec["configured_host"],
                "configured_port": spec["configured_port"],
                "smoke_report_path": _relative(
                    spec["smoke_report_path"], label="smoke report path"
                ),
                "smoke_report_sha256": spec["smoke_report_sha256"],
                "smoke_audit_report_path": _relative(
                    spec["smoke_audit_report_path"], label="smoke audit report path"
                ),
                "smoke_audit_report_sha256": spec["smoke_audit_report_sha256"],
            }
        )
        smoke_relative, _, smoke_raw = _rooted_file(
            spec["smoke_report_path"],
            root=root,
            label="smoke report",
            expected_name=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
        )
        audit_relative, _, audit_raw = _rooted_file(
            spec["smoke_audit_report_path"],
            root=root,
            label="smoke audit report",
            expected_name=DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_REPORT_NAME,
        )
        if (
            smoke_relative != result["smoke_report_path"]
            or sha256_bytes(smoke_raw) != spec["smoke_report_sha256"]
        ):
            raise _ReadinessFailure("HASH_MISMATCH", "smoke report binding differs")
        if (
            audit_relative != result["smoke_audit_report_path"]
            or sha256_bytes(audit_raw) != spec["smoke_audit_report_sha256"]
        ):
            raise _ReadinessFailure("HASH_MISMATCH", "smoke audit report binding differs")
        smoke = _read_json(
            smoke_file := (root / PureWindowsPath(smoke_relative)),
            smoke_raw,
            label="smoke report",
            expected_fields=_SMOKE_REPORT_FIELDS,
        )
        audit = _read_json(
            audit_file := (root / PureWindowsPath(audit_relative)),
            audit_raw,
            label="smoke audit report",
            expected_fields=_AUDIT_FIELDS,
        )
        del smoke_file, audit_file
        smoke_status, smoke_ready, audit_status, audit_ready = _validate_upstream(
            smoke, audit, smoke_relative=smoke_relative, smoke_sha=sha256_bytes(smoke_raw)
        )
        result.update(
            {
                "smoke_status": smoke_status,
                "smoke_ready": smoke_ready,
                "audit_status": audit_status,
                "audit_ready": audit_ready,
            }
        )
        result["port_available_at_check"] = _port_available(
            spec["configured_host"], spec["configured_port"]
        )
        if (
            smoke_status == "ready"
            and audit_status == "ready"
            and result["port_available_at_check"]
        ):
            result["deployment_status"] = "ready"
            result["deployment_ready"] = True
        else:
            result["deployment_status"] = "blocked"
            result["issues"] = [
                _issue("DEPLOYMENT_NOT_READY", "upstream evidence or port is not ready")
            ]
    except _ReadinessFailure as exc:
        result["deployment_status"] = "invalid"
        result["issues"] = [_issue(exc.code, str(exc))]
    _write_report(result, output_dir=output)
    return result, 0 if result["deployment_ready"] else 1


__all__ = [
    "READ_ONLY_RECEIPT_DEPLOYMENT_ENTRYPOINT",
    "READ_ONLY_RECEIPT_DEPLOYMENT_MODE",
    "READ_ONLY_RECEIPT_DEPLOYMENT_REPORT_NAME",
    "READ_ONLY_RECEIPT_DEPLOYMENT_VERSION",
    "ReadOnlyReceiptDeploymentReadinessError",
    "check_read_only_receipt_deployment",
]
