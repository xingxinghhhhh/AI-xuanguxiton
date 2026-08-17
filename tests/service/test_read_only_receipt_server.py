import json
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_final_receipt import (
    MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
)
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.read_only_receipt_probe import (
    probe_exit_code,
    probe_read_only_receipt_service,
)
from a_share_ai.service.read_only_receipt_server import (
    READ_ONLY_RECEIPT_SERVICE_VERSION,
    ReadOnlyReceiptServiceError,
    create_read_only_receipt_server,
    load_read_only_receipt_summary,
)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _write_self_hashed(path: Path, payload: dict[str, Any]) -> None:
    payload["output_sha256"] = None
    canonical = _json_bytes(payload)
    payload["output_sha256"] = sha256_bytes(canonical)
    path.write_bytes(_json_bytes(payload))


def _write_input_pair(tmp_path: Path, *, status: str = "ready") -> tuple[Path, Path]:
    ready = status == "ready"
    issues = [] if ready else [{"code": "STALE_INPUT", "message": r"C:\secret\token"}]
    root = tmp_path / "artifacts"
    root.mkdir()
    receipt_path = root / "final-receipt" / "market_aware_session_history_final_receipt.json"
    report_path = root / "final-receipt" / "market_aware_session_history_final_receipt_report.json"
    receipt_path.parent.mkdir(parents=True)
    receipt = {
        "admission_ready": ready,
        "audit_ready": True,
        "decision_ready": False,
        "first_as_of": "2026-08-10T00:00:00+00:00",
        "issues": issues,
        "last_as_of": "2026-08-11T00:00:00+00:00",
        "output_sha256": None,
        "package_count": 2,
        "receipt_ready": ready,
        "receipt_version": MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
        "render_ready": ready,
        "status": status,
        "symbol": "600000.SH",
    }
    _write_self_hashed(receipt_path, receipt)
    report = {
        **receipt,
        "artifact_root": ".",
        "inputs": {
            "admission": {"byte_count": 1, "path": "upstream/admission.json", "sha256": "a" * 64},
            "admission_report": {
                "byte_count": 1,
                "path": "upstream/admission-report.json",
                "sha256": "b" * 64,
            },
            "render_report": {
                "byte_count": 1,
                "path": "upstream/render-report.json",
                "sha256": "c" * 64,
            },
            "render_audit_report": {
                "byte_count": 1,
                "path": "upstream/render-audit-report.json",
                "sha256": "d" * 64,
            },
        },
        "receipt_sha256": sha256_bytes(receipt_path.read_bytes()),
    }
    _write_self_hashed(report_path, report)
    return receipt_path, report_path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_daily_admission_pair(
    tmp_path: Path, *, status: str = "ready"
) -> tuple[Path, Path, Path]:
    root = tmp_path / "daily-artifacts"
    root.mkdir()
    referenced = {
        "run_report.json": b'{"status":"ready"}\n',
        "run-audit-report.json": b'{"audit_ready":true}\n',
        "calendar.json": b'{"calendar_version":"fixture-v1"}\n',
        "calendar-report.json": b'{"status":"complete"}\n',
    }
    for name, raw in referenced.items():
        (root / name).write_bytes(raw)
    ready = status == "ready"
    payload = {
        "admission_version": "daily-research-admission-v1",
        "run_report_path": "run_report.json",
        "run_audit_report_path": "run-audit-report.json",
        "calendar_path": "calendar.json",
        "calendar_report_path": "calendar-report.json",
        "run_report_sha256": sha256_bytes(referenced["run_report.json"]),
        "run_audit_report_sha256": sha256_bytes(referenced["run-audit-report.json"]),
        "calendar_sha256": sha256_bytes(referenced["calendar.json"]),
        "calendar_report_sha256": sha256_bytes(referenced["calendar-report.json"]),
        "symbol": "600000.SH",
        "as_of": "2026-08-10T08:00:00+00:00",
        "evaluation_at": "2026-08-10T16:00:00+08:00",
        "expected_latest_trading_date": "2026-08-10" if ready else "2026-08-11",
        "status": status,
        "freshness_status": status,
        "audit_ready": ready,
        "analysis_input_ready": ready,
        "admission_ready": ready,
        "issues": [] if ready else [{"code": "RESEARCH_STALE", "message": r"C:\secret\token"}],
        "decision_ready": False,
        "output_sha256": None,
    }
    admission_path = root / "daily-admission.json"
    report_path = root / "daily-admission-report.json"
    _write_self_hashed(admission_path, dict(payload))
    _write_self_hashed(report_path, dict(payload))
    return admission_path, report_path, root


