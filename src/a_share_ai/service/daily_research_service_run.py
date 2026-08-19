"""Run one bounded, audited daily-research service session."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..market.replay import sha256_bytes, write_atomic
from .daily_research_service_launch_gate_startup import (
    DailyResearchServiceLaunchGateStartupConfig,
    DailyResearchServiceLaunchGateStartupError,
    load_daily_research_service_launch_gate_startup,
)
from .daily_research_service_probe import (
    DailyResearchServiceProbeError,
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)

DAILY_RESEARCH_SERVICE_RUN_VERSION = "daily-research-service-run-v1"
DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME = "daily_research_service_run_report.json"
_SHA256_LENGTH = 64
_RUN_FIELDS = {
    "run_version",
    "gate_path",
    "gate_sha256",
    "audit_path",
    "audit_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "startup_status",
    "probe_status",
    "probe_exit_code",
    "service_stopped",
    "run_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceRunError(ValueError):
    """A sanitized run configuration or execution failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _safe_relative(path: Path, *, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _file_sha(path: Path, *, root: Path) -> str | None:
    try:
        candidate = path.resolve()
        candidate.relative_to(root.resolve())
        return sha256_bytes(candidate.read_bytes())
    except (OSError, ValueError):
        return None


def _valid_timeout(value: Any, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise DailyResearchServiceRunError("TIMEOUT_INVALID", f"{label} must be positive")
    return float(value)


def _base_report(*, root: Path, gate_path: Path, audit_path: Path) -> dict[str, Any]:
    gate_relative = _safe_relative(gate_path, root=root)
    audit_relative = _safe_relative(audit_path, root=root)
    return {
        "run_version": DAILY_RESEARCH_SERVICE_RUN_VERSION,
        "gate_path": gate_relative,
        "gate_sha256": _file_sha(gate_path, root=root) if gate_relative is not None else None,
        "audit_path": audit_relative,
        "audit_sha256": _file_sha(audit_path, root=root) if audit_relative is not None else None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "startup_status": "invalid",
        "probe_status": None,
        "probe_exit_code": None,
        "service_stopped": False,
        "run_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _write_report(report: dict[str, Any], *, output_dir: Path) -> Path:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME
        canonical = dict(report)
        canonical["output_sha256"] = None
        report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
        write_atomic(output_path, _json_bytes(report))
    except OSError as exc:
        raise DailyResearchServiceRunError(
            "OUTPUT_UNAVAILABLE", "run report output is unavailable"
        ) from exc
    return output_path


def _service_url(host: str, port: int) -> str:
    if ":" in host:
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"


def _stop_process(process: subprocess.Popen[bytes]) -> tuple[bool, bool]:
    """Stop a process and report whether it exited before a stop was issued."""

    if process.poll() is not None:
        return False, True
    stop_signal_issued = False
    try:
        process.terminate()
        stop_signal_issued = True
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        if not stop_signal_issued and process.poll() is not None:
            return False, True
        try:
            process.kill()
            stop_signal_issued = True
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            if not stop_signal_issued and process.poll() is not None:
                return False, True
            return process.poll() is not None, False
    return process.poll() is not None, False


def _command(*, startup: DailyResearchServiceLaunchGateStartupConfig) -> list[str]:
    gate = startup.gate_config
    return [
        sys.executable,
        "-m",
        "a_share_ai.cli",
        "serve-research-receipt",
        "--daily-launch-gate",
        str(startup.gate_config.artifact_root / startup.audit_report["gate_path"]),
        "--daily-launch-gate-root",
        str(gate.artifact_root),
        "--daily-launch-gate-audit",
        str(startup.audit_path),
        "--artifact-root",
        str(gate.artifact_root),
    ]


def _copy_startup_metadata(
    report: dict[str, Any], startup: DailyResearchServiceLaunchGateStartupConfig
) -> None:
    gate_report = startup.gate_config.report
    report.update(
        {
            "gate_path": startup.audit_report["gate_path"],
            "gate_sha256": startup.audit_report["gate_sha256"],
            "audit_path": startup.audit_path.relative_to(
                startup.gate_config.artifact_root
            ).as_posix(),
            "audit_sha256": sha256_bytes(startup.audit_path.read_bytes()),
            "symbol": startup.audit_report["symbol"],
            "as_of": startup.audit_report["as_of"],
            "evaluation_at": startup.audit_report["evaluation_at"],
        }
    )
    if gate_report.get("symbol") != report["symbol"]:
        raise DailyResearchServiceRunError("CHAIN_MISMATCH", "startup symbol differs")


def run_daily_research_service(
    *,
    gate_path: Path,
    artifact_root: Path,
    audit_path: Path,
    startup_timeout_seconds: float,
    probe_timeout_seconds: float,
    output_dir: Path,
) -> tuple[dict[str, Any], int]:
    """Run one audited service process and return its report and CLI exit code."""

    startup_timeout = _valid_timeout(startup_timeout_seconds, label="startup-timeout-seconds")
    probe_timeout = _valid_timeout(probe_timeout_seconds, label="probe-timeout-seconds")
    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceRunError("ARTIFACT_ROOT_INVALID", "artifact root is invalid")
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceRunError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir escapes artifact root"
        ) from exc

    report = _base_report(root=root, gate_path=gate_path, audit_path=audit_path)
    try:
        startup = load_daily_research_service_launch_gate_startup(
            gate_path=gate_path, audit_path=audit_path, artifact_root=root
        )
    except DailyResearchServiceLaunchGateStartupError as exc:
        report["issues"] = [_issue(exc.code, str(exc))]
        report_path = _write_report(report, output_dir=output_dir)
        _ = report_path
        return report, 1

    _copy_startup_metadata(report, startup)
    if not startup.launch_ready:
        report["startup_status"] = "blocked"
        report["issues"] = [_issue("STARTUP_NOT_READY", "audited launch gate is not ready")]
        _write_report(report, output_dir=output_dir)
        return report, 1

    process: subprocess.Popen[bytes] | None = None
    abnormal_exit_before_stop = False
    deadline = time.monotonic() + startup_timeout
    try:
        process = subprocess.Popen(
            _command(startup=startup),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        report["startup_status"] = "ready"
        base_url = _service_url(startup.gate_config.host, startup.gate_config.port)
        probe_report: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                report["issues"] = [_issue("SERVICE_EXITED", "service process exited unexpectedly")]
                break
            remaining = max(0.01, deadline - time.monotonic())
            try:
                probe_report = probe_daily_research_service(
                    base_url=base_url, timeout_seconds=min(probe_timeout, remaining)
                )
            except DailyResearchServiceProbeError as exc:
                if exc.code == "SERVICE_UNAVAILABLE" and time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                report["issues"] = [_issue(exc.code, str(exc))]
                break
            report["probe_status"] = probe_report.get("status")
            report["probe_exit_code"] = daily_research_service_probe_exit_code(probe_report)
            if report["probe_exit_code"] == 0:
                if process.poll() is not None:
                    abnormal_exit_before_stop = True
                    report["issues"] = [_issue(
                        "SERVICE_EXITED", "service process exited before controlled stop"
                    )]
                    break
                report["issues"] = []
                break
            issues = probe_report.get("issues")
            report["issues"] = issues if isinstance(issues, list) else []
            if (
                probe_report.get("status") == "invalid"
                and report["issues"]
                and report["issues"][0].get("code") == "SERVICE_UNAVAILABLE"
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
                continue
            break
        else:
            report["issues"] = [_issue("STARTUP_TIMEOUT", "service did not become ready")]
        if report["probe_exit_code"] is None and report["issues"] == []:
            report["issues"] = [_issue("STARTUP_TIMEOUT", "service did not become ready")]
        report["run_ready"] = (
            report["startup_status"] == "ready"
            and report["probe_status"] == "ready"
            and report["probe_exit_code"] == 0
        )
    except OSError:
        report["issues"] = [_issue("SERVICE_START_FAILED", "service process could not start")]
    finally:
        if process is not None:
            stopped, exited_before_stop = _stop_process(process)
            abnormal_exit_before_stop = abnormal_exit_before_stop or exited_before_stop
            report["service_stopped"] = stopped

    if abnormal_exit_before_stop:
        report["run_ready"] = False
        if not report["issues"]:
            report["issues"] = [_issue(
                "SERVICE_EXITED", "service process exited before controlled stop"
            )]
    if report["run_ready"] and not report["service_stopped"]:
        report["run_ready"] = False
        report["issues"] = [_issue("SERVICE_STOP_FAILED", "service process did not stop")]
    if not report["run_ready"] and not report["issues"]:
        report["issues"] = [_issue("RUN_NOT_READY", "daily research service run is not ready")]
    _write_report(report, output_dir=output_dir)
    return report, 0 if report["run_ready"] and report["service_stopped"] else 1


__all__ = [
    "DAILY_RESEARCH_SERVICE_RUN_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RUN_VERSION",
    "DailyResearchServiceRunError",
    "run_daily_research_service",
]
