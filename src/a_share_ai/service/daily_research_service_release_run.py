"""Run one audited release package once and emit a sanitized run receipt."""

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
from .daily_research_service_probe import (
    DailyResearchServiceProbeError,
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)
from .daily_research_service_release import DAILY_RESEARCH_SERVICE_RELEASE_VERSION
from .daily_research_service_release_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION,
    DailyResearchServiceReleaseStartupError,
    load_daily_research_service_release_startup,
)
from .daily_research_service_run import _stop_process

DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION = "daily-research-service-release-run-v1"
DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME = "daily_research_service_release_run_report.json"


class DailyResearchServiceReleaseRunError(ValueError):
    """A sanitized release-run configuration or output failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


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
        raise DailyResearchServiceReleaseRunError("TIMEOUT_INVALID", f"{label} must be positive")
    return float(value)


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _base_report(
    *, root: Path, manifest_path: Path, report_path: Path, audit_path: Path
) -> dict[str, Any]:
    return {
        "run_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION,
        "release_version": DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
        "startup_version": DAILY_RESEARCH_SERVICE_RELEASE_STARTUP_VERSION,
        "release_manifest_path": _safe_relative(manifest_path, root=root),
        "release_manifest_sha256": _file_sha(manifest_path, root=root),
        "release_report_path": _safe_relative(report_path, root=root),
        "release_report_sha256": _file_sha(report_path, root=root),
        "release_audit_report_path": _safe_relative(audit_path, root=root),
        "release_audit_report_sha256": _file_sha(audit_path, root=root),
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "startup_status": "blocked",
        "startup_ready": False,
        "probe_status": "not_started",
        "probe_exit_code": None,
        "stop_status": "not_attempted",
        "service_stopped": False,
        "run_status": "blocked",
        "run_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }


def _write_report(report: dict[str, Any], *, output_dir: Path) -> Path:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME
        canonical = dict(report)
        canonical["output_sha256"] = None
        report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
        write_atomic(output_path, _json_bytes(report))
    except OSError as exc:
        raise DailyResearchServiceReleaseRunError(
            "OUTPUT_UNAVAILABLE", "release run report output is unavailable"
        ) from exc
    return output_path


def _service_url(host: str, port: int) -> str:
    if ":" in host:
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"


def _command(*, startup: Any) -> list[str]:
    return [
        sys.executable,
        "-m",
        "a_share_ai.cli",
        "serve-research-receipt",
        "--daily-release-manifest",
        str(startup.manifest_path),
        "--daily-release-report",
        str(startup.report_path),
        "--daily-release-audit-report",
        str(startup.audit_report_path),
        "--artifact-root",
        str(startup.artifact_root),
    ]


def run_daily_research_service_release(
    *,
    manifest_path: Path,
    report_path: Path,
    audit_report_path: Path,
    artifact_root: Path,
    startup_timeout_seconds: float,
    probe_timeout_seconds: float,
    output_dir: Path,
) -> tuple[dict[str, Any], int]:
    """Run a ready release once, probe it, stop it, and write its receipt."""

    startup_timeout = _valid_timeout(startup_timeout_seconds, label="startup-timeout-seconds")
    probe_timeout = _valid_timeout(probe_timeout_seconds, label="probe-timeout-seconds")
    root = artifact_root.resolve()
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid"
        )
    try:
        output_dir.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", "output-dir escapes artifact root"
        ) from exc

    receipt = _base_report(
        root=root,
        manifest_path=manifest_path,
        report_path=report_path,
        audit_path=audit_report_path,
    )
    try:
        startup = load_daily_research_service_release_startup(
            manifest_path=manifest_path,
            report_path=report_path,
            audit_report_path=audit_report_path,
            artifact_root=root,
        )
    except DailyResearchServiceReleaseStartupError as exc:
        receipt["issues"] = [_issue(exc.code, str(exc))]
        _write_report(receipt, output_dir=output_dir)
        return receipt, 1

    receipt.update(
        {
            "release_manifest_path": startup.manifest_path.relative_to(root).as_posix(),
            "release_manifest_sha256": startup.manifest_sha256,
            "release_report_path": startup.report_path.relative_to(root).as_posix(),
            "release_report_sha256": startup.report_sha256,
            "release_audit_report_path": startup.audit_report_path.relative_to(root).as_posix(),
            "release_audit_report_sha256": startup.audit_sha256,
            "symbol": startup.symbol,
            "as_of": startup.as_of,
            "evaluation_at": startup.evaluation_at,
            "startup_status": "ready",
            "startup_ready": startup.startup_ready,
        }
    )

    process: subprocess.Popen[bytes] | None = None
    uncontrolled_exit = False
    deadline = time.monotonic() + startup_timeout
    try:
        process = subprocess.Popen(
            _command(startup=startup),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = _service_url(startup.gate_config.host, startup.gate_config.port)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                receipt["probe_status"] = "service_exited"
                receipt["issues"] = [
                    _issue("SERVICE_EXITED", "service process exited unexpectedly")
                ]
                break
            remaining = max(0.01, deadline - time.monotonic())
            try:
                probe = probe_daily_research_service(
                    base_url=base_url, timeout_seconds=min(probe_timeout, remaining)
                )
            except DailyResearchServiceProbeError as exc:
                if exc.code == "SERVICE_UNAVAILABLE" and time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                receipt["probe_status"] = (
                    "timeout" if exc.code == "SERVICE_UNAVAILABLE" else "failed"
                )
                receipt["issues"] = [_issue(exc.code, str(exc))]
                break
            receipt["probe_exit_code"] = daily_research_service_probe_exit_code(probe)
            if receipt["probe_exit_code"] == 0:
                if process.poll() is not None:
                    uncontrolled_exit = True
                    receipt["probe_status"] = "service_exited"
                    receipt["issues"] = [
                        _issue("SERVICE_EXITED", "service process exited before controlled stop")
                    ]
                    break
                receipt["probe_status"] = "ready"
                receipt["issues"] = []
                break
            issues = probe.get("issues")
            first_issue = issues[0] if isinstance(issues, list) and issues else None
            if (
                probe.get("status") == "invalid"
                and isinstance(first_issue, Mapping)
                and first_issue.get("code") == "SERVICE_UNAVAILABLE"
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
                continue
            receipt["probe_status"] = (
                "timeout"
                if isinstance(first_issue, Mapping)
                and first_issue.get("code") == "SERVICE_UNAVAILABLE"
                else "failed"
            )
            receipt["issues"] = issues if isinstance(issues, list) else []
            break
        else:
            receipt["probe_status"] = "timeout"
            receipt["issues"] = [_issue("STARTUP_TIMEOUT", "service did not become ready")]
    except OSError:
        receipt["startup_status"] = "failed"
        receipt["issues"] = [_issue("SERVICE_START_FAILED", "service process could not start")]
    finally:
        if process is not None:
            stopped, exited_before_stop = _stop_process(process)
            receipt["service_stopped"] = stopped
            if exited_before_stop:
                uncontrolled_exit = True
                receipt["stop_status"] = "uncontrolled_exit"
            elif stopped:
                receipt["stop_status"] = "controlled"
            else:
                receipt["stop_status"] = "failed"

    if uncontrolled_exit:
        receipt["run_status"] = "failed"
        receipt["run_ready"] = False
        if not receipt["issues"]:
            receipt["issues"] = [
                _issue("SERVICE_EXITED", "service process exited before controlled stop")
            ]
    elif receipt["stop_status"] == "failed":
        receipt["run_status"] = "failed"
        receipt["run_ready"] = False
        receipt["issues"] = [_issue("SERVICE_STOP_FAILED", "service process did not stop")]
    else:
        receipt["run_ready"] = (
            receipt["startup_ready"] is True
            and receipt["probe_status"] == "ready"
            and receipt["probe_exit_code"] == 0
            and receipt["stop_status"] == "controlled"
            and receipt["service_stopped"] is True
            and receipt["decision_ready"] is False
        )
        receipt["run_status"] = "ready" if receipt["run_ready"] else "failed"
        if not receipt["run_ready"] and not receipt["issues"]:
            receipt["issues"] = [_issue("RUN_NOT_READY", "release service run is not ready")]

    _write_report(receipt, output_dir=output_dir)
    return receipt, 0 if receipt["run_ready"] else 1


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_VERSION",
    "DailyResearchServiceReleaseRunError",
    "run_daily_research_service_release",
]