def _request(base_url: str, path: str, *, method: str = "GET") -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(base_url + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw.decode("utf-8")) if raw else {}


def test_ready_service_exposes_read_only_health_and_receipt_routes(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    server = create_read_only_receipt_server(
        receipt_path=receipt_path,
        receipt_report_path=report_path,
        artifact_root=tmp_path / "artifacts",
        port=_free_port(),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = _request(base_url, "/healthz")
        assert status == 200
        assert health["service_version"] == READ_ONLY_RECEIPT_SERVICE_VERSION
        assert health["decision_ready"] is False
        assert "final-receipt" not in json.dumps(health)

        status, ready = _request(base_url, "/readyz")
        assert status == 200
        assert ready["receipt_ready"] is True

        status, receipt = _request(base_url, "/v1/research/receipt")
        assert status == 200
        assert receipt["symbol"] == "600000.SH"

        status, not_found = _request(base_url, "/other")
        assert status == 404
        assert not_found == {"error": "not_found"}

        status, daily_not_found = _request(base_url, "/v1/research/daily-admission")
        assert status == 404
        assert daily_not_found == {"error": "not_found"}

        status, method_error = _request(base_url, "/healthz", method="POST")
        assert status == 405
        assert method_error == {}
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_stale_service_is_live_but_not_ready_and_redacts_issue_paths(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path, status="stale")
    summary = load_read_only_receipt_summary(
        receipt_path=receipt_path,
        receipt_report_path=report_path,
        artifact_root=tmp_path / "artifacts",
    )
    assert summary["receipt_ready"] is False
    assert summary["issues"] == [{"code": "STALE_INPUT", "message": "<redacted-path>"}]

    server = create_read_only_receipt_server(
        receipt_path=receipt_path,
        receipt_report_path=report_path,
        artifact_root=tmp_path / "artifacts",
        port=_free_port(),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        status, _ = _request(f"http://127.0.0.1:{server.server_address[1]}", "/healthz")
        assert status == 200
        status, body = _request(f"http://127.0.0.1:{server.server_address[1]}", "/readyz")
        assert status == 503
        assert body["status"] == "stale"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize(
    "daily_status, expected_ready", [("ready", True), ("stale", False), ("blocked", False)]
)
def test_daily_admission_routes_and_ready_gate(
    tmp_path: Path, daily_status: str, expected_ready: bool
) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    daily_path, daily_report_path, daily_root = _write_daily_admission_pair(
        tmp_path, status=daily_status
    )
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
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = _request(base_url, "/healthz")
        assert status == 200
        assert "daily_admission" not in health
        assert health["receipt_ready"] is expected_ready
        if expected_ready:
            assert health["status"] == "ready"
        else:
            assert health["status"] == daily_status
            assert health["issues"][-1] == {
                "code": "DAILY_ADMISSION_NOT_READY",
                "message": f"daily admission status is {daily_status}",
            }
        status, ready = _request(base_url, "/readyz")
        assert status == (200 if expected_ready else 503)
        assert health == ready
        status, daily = _request(base_url, "/v1/research/daily-admission")
        assert status == 200
        assert set(daily) == {
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
        assert daily["decision_ready"] is False
        assert "daily-admission" not in json.dumps(daily)
        status, receipt = _request(base_url, "/v1/research/receipt")
        assert status == 200
        assert "daily_admission" not in receipt
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize(
    "daily_status, expected_probe_status",
    [("ready", "ready"), ("stale", "blocked"), ("blocked", "blocked")],
)
def test_daily_admission_service_remains_compatible_with_node54_probe(
    tmp_path: Path, daily_status: str, expected_probe_status: str
) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    daily_path, daily_report_path, daily_root = _write_daily_admission_pair(
        tmp_path, status=daily_status
    )
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
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        probe = probe_read_only_receipt_service(base_url=base_url)
        assert probe_exit_code(probe) == (0 if expected_probe_status == "ready" else 1)
        assert probe["status"] == expected_probe_status
        assert probe["ready_http_status"] == (200 if expected_probe_status == "ready" else 503)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_daily_admission_validation_is_fail_closed(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    daily_path, daily_report_path, daily_root = _write_daily_admission_pair(tmp_path)
    with pytest.raises(ReadOnlyReceiptServiceError, match="complete set"):
        create_read_only_receipt_server(
            receipt_path=receipt_path,
            receipt_report_path=report_path,
            artifact_root=tmp_path / "artifacts",
            daily_admission_path=daily_path,
            port=_free_port(),
        )

    daily_path.write_bytes(
        daily_path.read_bytes().replace(b"daily-research-admission-v1", b"tampered-admission-v1")
    )
    with pytest.raises(ReadOnlyReceiptServiceError):
        create_read_only_receipt_server(
            receipt_path=receipt_path,
            receipt_report_path=report_path,
            artifact_root=tmp_path / "artifacts",
            daily_admission_path=daily_path,
            daily_admission_report_path=daily_report_path,
            daily_admission_root=daily_root,
            port=_free_port(),
        )


def test_cli_daily_admission_options_must_be_paired(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    from a_share_ai.cli import main

    code = main(
        [
            "serve-research-receipt",
            "--receipt",
            str(receipt_path),
            "--receipt-report",
            str(report_path),
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--daily-admission",
            str(tmp_path / "missing-admission.json"),
        ]
    )
    assert code == 2


@pytest.mark.parametrize("mutation", ["outside", "tampered", "public"])
def test_service_fails_closed_before_listening(tmp_path: Path, mutation: str) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    root = tmp_path / "artifacts"
    if mutation == "outside":
        outside = tmp_path / "outside-receipt.json"
        outside.write_bytes(receipt_path.read_bytes())
        receipt_path = outside
    elif mutation == "tampered":
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        payload["symbol"] = "tampered"
        receipt_path.write_bytes(_json_bytes(payload))
    else:
        with pytest.raises(ReadOnlyReceiptServiceError, match="127.0.0.1 or ::1"):
            create_read_only_receipt_server(
                receipt_path=receipt_path,
                receipt_report_path=report_path,
                artifact_root=root,
                host="0.0.0.0",
                port=_free_port(),
            )
        return

    with pytest.raises(ReadOnlyReceiptServiceError):
        create_read_only_receipt_server(
            receipt_path=receipt_path,
            receipt_report_path=report_path,
            artifact_root=root,
            port=_free_port(),
        )


def test_cli_starts_service_and_serves_local_http(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    port = _free_port()
    command = [
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
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(30):
            try:
                status, body = _request(f"http://127.0.0.1:{port}", "/readyz")
                if status == 200:
                    assert body["receipt_ready"] is True
                    break
            except (urllib.error.URLError, TimeoutError):
                pass
        else:
            stderr = process.stderr.read() if process.stderr else ""
            pytest.fail(f"service did not start: {stderr}")
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_cli_starts_service_with_daily_admission(tmp_path: Path) -> None:
    receipt_path, report_path = _write_input_pair(tmp_path)
    daily_path, daily_report_path, daily_root = _write_daily_admission_pair(tmp_path)
    port = _free_port()
    command = [
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
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(30):
            try:
                status, body = _request(f"http://127.0.0.1:{port}", "/v1/research/daily-admission")
                if status == 200:
                    assert body["admission_ready"] is True
                    break
            except (urllib.error.URLError, TimeoutError):
                pass
        else:
            stderr = process.stderr.read() if process.stderr else ""
            pytest.fail(f"daily admission service did not start: {stderr}")
    finally:
        process.terminate()
        process.wait(timeout=5)
