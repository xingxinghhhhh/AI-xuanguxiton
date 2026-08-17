"""Probe the loopback-only Node53 receipt service for deployment checks."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .read_only_receipt_server import READ_ONLY_RECEIPT_SERVICE_VERSION

READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION = "read-only-receipt-service-probe-v1"
DEFAULT_PROBE_TIMEOUT_SECONDS = 3.0
_ALLOWED_HOSTS = {"127.0.0.1", "::1"}
_ALLOWED_SCHEMES = {"http", "https"}
_SUMMARY_FIELDS = {
    "service_version",
    "status",
    "symbol",
    "package_count",
    "first_as_of",
    "last_as_of",
    "receipt_ready",
    "decision_ready",
    "issues",
}
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:\b[A-Z]:[\\/]|(?:^|\s)/)")


class ReadOnlyReceiptProbeError(ValueError):
    """A sanitized probe configuration or response failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    @staticmethod
    def redirect_request(*args: Any, **kwargs: Any) -> None:
        return None


def _normalize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReadOnlyReceiptProbeError("URL_INVALID", "base URL is required")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ReadOnlyReceiptProbeError("URL_INVALID", "base URL is invalid") from exc
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ReadOnlyReceiptProbeError("URL_INVALID", "base URL must use HTTP or HTTPS")
    if hostname not in _ALLOWED_HOSTS:
        raise ReadOnlyReceiptProbeError("URL_INVALID", "base URL must target loopback")
    if parsed.username is not None or parsed.password is not None:
        raise ReadOnlyReceiptProbeError("URL_INVALID", "base URL cannot contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ReadOnlyReceiptProbeError("URL_INVALID", "base URL cannot contain a path or query")
    if port is not None and not 1 <= port <= 65535:
        raise ReadOnlyReceiptProbeError("URL_INVALID", "base URL port is invalid")
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def _base_report(base_url: str) -> dict[str, Any]:
    return {
        "probe_version": READ_ONLY_RECEIPT_SERVICE_PROBE_VERSION,
        "base_url": base_url,
        "status": "invalid",
        "health_http_status": None,
        "ready_http_status": None,
        "receipt_http_status": None,
        "receipt_ready": None,
        "decision_ready": None,
        "checks": [],
        "issues": [],
    }


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _response_contains_path(payload: Mapping[str, Any]) -> bool:
    return bool(_ABSOLUTE_PATH_RE.search(json.dumps(payload, ensure_ascii=False, sort_keys=True)))


def _validate_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service response must be an object")
    if set(payload) != _SUMMARY_FIELDS:
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service response fields are invalid")
    if payload.get("service_version") != READ_ONLY_RECEIPT_SERVICE_VERSION:
        raise ReadOnlyReceiptProbeError("VERSION_MISMATCH", "service version is invalid")
    if payload.get("status") not in {"ready", "stale", "blocked"}:
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service status is invalid")
    if not isinstance(payload.get("symbol"), str) or not payload["symbol"]:
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service symbol is invalid")
    if not isinstance(payload.get("package_count"), int) or payload["package_count"] < 0:
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service package_count is invalid")
    if not isinstance(payload.get("receipt_ready"), bool):
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service receipt_ready is invalid")
    if not isinstance(payload.get("decision_ready"), bool):
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service decision_ready is invalid")
    if payload["decision_ready"] is not False:
        raise ReadOnlyReceiptProbeError(
            "DECISION_GATE_INVALID", "service decision_ready must be false"
        )
    if payload["status"] == "ready" and payload["receipt_ready"] is not True:
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "ready service must be receipt_ready")
    if payload["status"] != "ready" and payload["receipt_ready"] is not False:
        raise ReadOnlyReceiptProbeError(
            "RESPONSE_INVALID", "stale or blocked service cannot be receipt_ready"
        )
    if not isinstance(payload.get("issues"), list):
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service issues are invalid")
    if _response_contains_path(payload):
        raise ReadOnlyReceiptProbeError("SENSITIVE_OUTPUT", "service response contains a path")
    return payload


def _fetch_json(opener: urllib.request.OpenerDirector, url: str, timeout: float) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read(1_048_577)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(1_048_577)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReadOnlyReceiptProbeError("SERVICE_UNAVAILABLE", "service request failed") from exc
    if status in {301, 302, 303, 307, 308}:
        raise ReadOnlyReceiptProbeError("REDIRECT_REJECTED", "service redirect is not allowed")
    if len(raw) > 1_048_576:
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service response is too large")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReadOnlyReceiptProbeError("RESPONSE_INVALID", "service response is not JSON") from exc


def _check(
    *, name: str, http_status: int, expected_status: int, payload: dict[str, Any]
) -> dict[str, Any]:
    if http_status != expected_status:
        raise ReadOnlyReceiptProbeError(
            "HTTP_STATUS_INVALID", f"{name} returned HTTP {http_status}"
        )
    return {"name": name, "http_status": http_status, "ok": True, "status": payload["status"]}


def probe_read_only_receipt_service(
    *, base_url: str, timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Probe all three Node53 GET routes and return a deterministic report."""

    normalized_url = _normalize_base_url(base_url)
    if timeout_seconds <= 0:
        raise ReadOnlyReceiptProbeError("TIMEOUT_INVALID", "timeout-seconds must be positive")
    report = _base_report(normalized_url)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler()
    )
    try:
        health_status, health_payload_raw = _fetch_json(
            opener, f"{normalized_url}/healthz", timeout_seconds
        )
        health_payload = _validate_summary(health_payload_raw)
        report["health_http_status"] = health_status
        report["checks"].append(
            _check(
                name="healthz",
                http_status=health_status,
                expected_status=200,
                payload=health_payload,
            )
        )

        ready_status, ready_payload_raw = _fetch_json(
            opener, f"{normalized_url}/readyz", timeout_seconds
        )
        ready_payload = _validate_summary(ready_payload_raw)
        if ready_payload != health_payload:
            raise ReadOnlyReceiptProbeError("RESPONSE_MISMATCH", "healthz and readyz differ")
        expected_ready_status = 200 if health_payload["receipt_ready"] else 503
        report["ready_http_status"] = ready_status
        report["checks"].append(
            _check(
                name="readyz",
                http_status=ready_status,
                expected_status=expected_ready_status,
                payload=ready_payload,
            )
        )

        receipt_status, receipt_payload_raw = _fetch_json(
            opener, f"{normalized_url}/v1/research/receipt", timeout_seconds
        )
        receipt_payload = _validate_summary(receipt_payload_raw)
        if receipt_payload != health_payload:
            raise ReadOnlyReceiptProbeError(
                "RESPONSE_MISMATCH", "healthz and receipt endpoint differ"
            )
        report["receipt_http_status"] = receipt_status
        report["checks"].append(
            _check(
                name="receipt",
                http_status=receipt_status,
                expected_status=200,
                payload=receipt_payload,
            )
        )
        report["receipt_ready"] = health_payload["receipt_ready"]
        report["decision_ready"] = health_payload["decision_ready"]
        report["status"] = "ready" if health_payload["receipt_ready"] else "blocked"
        if not health_payload["receipt_ready"]:
            report["issues"] = [
                _issue("SERVICE_NOT_READY", f"receipt status is {health_payload['status']}")
            ]
    except ReadOnlyReceiptProbeError as exc:
        report["issues"] = [_issue(exc.code, str(exc))]
    return report


def probe_exit_code(report: Mapping[str, Any]) -> int:
    """Return 0 only for a fully ready, safe service response."""

    return 0 if report.get("status") == "ready" and report.get("decision_ready") is False else 1
