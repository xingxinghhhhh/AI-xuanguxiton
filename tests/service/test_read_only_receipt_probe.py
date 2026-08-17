import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.service.read_only_receipt_probe import (
    ReadOnlyReceiptProbeError,
    probe_exit_code,
    probe_read_only_receipt_service,
)
from a_share_ai.service.read_only_receipt_server import create_read_only_receipt_server
from tests.service.test_read_only_receipt_server import _free_port, _write_input_pair


def _start_node53(
    tmp_path: Path, *, status: str = "ready"
) -> tuple[HTTPServer, threading.Thread, str]:
    receipt_path, report_path = _write_input_pair(tmp_path, status=status)
    server = create_read_only_receipt_server(
        receipt_path=receipt_path,
        receipt_report_path=report_path,
        artifact_root=tmp_path / "artifacts",
        port=_free_port(),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _start_fake_server(
    *, payload: Any = None, status: int = 200, redirect: bool = False
) -> tuple[HTTPServer, threading.Thread, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if redirect:
                self.send_response(302)
                self.send_header("Location", "http://example.com/")
                self.end_headers()
                return
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


def _valid_summary() -> dict[str, Any]:
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


def test_probe_ready_service_returns_success(tmp_path: Path) -> None:
    server, thread, base_url = _start_node53(tmp_path)
    try:
        report = probe_read_only_receipt_service(base_url=base_url)
        assert probe_exit_code(report) == 0
        assert report["status"] == "ready"
        assert report["health_http_status"] == 200
        assert report["ready_http_status"] == 200
        assert report["receipt_http_status"] == 200
        assert report["decision_ready"] is False
        assert [item["name"] for item in report["checks"]] == [
            "healthz",
            "readyz",
            "receipt",
        ]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_probe_stale_service_is_not_ready(tmp_path: Path) -> None:
    server, thread, base_url = _start_node53(tmp_path, status="stale")
    try:
        report = probe_read_only_receipt_service(base_url=base_url)
        assert probe_exit_code(report) == 1
        assert report["status"] == "blocked"
        assert report["ready_http_status"] == 503
        assert report["receipt_ready"] is False
        assert report["issues"] == [
            {"code": "SERVICE_NOT_READY", "message": "receipt status is stale"}
        ]
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
def test_probe_rejects_non_loopback_or_ambiguous_urls(url: str) -> None:
    with pytest.raises(ReadOnlyReceiptProbeError, match="base URL"):
        probe_read_only_receipt_service(base_url=url)


def test_probe_rejects_redirect_and_unknown_response_fields() -> None:
    redirect_server, redirect_thread, redirect_url = _start_fake_server(redirect=True)
    try:
        report = probe_read_only_receipt_service(base_url=redirect_url)
        assert probe_exit_code(report) == 1
        assert report["issues"][0]["code"] == "REDIRECT_REJECTED"
    finally:
        redirect_server.shutdown()
        redirect_thread.join(timeout=3)
        redirect_server.server_close()

    payload = _valid_summary()
    payload["unexpected"] = True
    fake_server, fake_thread, fake_url = _start_fake_server(payload=payload)
    try:
        report = probe_read_only_receipt_service(base_url=fake_url)
        assert report["status"] == "invalid"
        assert report["issues"][0]["code"] == "RESPONSE_INVALID"
    finally:
        fake_server.shutdown()
        fake_thread.join(timeout=3)
        fake_server.server_close()


def test_probe_rejects_decision_ready_and_absolute_path_response() -> None:
    payload = _valid_summary()
    payload["decision_ready"] = True
    server, thread, url = _start_fake_server(payload=payload)
    try:
        report = probe_read_only_receipt_service(base_url=url)
        assert report["issues"][0]["code"] == "DECISION_GATE_INVALID"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    payload = _valid_summary()
    payload["issues"] = [{"code": "LEAK", "message": r"C:\secret\key"}]
    server, thread, url = _start_fake_server(payload=payload)
    try:
        report = probe_read_only_receipt_service(base_url=url)
        assert report["issues"][0]["code"] == "SENSITIVE_OUTPUT"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_probe_unavailable_service_is_failure(tmp_path: Path) -> None:
    report = probe_read_only_receipt_service(
        base_url=f"http://127.0.0.1:{_free_port()}", timeout_seconds=0.2
    )
    assert probe_exit_code(report) == 1
    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "SERVICE_UNAVAILABLE"


def test_probe_cli_runs_against_node53_cli(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    port = _free_port()
    server_process = subprocess.Popen(
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
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "a_share_ai.cli",
                "probe-research-receipt-service",
                "--base-url",
                f"http://127.0.0.1:{port}",
                "--timeout-seconds",
                "2",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.fail(f"probe failed: {result.stdout}\n{result.stderr}")
        report = json.loads(result.stdout)
        assert report["status"] == "ready"
        assert report["decision_ready"] is False
        assert "C:\\" not in result.stdout
    finally:
        server_process.terminate()
        server_process.wait(timeout=5)
