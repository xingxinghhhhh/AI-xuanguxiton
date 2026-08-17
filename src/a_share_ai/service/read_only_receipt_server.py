"""Serve a validated Node52 receipt through a loopback-only HTTP boundary."""

from __future__ import annotations

import json
import re
import socket
import sys
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ..analysis.market_aware_session_history_final_receipt import (
    MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
)
from ..market.replay import sha256_bytes

READ_ONLY_RECEIPT_SERVICE_VERSION = "read-only-receipt-service-v1"
DEFAULT_READ_ONLY_RECEIPT_HOST = "127.0.0.1"
DEFAULT_READ_ONLY_RECEIPT_PORT = 8765
_ALLOWED_HOSTS = {"127.0.0.1", "::1"}
_ALLOWED_STATUSES = {"blocked", "ready", "stale"}
_SUMMARY_FIELDS = (
    "service_version",
    "status",
    "symbol",
    "package_count",
    "first_as_of",
    "last_as_of",
    "receipt_ready",
    "decision_ready",
    "issues",
)


class ReadOnlyReceiptServiceError(ValueError):
    """A sanitized configuration or receipt validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadOnlyReceiptServiceError("INPUT_INVALID", f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise ReadOnlyReceiptServiceError("INPUT_INVALID", f"{label} must be an object")
    return payload


def _safe_input(path: Path, *, root: Path, label: str) -> Path:
    candidate = path.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ReadOnlyReceiptServiceError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root"
        ) from exc
    if not candidate.is_file():
        raise ReadOnlyReceiptServiceError("INPUT_UNAVAILABLE", f"{label} is not a file")
    return candidate


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> None:
    declared = payload.get("output_sha256")
    if not isinstance(declared, str) or len(declared) != 64:
        raise ReadOnlyReceiptServiceError("HASH_INVALID", f"{label}.output_sha256 is invalid")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if declared != sha256_bytes(_json_bytes(canonical)):
        raise ReadOnlyReceiptServiceError("HASH_MISMATCH", f"{label}.output_sha256 is invalid")


def _validate_relative_report_paths(report: Mapping[str, Any], *, root: Path) -> None:
    inputs = report.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", "report.inputs is invalid")
    for role, item in inputs.items():
        if not isinstance(role, str) or not isinstance(item, Mapping):
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", "report.inputs is invalid")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ReadOnlyReceiptServiceError(
                "REPORT_INVALID", f"report input {role} path is invalid"
            )
        try:
            (root / relative).resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ReadOnlyReceiptServiceError(
                "PATH_OUTSIDE_ARTIFACT_ROOT", f"report input {role} escapes artifact root"
            ) from exc


def _redact_path_text(value: str) -> str:
    value = re.sub(r"(?i)\b[A-Z]:[\\/][^\"']*", "<redacted-path>", value)
    value = re.sub(r"(?<![A-Za-z0-9])/(?:[^\s\"']+/?)+", "<redacted-path>", value)
    return value


def _safe_issues(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", "issues is invalid")
    issues: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", "issues contains an invalid item")
        code = item.get("code")
        message = item.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", "issues contains an invalid item")
        issues.append({"code": code, "message": _redact_path_text(message)})
    return issues


def _validate_cross_fields(
    receipt: Mapping[str, Any], report: Mapping[str, Any]
) -> list[dict[str, str]]:
    if receipt.get("receipt_version") != MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION:
        raise ReadOnlyReceiptServiceError("VERSION_MISMATCH", "receipt version is invalid")
    if report.get("receipt_version") != MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION:
        raise ReadOnlyReceiptServiceError("VERSION_MISMATCH", "report version is invalid")
    if receipt.get("status") not in _ALLOWED_STATUSES:
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", "receipt status is invalid")
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
    ):
        if receipt.get(field) != report.get(field):
            raise ReadOnlyReceiptServiceError("FIELD_MISMATCH", f"receipt/report {field} differs")
    if receipt.get("decision_ready") is not False:
        raise ReadOnlyReceiptServiceError("DECISION_GATE_INVALID", "decision_ready must be false")
    if receipt.get("issues") != report.get("issues"):
        raise ReadOnlyReceiptServiceError("FIELD_MISMATCH", "receipt/report issues differ")
    if not isinstance(receipt.get("symbol"), str) or not receipt["symbol"]:
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", "symbol is invalid")
    if not isinstance(receipt.get("package_count"), int) or receipt["package_count"] < 0:
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", "package_count is invalid")
    for field in ("admission_ready", "render_ready", "audit_ready", "receipt_ready"):
        if not isinstance(receipt.get(field), bool):
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{field} is invalid")
    if receipt["status"] == "ready" and receipt["receipt_ready"] is not True:
        raise ReadOnlyReceiptServiceError("FIELD_MISMATCH", "ready status requires receipt_ready")
    if receipt["status"] != "ready" and receipt["receipt_ready"] is not False:
        raise ReadOnlyReceiptServiceError(
            "FIELD_MISMATCH", "non-ready status cannot be receipt_ready"
        )
    return _safe_issues(receipt.get("issues"))


def load_read_only_receipt_summary(
    *, receipt_path: Path, receipt_report_path: Path, artifact_root: Path
) -> dict[str, Any]:
    """Load and validate only the two Node52 files needed by the service."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise ReadOnlyReceiptServiceError(
            "ARTIFACT_ROOT_INVALID", "artifact root is not a directory"
        )
    receipt_file = _safe_input(receipt_path, root=root, label="receipt")
    report_file = _safe_input(receipt_report_path, root=root, label="receipt report")
    if receipt_file == report_file:
        raise ReadOnlyReceiptServiceError("PATH_INVALID", "receipt inputs must be distinct files")
    receipt = _read_json(receipt_file, label="receipt")
    report = _read_json(report_file, label="receipt report")
    _validate_self_hash(receipt, label="receipt")
    _validate_self_hash(report, label="receipt report")
    declared_receipt_sha = report.get("receipt_sha256")
    actual_receipt_sha = sha256_bytes(receipt_file.read_bytes())
    if declared_receipt_sha != actual_receipt_sha:
        raise ReadOnlyReceiptServiceError(
            "HASH_MISMATCH", "report receipt SHA does not match receipt"
        )
    _validate_relative_report_paths(report, root=root)
    issues = _validate_cross_fields(receipt, report)
    return {
        "service_version": READ_ONLY_RECEIPT_SERVICE_VERSION,
        "status": receipt["status"],
        "symbol": receipt["symbol"],
        "package_count": receipt["package_count"],
        "first_as_of": receipt["first_as_of"],
        "last_as_of": receipt["last_as_of"],
        "receipt_ready": receipt["receipt_ready"],
        "decision_ready": False,
        "issues": issues,
    }


