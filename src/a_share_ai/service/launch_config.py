"""Validate and run a versioned, rollbackable read-only service launch manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from ..market.replay import sha256_bytes
from .read_only_receipt_probe import READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION
from .read_only_receipt_server import (
    READ_ONLY_RECEIPT_SERVICE_VERSION,
    ReadOnlyReceiptServiceError,
    create_read_only_receipt_server,
    load_read_only_receipt_summary,
)

READ_ONLY_RECEIPT_SERVICE_LAUNCH_VERSION = "read-only-receipt-service-launch-v1"
_ALLOWED_HOSTS = {"127.0.0.1", "::1"}
_MANIFEST_FIELDS = {
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
_HEX_DIGITS = frozenset("0123456789abcdef")


class ReadOnlyReceiptLaunchError(ValueError):
    """A sanitized launch manifest or artifact validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReadOnlyReceiptLaunchConfig:
    """Resolved launch values after all manifest and artifact checks pass."""

    artifact_root: Path
    receipt_path: Path
    receipt_report_path: Path
    receipt_sha256: str
    receipt_report_sha256: str
    host: str
    port: int
    receipt_status: str
    receipt_ready: bool
    decision_ready: bool


def _read_manifest(path: Path, *, artifact_root: Path) -> dict[str, Any]:
    root = artifact_root.resolve()
    if not root.is_dir():
        raise ReadOnlyReceiptLaunchError("ARTIFACT_ROOT_INVALID", "artifact root is invalid")
    try:
        manifest = path.resolve()
    except (OSError, ValueError) as exc:
        raise ReadOnlyReceiptLaunchError("PATH_INVALID", "launch manifest path is invalid") from exc
    try:
        manifest.relative_to(root)
    except ValueError as exc:
        raise ReadOnlyReceiptLaunchError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "launch manifest is outside artifact root"
        ) from exc
    if not manifest.is_file():
        raise ReadOnlyReceiptLaunchError("INPUT_UNAVAILABLE", "launch manifest is unavailable")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadOnlyReceiptLaunchError("INPUT_INVALID", "launch manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise ReadOnlyReceiptLaunchError("INPUT_INVALID", "launch manifest must be an object")
    if set(payload) != _MANIFEST_FIELDS:
        raise ReadOnlyReceiptLaunchError("SCHEMA_INVALID", "launch manifest fields are invalid")
    return payload


def _relative_artifact_path(value: Any, *, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ReadOnlyReceiptLaunchError("PATH_INVALID", f"{label} is invalid")
    # Explicitly check Windows syntax even when this function is reused on a
    # non-Windows host to keep the manifest contract platform-independent.
    try:
        is_absolute = Path(value).is_absolute() or PureWindowsPath(value).is_absolute()
    except (OSError, ValueError) as exc:
        raise ReadOnlyReceiptLaunchError("PATH_INVALID", f"{label} is invalid") from exc
    if is_absolute:
        raise ReadOnlyReceiptLaunchError("PATH_INVALID", f"{label} must be relative")
    try:
        candidate = (root / Path(value)).resolve()
    except (OSError, ValueError) as exc:
        raise ReadOnlyReceiptLaunchError("PATH_INVALID", f"{label} is invalid") from exc
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ReadOnlyReceiptLaunchError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} escapes artifact root"
        ) from exc
    if not candidate.is_file():
        raise ReadOnlyReceiptLaunchError("INPUT_UNAVAILABLE", f"{label} is unavailable")
    return candidate


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise ReadOnlyReceiptLaunchError("HASH_INVALID", f"{label} is invalid")
    if any(char not in _HEX_DIGITS for char in value):
        raise ReadOnlyReceiptLaunchError("HASH_INVALID", f"{label} is invalid")
    return value


def _read_port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ReadOnlyReceiptLaunchError("PORT_INVALID", "port is invalid")
    return value


