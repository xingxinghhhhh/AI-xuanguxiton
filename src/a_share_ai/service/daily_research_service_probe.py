"""Probe the Node59 daily-research service over loopback HTTP."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..runtime.daily_research_admission import DAILY_RESEARCH_ADMISSION_VERSION
from .read_only_receipt_server import READ_ONLY_RECEIPT_SERVICE_VERSION

DAILY_RESEARCH_SERVICE_PROBE_VERSION = "daily-research-service-probe-v1"
DEFAULT_DAILY_RESEARCH_PROBE_TIMEOUT_SECONDS = 3.0
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
_DAILY_FIELDS = {
    "service_version",
    "admission_version",
    "symbol",
    "as_of",
    "evaluation_at",
    "status",
    "freshness_status",
    "admission_ready",
    "issues",
    "decision_ready",
}
_DAILY_STATUSES = {"ready", "stale", "calendar_unknown", "blocked", "invalid"}
_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:\b[A-Z]:[\\/]|(?:^|\s)/)")


class DailyResearchServiceProbeError(ValueError):
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
        raise DailyResearchServiceProbeError("URL_INVALID", "base URL is required")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise DailyResearchServiceProbeError("URL_INVALID", "base URL is invalid") from exc
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise DailyResearchServiceProbeError("URL_INVALID", "base URL must use HTTP or HTTPS")
    if hostname not in _ALLOWED_HOSTS:
        raise DailyResearchServiceProbeError("URL_INVALID", "base URL must target loopback")
    if parsed.username is not None or parsed.password is not None:
        raise DailyResearchServiceProbeError("URL_INVALID", "base URL cannot contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise DailyResearchServiceProbeError(
            "URL_INVALID", "base URL cannot contain a path or query"
        )
    if port is not None and not 1 <= port <= 65535:
        raise DailyResearchServiceProbeError("URL_INVALID", "base URL port is invalid")
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def _base_report() -> dict[str, Any]:
    return {
        "probe_version": DAILY_RESEARCH_SERVICE_PROBE_VERSION,
        "status": "invalid",
        "health_status": None,
        "ready_status": None,
        "receipt_status": None,
        "daily_admission_status": None,
        "daily_admission_ready": None,
        "decision_ready": None,
        "issues": [],
    }


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _response_contains_path(payload: Mapping[str, Any]) -> bool:
    return bool(_ABSOLUTE_PATH_RE.search(json.dumps(payload, ensure_ascii=False, sort_keys=True)))


def _validate_issues(payload: Any) -> None:
    if not isinstance(payload, list):
        raise DailyResearchServiceProbeError("RESPONSE_INVALID", "service issues are invalid")
    for issue in payload:
        if (
            not isinstance(issue, dict)
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("message"), str)
        ):
            raise DailyResearchServiceProbeError("RESPONSE_INVALID", "service issues are invalid")


def _validate_receipt_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "receipt response must be an object"
        )
    if set(payload) != _SUMMARY_FIELDS:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "receipt response fields are invalid"
        )
    if payload.get("service_version") != READ_ONLY_RECEIPT_SERVICE_VERSION:
        raise DailyResearchServiceProbeError("VERSION_MISMATCH", "service version is invalid")
    if payload.get("status") not in {"ready", "stale", "blocked"}:
        raise DailyResearchServiceProbeError("RESPONSE_INVALID", "service status is invalid")
    if not isinstance(payload.get("symbol"), str) or not payload["symbol"]:
        raise DailyResearchServiceProbeError("RESPONSE_INVALID", "service symbol is invalid")
    if not isinstance(payload.get("package_count"), int) or payload["package_count"] < 0:
        raise DailyResearchServiceProbeError("RESPONSE_INVALID", "service package_count is invalid")
    if not isinstance(payload.get("receipt_ready"), bool):
        raise DailyResearchServiceProbeError("RESPONSE_INVALID", "service receipt_ready is invalid")
    if not isinstance(payload.get("decision_ready"), bool):
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "service decision_ready is invalid"
        )
    if payload["decision_ready"] is not False:
        raise DailyResearchServiceProbeError(
            "DECISION_GATE_INVALID", "service decision_ready must be false"
        )
    if payload["status"] == "ready" and payload["receipt_ready"] is not True:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "ready service must be receipt_ready"
        )
    if payload["status"] != "ready" and payload["receipt_ready"] is not False:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "stale or blocked service cannot be receipt_ready"
        )
    _validate_issues(payload.get("issues"))
    if _response_contains_path(payload):
        raise DailyResearchServiceProbeError("SENSITIVE_OUTPUT", "service response contains a path")
    return payload


def _validate_daily_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission response must be an object"
        )
    if set(payload) != _DAILY_FIELDS:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission response fields are invalid"
        )
    if payload.get("service_version") != READ_ONLY_RECEIPT_SERVICE_VERSION:
        raise DailyResearchServiceProbeError("VERSION_MISMATCH", "service version is invalid")
    if payload.get("admission_version") != DAILY_RESEARCH_ADMISSION_VERSION:
        raise DailyResearchServiceProbeError(
            "VERSION_MISMATCH", "daily admission version is invalid"
        )
    if payload.get("status") not in _DAILY_STATUSES:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission status is invalid"
        )
    if payload.get("freshness_status") != payload.get("status"):
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission freshness status is invalid"
        )
    if not isinstance(payload.get("symbol"), str) or not payload["symbol"]:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission symbol is invalid"
        )
    if not isinstance(payload.get("as_of"), str) or not payload["as_of"]:
        raise DailyResearchServiceProbeError("RESPONSE_INVALID", "daily admission as_of is invalid")
    if not isinstance(payload.get("evaluation_at"), str) or not payload["evaluation_at"]:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission evaluation_at is invalid"
        )
    if not isinstance(payload.get("admission_ready"), bool):
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission_ready is invalid"
        )
    if payload["admission_ready"] is not (payload["status"] == "ready"):
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "daily admission status and readiness differ"
        )
    if payload.get("decision_ready") is not False:
        raise DailyResearchServiceProbeError(
            "DECISION_GATE_INVALID", "daily decision_ready must be false"
        )
    _validate_issues(payload.get("issues"))
    if _response_contains_path(payload):
        raise DailyResearchServiceProbeError(
            "SENSITIVE_OUTPUT", "daily admission response contains a path"
        )
    return payload


def _fetch_json(
    opener: urllib.request.OpenerDirector, url: str, timeout: float
) -> tuple[int, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read(1_048_577)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(1_048_577)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DailyResearchServiceProbeError(
            "SERVICE_UNAVAILABLE", "service request failed"
        ) from exc
    if status in {301, 302, 303, 307, 308}:
        raise DailyResearchServiceProbeError("REDIRECT_REJECTED", "service redirect is not allowed")
    if len(raw) > 1_048_576:
        raise DailyResearchServiceProbeError("RESPONSE_INVALID", "service response is too large")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DailyResearchServiceProbeError(
            "RESPONSE_INVALID", "service response is not JSON"
        ) from exc


def _expect_status(name: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise DailyResearchServiceProbeError(
            "HTTP_STATUS_INVALID", f"{name} returned HTTP {actual}"
        )


def probe_daily_research_service(
    *,
    base_url: str,
    timeout_seconds: float = DEFAULT_DAILY_RESEARCH_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe all four Node59 GET routes and return a deterministic report."""

    normalized_url = _normalize_base_url(base_url)
    if timeout_seconds <= 0:
        raise DailyResearchServiceProbeError("TIMEOUT_INVALID", "timeout-seconds must be positive")
    report = _base_report()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler()
    )
    try:
        health_status, health_raw = _fetch_json(
            opener, f"{normalized_url}/healthz", timeout_seconds
        )
        report["health_status"] = health_status
        _expect_status("healthz", health_status, 200)
        health = _validate_receipt_summary(health_raw)
        report["decision_ready"] = health["decision_ready"]

        ready_status, ready_raw = _fetch_json(opener, f"{normalized_url}/readyz", timeout_seconds)
        report["ready_status"] = ready_status
        ready = _validate_receipt_summary(ready_raw)
        if ready != health:
            raise DailyResearchServiceProbeError("RESPONSE_MISMATCH", "healthz and readyz differ")
        _expect_status("readyz", ready_status, 200 if health["receipt_ready"] else 503)

        receipt_status, receipt_raw = _fetch_json(
            opener, f"{normalized_url}/v1/research/receipt", timeout_seconds
        )
        report["receipt_status"] = receipt_status
        receipt = _validate_receipt_summary(receipt_raw)
        if receipt != health:
            raise DailyResearchServiceProbeError(
                "RESPONSE_MISMATCH", "healthz and receipt endpoint differ"
            )
        _expect_status("receipt", receipt_status, 200)

        daily_status, daily_raw = _fetch_json(
            opener, f"{normalized_url}/v1/research/daily-admission", timeout_seconds
        )
        report["daily_admission_status"] = daily_status
        _expect_status("daily admission", daily_status, 200)
        daily = _validate_daily_summary(daily_raw)
        report["daily_admission_ready"] = daily["admission_ready"]
        if daily["admission_ready"] and not health["receipt_ready"]:
            report["status"] = "blocked"
        elif not daily["admission_ready"] and health["receipt_ready"]:
            raise DailyResearchServiceProbeError(
                "READINESS_MISMATCH", "daily admission is not ready while service is ready"
            )
        else:
            report["status"] = "ready" if health["receipt_ready"] else "blocked"
        if report["status"] == "blocked":
            report["issues"] = [
                _issue("SERVICE_NOT_READY", f"daily service status is {daily['status']}")
            ]
    except DailyResearchServiceProbeError as exc:
        report["status"] = "invalid"
        report["issues"] = [_issue(exc.code, str(exc))]
    return report


def probe_exit_code(report: Mapping[str, Any]) -> int:
    """Return 0 only for a ready service and ready daily admission."""

    return (
        0
        if report.get("status") == "ready"
        and report.get("daily_admission_ready") is True
        and report.get("decision_ready") is False
        else 1
    )


daily_research_service_probe_exit_code = probe_exit_code
