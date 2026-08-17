import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.service.daily_research_service_probe import (
    DailyResearchServiceProbeError,
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)
from tests.service.test_read_only_receipt_server import (
    _free_port,
    _write_daily_admission_pair,
    _write_input_pair,
)


def _start_server(
    tmp_path: Path, *, daily_status: str = "ready"
) -> tuple[HTTPServer, threading.Thread, str]:
    receipt_path, report_path = _write_input_pair(tmp_path)
    daily_path, daily_report_path, daily_root = _write_daily_admission_pair(
        tmp_path, status=daily_status
    )
    from a_share_ai.service.read_only_receipt_server import create_read_only_receipt_server

    server = create_read_only_receipt_server(
        receipt_path=receipt_path,
        receipt_report_path=report_path,
        artifact_root=tmp_path / "artifacts",
        daily_admission_path=daily_path,
        daily_admission_report_path=daily_report_path,
        daily_admission_root=daily_root,
        port=_free_port(),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.mark.parametrize(
    "daily_status, expected_status, expected_ready, expected_exit",
    [("ready", "ready", True, 0), ("stale", "blocked", False, 1), ("blocked", "blocked", False, 1)],
)
def test_probe_validates_all_four_node59_routes(
    tmp_path: Path,
    daily_status: str,
    expected_status: str,
    expected_ready: bool,
    expected_exit: int,
) -> None:
    server, thread, base_url = _start_server(tmp_path, daily_status=daily_status)
    try:
        report = probe_daily_research_service(base_url=base_url)
        assert set(report) == {
            "probe_version",
            "status",
            "health_status",
            "ready_status",
            "receipt_status",
            "daily_admission_status",
            "daily_admission_ready",
            "decision_ready",
            "issues",
        }
        assert report["status"] == expected_status
        assert report["health_status"] == 200
        assert report["ready_status"] == (200 if expected_ready else 503)
        assert report["receipt_status"] == 200
        assert report["daily_admission_status"] == 200
        assert report["daily_admission_ready"] is expected_ready
        assert report["decision_ready"] is False
        assert daily_research_service_probe_exit_code(report) == expected_exit
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _start_fake_routes(
    routes: dict[str, tuple[int, Any]]
) -> tuple[HTTPServer, threading.Thread, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            status, payload = routes.get(self.path, (404, {"error": "not_found"}))
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _start_raw_routes(
    routes: dict[str, tuple[int, bytes]]
) -> tuple[HTTPServer, threading.Thread, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            status, raw = routes.get(self.path, (404, b'{"error":"not_found"}'))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _receipt_payload() -> dict[str, Any]:
    return {
        "service_version": "read-only-receipt-service-v1",
        "status": "ready",
        "symbol": "600000.SH",
        "package_count": 2,
        "first_as_of": "2026-08-10T00:00:00+00:00",
        "last_as_of": "2026-08-11T00:00:00+00:00",
        "receipt_ready": True,
        "decision_ready": False,
        "issues": [],
    }


def _daily_payload() -> dict[str, Any]:
    return {
        "service_version": "read-only-receipt-service-v1",
        "admission_version": "daily-research-admission-v1",
        "symbol": "600000.SH",
        "as_of": "2026-08-10T08:00:00+00:00",
        "evaluation_at": "2026-08-10T16:00:00+08:00",
        "status": "ready",
        "freshness_status": "ready",
        "admission_ready": True,
        "issues": [],
        "decision_ready": False,
    }


def _valid_route_payloads() -> dict[str, tuple[int, Any]]:
    receipt = _receipt_payload()
    return {
        "/healthz": (200, receipt),
        "/readyz": (200, receipt),
        "/v1/research/receipt": (200, receipt),
        "/v1/research/daily-admission": (200, _daily_payload()),
    }


def test_probe_rejects_daily_unknown_fields_and_sensitive_output() -> None:
    receipt = _receipt_payload()
    daily = _daily_payload()
    daily["unexpected"] = True
    server, thread, base_url = _start_fake_routes(
        {
            "/healthz": (200, receipt),
            "/readyz": (200, receipt),
            "/v1/research/receipt": (200, receipt),
            "/v1/research/daily-admission": (200, daily),
        }
    )
    try:
        report = probe_daily_research_service(base_url=base_url)
        assert report["status"] == "invalid"
        assert report["issues"] == [
            {"code": "RESPONSE_INVALID", "message": "daily admission response fields are invalid"}
        ]
        assert daily_research_service_probe_exit_code(report) == 1
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_probe_rejects_daily_symbol_mismatch_and_unc_path() -> None:
    receipt = _receipt_payload()
    daily = _daily_payload()
    daily["symbol"] = "000001.SZ"
    server, thread, base_url = _start_fake_routes(
        {
            "/healthz": (200, receipt),
            "/readyz": (200, receipt),
            "/v1/research/receipt": (200, receipt),
            "/v1/research/daily-admission": (200, daily),
        }
    )
    try:
        report = probe_daily_research_service(base_url=base_url)
        assert report["issues"] == [
            {
                "code": "RESPONSE_MISMATCH",
                "message": "daily admission symbol differs from receipt symbol",
            }
        ]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_probe_rejects_unc_path() -> None:
    receipt = _receipt_payload()
    daily = _daily_payload()
    daily["issues"] = [{"code": "LEAK", "message": r"\\server\share\file"}]
    server, thread, base_url = _start_fake_routes(
        {
            "/healthz": (200, receipt),
            "/readyz": (200, receipt),
            "/v1/research/receipt": (200, receipt),
            "/v1/research/daily-admission": (200, daily),
        }
    )
    try:
        report = probe_daily_research_service(base_url=base_url)
        assert report["issues"] == [
            {"code": "SENSITIVE_OUTPUT", "message": "daily admission response contains a path"}
        ]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize("http_status", [404, 405])
def test_probe_rejects_missing_or_method_not_allowed_daily_endpoint(http_status: int) -> None:
    routes = _valid_route_payloads()
    routes["/v1/research/daily-admission"] = (http_status, {"error": "not_found"})
    server, thread, base_url = _start_fake_routes(routes)
    try:
        report = probe_daily_research_service(base_url=base_url)
        assert report["status"] == "invalid"
        assert report["daily_admission_status"] == http_status
        assert report["decision_ready"] is False
        assert report["issues"][0]["code"] == "HTTP_STATUS_INVALID"
        assert daily_research_service_probe_exit_code(report) == 1
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize(
    "path, raw",
    [
        ("/healthz", b"not-json"),
        ("/readyz", b"not-json"),
        ("/v1/research/receipt", b"not-json"),
        ("/v1/research/daily-admission", b"not-json"),
        ("/v1/research/daily-admission", b"\xff\xfe\x00"),
    ],
)
def test_probe_rejects_invalid_json_or_utf8(path: str, raw: bytes) -> None:
    routes = _valid_route_payloads()
    routes_raw = {
        route: (status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        for route, (status, payload) in routes.items()
    }
    routes_raw[path] = (200, raw)
    server, thread, base_url = _start_raw_routes(routes_raw)
    try:
        report = probe_daily_research_service(base_url=base_url)
        assert report["status"] == "invalid"
        assert report["decision_ready"] is False
        assert report["issues"] == [
            {"code": "RESPONSE_INVALID", "message": "service response is not JSON"}
        ]
        assert daily_research_service_probe_exit_code(report) == 1
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_probe_rejects_redirect() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        report = probe_daily_research_service(base_url=base_url)
        assert report["status"] == "invalid"
        assert report["issues"] == [
            {"code": "REDIRECT_REJECTED", "message": "service redirect is not allowed"}
        ]
        assert report["decision_ready"] is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

@pytest.mark.parametrize(
    "url",
    [
        "ftp://127.0.0.1:8765",
        "http://example.com:8765",
        "http://127.0.0.1:8765/path",
        "http://user:password@127.0.0.1:8765",
    ],
)
def test_probe_rejects_non_loopback_urls(url: str) -> None:
    with pytest.raises(DailyResearchServiceProbeError, match="base URL"):
        probe_daily_research_service(base_url=url)


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("nan"), float("inf"), float("-inf")])
def test_probe_rejects_non_positive_or_non_finite_timeout(timeout_seconds: float) -> None:
    with pytest.raises(DailyResearchServiceProbeError, match="timeout-seconds"):
        probe_daily_research_service(
            base_url="http://127.0.0.1:8765", timeout_seconds=timeout_seconds
        )


def test_probe_failure_report_keeps_decision_gate_false() -> None:
    report = probe_daily_research_service(
        base_url=f"http://127.0.0.1:{_free_port()}", timeout_seconds=0.1
    )
    assert report["status"] == "invalid"
    assert report["decision_ready"] is False


def test_probe_cli_runs_against_node59_daily_service(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    daily_path, daily_report_path, daily_root = _write_daily_admission_pair(tmp_path)
    port = _free_port()
    service_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "serve-research-receipt",
            "--receipt",
            str(receipt_path),
            "--receipt-report",
            str(report_path),
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--daily-admission",
            str(daily_path),
            "--daily-admission-report",
            str(daily_report_path),
            "--daily-admission-root",
            str(daily_root),
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        result = None
        for _ in range(30):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "a_share_ai.cli",
                    "probe-daily-research-service",
                    "--base-url",
                    f"http://127.0.0.1:{port}",
                    "--timeout-seconds",
                    "2",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                break
        assert result is not None
        assert result.returncode == 0, f"probe failed: {result.stdout}\n{result.stderr}"
        report = json.loads(result.stdout)
        assert report["status"] == "ready"
        assert report["daily_admission_ready"] is True
        assert report["decision_ready"] is False
    finally:
        service_process.terminate()
        service_process.wait(timeout=5)


def test_probe_cli_invalid_url_returns_exit_two() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "probe-daily-research-service",
            "--base-url",
            "http://example.com:8765",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "invalid"
    assert report["issues"] == [
        {"code": "URL_INVALID", "message": "base URL must target loopback"}
    ]


@pytest.mark.parametrize("timeout_text", ["0", "-1", "nan", "inf", "-inf"])
def test_probe_cli_invalid_timeout_returns_exit_two(timeout_text: str) -> None:
    timeout_argument = (
        f"--timeout-seconds={timeout_text}"
        if timeout_text.startswith("-")
        else "--timeout-seconds"
    )
    timeout_value = None if timeout_text.startswith("-") else timeout_text
    command = [
        sys.executable,
        "-m",
        "a_share_ai.cli",
        "probe-daily-research-service",
        "--base-url",
        "http://127.0.0.1:8765",
        timeout_argument,
    ]
    if timeout_value is not None:
        command.append(timeout_value)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "invalid"
    assert report["decision_ready"] is False
    assert report["issues"] == [
        {"code": "TIMEOUT_INVALID", "message": "timeout-seconds must be positive"}
    ]