def load_read_only_receipt_launch_config(
    *, manifest_path: Path, artifact_root: Path
) -> ReadOnlyReceiptLaunchConfig:
    """Validate a manifest, its hashes, and the Node52/53 receipt contract."""

    payload = _read_manifest(manifest_path, artifact_root=artifact_root)
    if not isinstance(payload["launch_version"], str) or (
        payload["launch_version"] != READ_ONLY_RECEIPT_SERVICE_LAUNCH_VERSION
    ):
        raise ReadOnlyReceiptLaunchError("VERSION_MISMATCH", "launch version is invalid")
    if not isinstance(payload["service_version"], str) or (
        payload["service_version"] != READ_ONLY_RECEIPT_SERVICE_VERSION
    ):
        raise ReadOnlyReceiptLaunchError("VERSION_MISMATCH", "service version is invalid")
    if not isinstance(payload["probe_version"], str) or (
        payload["probe_version"] != READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION
    ):
        raise ReadOnlyReceiptLaunchError("VERSION_MISMATCH", "probe version is invalid")
    if not isinstance(payload["host"], str) or payload["host"] not in _ALLOWED_HOSTS:
        raise ReadOnlyReceiptLaunchError("HOST_INVALID", "host must be 127.0.0.1 or ::1")
    if payload["decision_ready"] is not False:
        raise ReadOnlyReceiptLaunchError("DECISION_GATE_INVALID", "decision_ready must be false")

    root = artifact_root.resolve()
    receipt_path = _relative_artifact_path(payload["receipt_path"], root=root, label="receipt path")
    receipt_report_path = _relative_artifact_path(
        payload["receipt_report_path"], root=root, label="receipt report path"
    )
    if receipt_path == receipt_report_path:
        raise ReadOnlyReceiptLaunchError("PATH_INVALID", "receipt inputs must be distinct files")
    receipt_sha256 = _sha256(payload["receipt_sha256"], label="receipt SHA-256")
    receipt_report_sha256 = _sha256(
        payload["receipt_report_sha256"], label="receipt report SHA-256"
    )
    try:
        actual_receipt_sha256 = sha256_bytes(receipt_path.read_bytes())
        actual_report_sha256 = sha256_bytes(receipt_report_path.read_bytes())
    except OSError as exc:
        raise ReadOnlyReceiptLaunchError(
            "INPUT_UNAVAILABLE", "launch artifacts are unavailable"
        ) from exc
    if receipt_sha256 != actual_receipt_sha256:
        raise ReadOnlyReceiptLaunchError("HASH_MISMATCH", "receipt SHA-256 does not match")
    if receipt_report_sha256 != actual_report_sha256:
        raise ReadOnlyReceiptLaunchError(
            "HASH_MISMATCH", "receipt report SHA-256 does not match"
        )

    try:
        summary = load_read_only_receipt_summary(
            receipt_path=receipt_path,
            receipt_report_path=receipt_report_path,
            artifact_root=root,
        )
    except ReadOnlyReceiptServiceError as exc:
        raise ReadOnlyReceiptLaunchError(exc.code, str(exc)) from exc
    if summary["service_version"] != payload["service_version"]:
        raise ReadOnlyReceiptLaunchError("VERSION_MISMATCH", "receipt service version is invalid")
    if summary["decision_ready"] is not False:
        raise ReadOnlyReceiptLaunchError("DECISION_GATE_INVALID", "decision_ready must be false")

    return ReadOnlyReceiptLaunchConfig(
        artifact_root=root,
        receipt_path=receipt_path,
        receipt_report_path=receipt_report_path,
        receipt_sha256=receipt_sha256,
        receipt_report_sha256=receipt_report_sha256,
        host=payload["host"],
        port=_read_port(payload["port"]),
        receipt_status=summary["status"],
        receipt_ready=summary["receipt_ready"],
        decision_ready=False,
    )


def check_read_only_receipt_launch(
    *, manifest_path: Path, artifact_root: Path
) -> ReadOnlyReceiptLaunchConfig:
    """Alias for the side-effect-free launch validation used by ``--check-only``."""

    return load_read_only_receipt_launch_config(
        manifest_path=manifest_path,
        artifact_root=artifact_root,
    )


def serve_read_only_receipt_launch(config: ReadOnlyReceiptLaunchConfig) -> None:
    """Start the Node53 server from a validated manifest configuration."""

    server = create_read_only_receipt_server(
        receipt_path=config.receipt_path,
        receipt_report_path=config.receipt_report_path,
        artifact_root=config.artifact_root,
        host=config.host,
        port=config.port,
    )
    try:
        import sys

        print(
            f"read-only receipt service listening on http://{config.host}:{config.port}",
            file=sys.stderr,
        )
        server.serve_forever()
    finally:
        server.server_close()


def launch_check_report(config: ReadOnlyReceiptLaunchConfig) -> dict[str, Any]:
    """Return a safe, path-free summary for a successful check-only run."""

    return {
        "launch_version": READ_ONLY_RECEIPT_SERVICE_LAUNCH_VERSION,
        "service_version": READ_ONLY_RECEIPT_SERVICE_VERSION,
        "probe_version": READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION,
        "host": config.host,
        "port": config.port,
        "receipt_status": config.receipt_status,
        "receipt_ready": config.receipt_ready,
        "decision_ready": config.decision_ready,
        "validation": "passed",
    }
