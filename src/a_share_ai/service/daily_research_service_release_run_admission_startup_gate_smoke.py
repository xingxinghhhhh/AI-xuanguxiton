"""Run one controlled E2E smoke check through the Node80 startup gate."""

from __future__ import annotations

import json
import math
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_probe import (
    DailyResearchServiceProbeError,
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)
from .daily_research_service_release_run_admission_startup_gate import (
    DailyResearchServiceReleaseRunAdmissionStartupGateError,
    check_daily_research_service_release_run_admission_startup_gate,
)
from .daily_research_service_run import _stop_process

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION = (
    "daily-research-service-release-run-admission-startup-gate-smoke-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME = (
    "daily_research_service_release_run_admission_startup_gate_smoke_report.json"
)
_MAX_TIMEOUT_SECONDS = 300.0
_REPORT_FIELDS = {
    "smoke_version",
    "admission_path",
    "admission_sha256",
    "admission_report_path",
    "admission_report_sha256",
    "admission_audit_path",
    "admission_audit_sha256",
    "release_manifest_path",
    "release_manifest_sha256",
    "release_report_path",
    "release_report_sha256",
    "release_audit_report_path",
    "release_audit_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "gate_status",
    "gate_ready",
    "startup_status",
    "service_started",
    "probe_status",
    "probe_exit_code",
    "stop_status",
    "service_stopped",
    "port_released",
    "smoke_status",
    "smoke_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(ValueError):
    """A sanitized smoke configuration or output failure."""

    def __init__(self, code: str, message: str, *, configuration: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.configuration = configuration


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _timeout(value: Any, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > _MAX_TIMEOUT_SECONDS
    ):
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(
            "TIMEOUT_INVALID", f"{label} must be positive and finite", configuration=True
        )
    return float(value)


def _rooted_dir(path: Path, *, root: Path) -> Path:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir escapes artifact root", configuration=True
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(
            "OUTPUT_DIR_INVALID", "output-dir is invalid", configuration=True
        )
    return resolved


def _port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    address = (host, port, 0, 0) if family == socket.AF_INET6 else (host, port)
    with socket.socket(family, socket.SOCK_STREAM) as probe_socket:
        try:
            probe_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe_socket.bind(address)
        except OSError:
            return False
    return True


def _base_report(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "smoke_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION,
        "admission_path": summary["admission_path"],
        "admission_sha256": summary["admission_sha256"],
        "admission_report_path": summary["admission_report_path"],
        "admission_report_sha256": summary["admission_report_sha256"],
        "admission_audit_path": summary["admission_audit_path"],
        "admission_audit_sha256": summary["admission_audit_sha256"],
        "release_manifest_path": summary["release_manifest_path"],
        "release_manifest_sha256": summary["release_manifest_sha256"],
        "release_report_path": summary["release_report_path"],
        "release_report_sha256": summary["release_report_sha256"],
        "release_audit_report_path": summary["release_audit_report_path"],
        "release_audit_report_sha256": summary["release_audit_report_sha256"],
        "symbol": summary["symbol"],
        "as_of": summary["as_of"],
        "evaluation_at": summary["evaluation_at"],
        "gate_status": summary["status"],
        "gate_ready": summary["status"] == "ready",
        "startup_status": "not_started",
        "service_started": False,
        "probe_status": "not_started",
        "probe_exit_code": None,
        "stop_status": "not_attempted",
        "service_stopped": False,
        "port_released": False,
        "smoke_status": summary["status"],
        "smoke_ready": False,
        "issues": list(summary["issues"]),
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> None:
    canonical = dict(report)
    canonical["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(
            output_dir
            / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
            _json_bytes(report),
        )
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(
            "OUTPUT_UNAVAILABLE", "smoke report output is unavailable"
        ) from exc


def _command(
    *,
    admission_path: Path,
    admission_report_path: Path,
    admission_audit_path: Path,
    release_manifest_path: Path,
    release_report_path: Path,
    release_audit_report_path: Path,
    artifact_root: Path,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "a_share_ai.cli",
        "serve-research-receipt",
        "--daily-smoke-admission",
        str(admission_path),
        "--daily-smoke-admission-report",
        str(admission_report_path),
        "--daily-smoke-admission-audit-report",
        str(admission_audit_path),
        "--daily-release-manifest",
        str(release_manifest_path),
        "--daily-release-report",
        str(release_report_path),
        "--daily-release-audit-report",
        str(release_audit_report_path),
        "--artifact-root",
        str(artifact_root),
    ]


def _finish(report: dict[str, Any], *, output_dir: Path, status: str) -> tuple[dict[str, Any], int]:
    report["smoke_status"] = status
    report["smoke_ready"] = status == "ready"
    report["decision_ready"] = False
    _write_report(report, output_dir=output_dir)
    return report, 0 if report["smoke_ready"] else 1


def run_daily_research_service_release_run_admission_startup_gate_smoke(
    *,
    admission_path: Path,
    admission_report_path: Path,
    admission_audit_path: Path,
    release_manifest_path: Path,
    release_report_path: Path,
    release_audit_report_path: Path,
    artifact_root: Path,
    startup_timeout_seconds: float,
    probe_timeout_seconds: float,
    output_dir: Path,
) -> tuple[dict[str, Any], int]:
    """Run one Node80-gated loopback service smoke test and write its receipt."""

    startup_timeout = _timeout(startup_timeout_seconds, label="startup-timeout-seconds")
    probe_timeout = _timeout(probe_timeout_seconds, label="probe-timeout-seconds")
    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    output = _rooted_dir(output_dir, root=root)
    try:
        summary, gate_code, startup = (
            check_daily_research_service_release_run_admission_startup_gate(
                admission_path=admission_path,
                admission_report_path=admission_report_path,
                admission_audit_path=admission_audit_path,
                release_manifest_path=release_manifest_path,
                release_report_path=release_report_path,
                release_audit_report_path=release_audit_report_path,
                artifact_root=root,
            )
        )
    except DailyResearchServiceReleaseRunAdmissionStartupGateError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError(
            exc.code, str(exc), configuration=exc.configuration
        ) from exc
    report = _base_report(summary)
    if gate_code != 0 or startup is None:
        return _finish(report, output_dir=output, status=summary["status"])
    host = startup.gate_config.host
    port = startup.gate_config.port
    if host not in {"127.0.0.1", "::1"} or isinstance(port, bool) or not isinstance(port, int):
        report["issues"] = [_issue("STARTUP_INVALID", "startup endpoint is invalid")]
        return _finish(report, output_dir=output, status="failed")

    process: subprocess.Popen[bytes] | None = None
    uncontrolled_exit = False
    try:
        process = subprocess.Popen(
            _command(
                admission_path=admission_path,
                admission_report_path=admission_report_path,
                admission_audit_path=admission_audit_path,
                release_manifest_path=release_manifest_path,
                release_report_path=release_report_path,
                release_audit_report_path=release_audit_report_path,
                artifact_root=root,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        report["service_started"] = process.poll() is None
        report["startup_status"] = "ready" if report["service_started"] else "failed"
        if not report["service_started"]:
            report["issues"] = [_issue("SERVICE_EXITED", "service process exited unexpectedly")]
        else:
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    uncontrolled_exit = True
                    report["probe_status"] = "service_exited"
                    report["issues"] = [_issue("SERVICE_EXITED", "service process exited")]
                    break
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    probe = probe_daily_research_service(
                        base_url=(f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"),
                        timeout_seconds=min(probe_timeout, remaining),
                    )
                except DailyResearchServiceProbeError as exc:
                    if exc.code == "SERVICE_UNAVAILABLE":
                        time.sleep(0.05)
                        continue
                    report["probe_status"] = "failed"
                    report["issues"] = [_issue(exc.code, str(exc))]
                    break
                report["probe_exit_code"] = daily_research_service_probe_exit_code(probe)
                if report["probe_exit_code"] == 0:
                    if process.poll() is not None:
                        uncontrolled_exit = True
                        report["probe_status"] = "service_exited"
                        report["issues"] = [_issue("SERVICE_EXITED", "service exited before stop")]
                    else:
                        report["probe_status"] = "ready"
                    break
                issues = probe.get("issues")
                first_issue = issues[0] if isinstance(issues, list) and issues else None
                if (
                    probe.get("status") == "invalid"
                    and isinstance(first_issue, Mapping)
                    and first_issue.get("code") == "SERVICE_UNAVAILABLE"
                ):
                    time.sleep(0.05)
                    continue
                report["probe_status"] = "failed"
                report["issues"] = issues if isinstance(issues, list) else []
                break
            else:
                report["probe_status"] = "timeout"
                report["issues"] = [_issue("PROBE_TIMEOUT", "service probe timed out")]
    except OSError:
        report["startup_status"] = "failed"
        report["issues"] = [_issue("SERVICE_START_FAILED", "service process could not start")]
    finally:
        if process is not None:
            stopped, exited_before_stop = _stop_process(process)
            report["service_stopped"] = stopped
            if exited_before_stop:
                uncontrolled_exit = True
                report["stop_status"] = "uncontrolled_exit"
            elif stopped:
                report["stop_status"] = "controlled"
            else:
                report["stop_status"] = "failed"
            report["port_released"] = _port_is_free(host, port)

    if uncontrolled_exit:
        report["issues"] = report["issues"] or [_issue("SERVICE_EXITED", "service exited")]
    elif report["stop_status"] == "failed" or not report["service_stopped"]:
        report["issues"] = report["issues"] or [
            _issue("SERVICE_STOP_FAILED", "service did not stop")
        ]
    elif not report["port_released"]:
        report["issues"] = [_issue("SERVICE_PORT_NOT_RELEASED", "service port was not released")]
    elif report["probe_status"] == "ready" and report["probe_exit_code"] == 0:
        return _finish(report, output_dir=output, status="ready")
    else:
        report["issues"] = report["issues"] or [_issue("SMOKE_NOT_READY", "smoke run is not ready")]
    return _finish(report, output_dir=output, status="failed")


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupGateSmokeError",
    "run_daily_research_service_release_run_admission_startup_gate_smoke",
]
