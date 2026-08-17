"""Serve a validated Node52 receipt through a loopback-only HTTP boundary."""

from __future__ import annotations

import json
import re
import socket
import sys
from collections.abc import Mapping
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PureWindowsPath
from typing import Any

from ..analysis.market_aware_session_history_final_receipt import (
    MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
)
from ..market.replay import sha256_bytes
from ..runtime.daily_research_admission import DAILY_RESEARCH_ADMISSION_VERSION

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
_DAILY_ADMISSION_FIELDS = {
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
_DAILY_ADMISSION_STATUSES = {"ready", "stale", "calendar_unknown", "blocked", "invalid"}


class ReadOnlyReceiptServiceError(ValueError):
    """A sanitized configuration or receipt validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


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


def _safe_relative_input(value: Any, *, root: Path, label: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} path is invalid")
    windows_path = PureWindowsPath(value)
    if (
        Path(value).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in windows_path.parts)
        or value.replace("\\", "/") != "/".join(windows_path.parts)
    ):
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} path is invalid")
    candidate = _safe_input(root / Path(value), root=root, label=label)
    return candidate, candidate.relative_to(root.resolve()).as_posix()


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


def _parse_daily_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} must include timezone")
    return parsed


def load_daily_research_admission_summary(
    *, admission_path: Path, admission_report_path: Path, artifact_root: Path
) -> dict[str, Any]:
    """Load and validate the Node58 admission pair for service exposure."""

    root = artifact_root.resolve()
    if not root.is_dir():
        raise ReadOnlyReceiptServiceError(
            "ARTIFACT_ROOT_INVALID", "daily admission root is invalid"
        )
    admission_file = _safe_input(admission_path, root=root, label="daily admission")
    report_file = _safe_input(admission_report_path, root=root, label="daily admission report")
    if admission_file == report_file:
        raise ReadOnlyReceiptServiceError(
            "PATH_INVALID", "daily admission inputs must be distinct files"
        )
    admission = _read_json(admission_file, label="daily admission")
    report = _read_json(report_file, label="daily admission report")
    for label, payload in (("daily admission", admission), ("daily admission report", report)):
        if set(payload) != _DAILY_ADMISSION_FIELDS:
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} fields are invalid")
        _validate_self_hash(payload, label=label)
        if payload.get("admission_version") != DAILY_RESEARCH_ADMISSION_VERSION:
            raise ReadOnlyReceiptServiceError("VERSION_MISMATCH", f"{label} version is invalid")
        if payload.get("decision_ready") is not False:
            raise ReadOnlyReceiptServiceError(
                "DECISION_GATE_INVALID", f"{label} decision_ready must be false"
            )
        if payload.get("status") not in _DAILY_ADMISSION_STATUSES:
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} status is invalid")
        if payload.get("freshness_status") != payload.get("status"):
            raise ReadOnlyReceiptServiceError("FIELD_MISMATCH", f"{label} freshness status differs")
        for field in ("audit_ready", "analysis_input_ready", "admission_ready"):
            if not isinstance(payload.get(field), bool):
                raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} {field} is invalid")
        if payload["admission_ready"] is not (payload["status"] == "ready"):
            raise ReadOnlyReceiptServiceError(
                "FIELD_MISMATCH", f"{label} admission gate is invalid"
            )
        if payload["status"] == "ready" and (
            payload["audit_ready"] is not True or payload["analysis_input_ready"] is not True
        ):
            raise ReadOnlyReceiptServiceError("FIELD_MISMATCH", f"{label} ready gates are invalid")
        if not isinstance(payload.get("symbol"), str) or not payload["symbol"].strip():
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} symbol is invalid")
        _parse_daily_datetime(payload.get("as_of"), label=f"{label}.as_of")
        _parse_daily_datetime(payload.get("evaluation_at"), label=f"{label}.evaluation_at")
        if not isinstance(payload.get("issues"), list):
            raise ReadOnlyReceiptServiceError("REPORT_INVALID", f"{label} issues are invalid")

    for field in _DAILY_ADMISSION_FIELDS:
        if admission.get(field) != report.get(field):
            raise ReadOnlyReceiptServiceError("FIELD_MISMATCH", f"daily admission {field} differs")
    admission_root = root
    for path_field, sha_field in (
        ("run_report_path", "run_report_sha256"),
        ("run_audit_report_path", "run_audit_report_sha256"),
        ("calendar_path", "calendar_sha256"),
        ("calendar_report_path", "calendar_report_sha256"),
    ):
        file_path, relative = _safe_relative_input(
            admission[path_field], root=admission_root, label=f"daily admission {path_field}"
        )
        if relative != admission[path_field]:
            raise ReadOnlyReceiptServiceError(
                "REPORT_INVALID", f"daily admission {path_field} is not normalized"
            )
        expected_sha = admission[sha_field]
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ReadOnlyReceiptServiceError(
                "REPORT_INVALID", f"daily admission {sha_field} is invalid"
            )
        if sha256_bytes(file_path.read_bytes()) != expected_sha:
            raise ReadOnlyReceiptServiceError(
                "HASH_MISMATCH", f"daily admission {path_field} hash differs"
            )
    issues = _safe_issues(admission["issues"])
    return {
        "service_version": READ_ONLY_RECEIPT_SERVICE_VERSION,
        "admission_version": admission["admission_version"],
        "symbol": admission["symbol"],
        "as_of": admission["as_of"],
        "evaluation_at": admission["evaluation_at"],
        "status": admission["status"],
        "freshness_status": admission["freshness_status"],
        "admission_ready": admission["admission_ready"],
        "issues": issues,
        "decision_ready": False,
    }


class _ReceiptHTTPServer(HTTPServer):
    summary: dict[str, Any]
    daily_admission_summary: dict[str, Any] | None


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
        health_summary = dict(self.server.summary)
        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, health_summary)
            return
        if self.path == "/readyz":
            status = (
                HTTPStatus.OK
                if self.server.summary["receipt_ready"]
                and (
                    self.server.daily_admission_summary is None
                    or self.server.daily_admission_summary["admission_ready"]
                )
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
            self._send_json(status, health_summary)
            return
        if self.path == "/v1/research/receipt":
            self._send_json(HTTPStatus.OK, self.server.summary)
            return
        if self.path == "/v1/research/daily-admission":
            if self.server.daily_admission_summary is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            else:
                self._send_json(HTTPStatus.OK, self.server.daily_admission_summary)
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
    daily_admission_path: Path | None = None,
    daily_admission_report_path: Path | None = None,
    daily_admission_root: Path | None = None,
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
    daily_values = (daily_admission_path, daily_admission_report_path, daily_admission_root)
    if any(value is not None for value in daily_values) and not all(
        value is not None for value in daily_values
    ):
        raise ReadOnlyReceiptServiceError(
            "CONFIG_INVALID", "daily admission options must be provided as a complete set"
        )
    daily_summary = None
    if all(value is not None for value in daily_values):
        daily_summary = load_daily_research_admission_summary(
            admission_path=daily_admission_path,
            admission_report_path=daily_admission_report_path,
            artifact_root=daily_admission_root,
        )
    try:
        server_class = _ReceiptHTTPServerV6 if host == "::1" else _ReceiptHTTPServer
        server = server_class((host, port), _ReceiptRequestHandler)
    except OSError as exc:
        raise ReadOnlyReceiptServiceError(
            "BIND_FAILED", "could not bind service host and port"
        ) from exc
    server.summary = summary
    server.daily_admission_summary = daily_summary
    return server


def serve_read_only_receipt(
    *,
    receipt_path: Path,
    receipt_report_path: Path,
    artifact_root: Path,
    host: str = DEFAULT_READ_ONLY_RECEIPT_HOST,
    port: int = DEFAULT_READ_ONLY_RECEIPT_PORT,
    daily_admission_path: Path | None = None,
    daily_admission_report_path: Path | None = None,
    daily_admission_root: Path | None = None,
) -> None:
    """Validate the receipt and serve it until interrupted."""

    server = create_read_only_receipt_server(
        receipt_path=receipt_path,
        receipt_report_path=receipt_report_path,
        artifact_root=artifact_root,
        host=host,
        port=port,
        daily_admission_path=daily_admission_path,
        daily_admission_report_path=daily_admission_report_path,
        daily_admission_root=daily_admission_root,
    )
    try:
        print(
            f"read-only receipt service listening on http://{host}:{port}",
            file=sys.stderr,
        )
        server.serve_forever()
    finally:
        server.server_close()