class _ReceiptHTTPServer(HTTPServer):
    summary: dict[str, Any]


class _ReceiptHTTPServerV6(_ReceiptHTTPServer):
    address_family = socket.AF_INET6


class _ReceiptRequestHandler(BaseHTTPRequestHandler):
    server: _ReceiptHTTPServer

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, self.server.summary)
            return
        if self.path == "/readyz":
            status = (
                HTTPStatus.OK
                if self.server.summary["receipt_ready"]
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self._send_json(status, self.server.summary)
            return
        if self.path == "/v1/research/receipt":
            self._send_json(HTTPStatus.OK, self.server.summary)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _method_not_allowed(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED.value)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_HEAD = _method_not_allowed

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_read_only_receipt_server(
    *,
    receipt_path: Path,
    receipt_report_path: Path,
    artifact_root: Path,
    host: str = DEFAULT_READ_ONLY_RECEIPT_HOST,
    port: int = DEFAULT_READ_ONLY_RECEIPT_PORT,
) -> _ReceiptHTTPServer:
    """Create a validated loopback-only HTTP server without starting it."""

    if host not in _ALLOWED_HOSTS:
        raise ReadOnlyReceiptServiceError("HOST_INVALID", "host must be 127.0.0.1 or ::1")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ReadOnlyReceiptServiceError("PORT_INVALID", "port must be between 1 and 65535")
    summary = load_read_only_receipt_summary(
        receipt_path=receipt_path,
        receipt_report_path=receipt_report_path,
        artifact_root=artifact_root,
    )
    try:
        server_class = _ReceiptHTTPServerV6 if host == "::1" else _ReceiptHTTPServer
        server = server_class((host, port), _ReceiptRequestHandler)
    except OSError as exc:
        raise ReadOnlyReceiptServiceError(
            "BIND_FAILED", "could not bind service host and port"
        ) from exc
    server.summary = summary
    return server


def serve_read_only_receipt(
    *,
    receipt_path: Path,
    receipt_report_path: Path,
    artifact_root: Path,
    host: str = DEFAULT_READ_ONLY_RECEIPT_HOST,
    port: int = DEFAULT_READ_ONLY_RECEIPT_PORT,
) -> None:
    """Validate the receipt and serve it until interrupted."""

    server = create_read_only_receipt_server(
        receipt_path=receipt_path,
        receipt_report_path=receipt_report_path,
        artifact_root=artifact_root,
        host=host,
        port=port,
    )
    try:
        print(
            f"read-only receipt service listening on http://{host}:{port}",
            file=sys.stderr,
        )
        server.serve_forever()
    finally:
        server.server_close()
