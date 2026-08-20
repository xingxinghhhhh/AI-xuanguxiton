"""Run one controlled, loopback-only smoke check for the Node75 startup path."""

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
from .daily_research_service_release import DAILY_RESEARCH_SERVICE_RELEASE_VERSION
from .daily_research_service_release_run_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
)
from .daily_research_service_release_run_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
)
from .daily_research_service_release_run_admission_startup import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION,
    DailyResearchServiceReleaseRunAdmissionStartupError,
    run_daily_research_service_release_run_admission_startup,
)
from .daily_research_service_release_startup import (
    DailyResearchServiceReleaseStartupError,
    load_daily_research_service_release_startup,
)
from .daily_research_service_run import _stop_process

DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION = (
    "daily-research-service-release-run-admission-startup-smoke-v1"
)
DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME = (
    "daily_research_service_release_run_admission_startup_smoke_report.json"
)
_MAX_TIMEOUT_SECONDS = 300.0
_REPORT_FIELDS = {
    "run_version",
    "startup_version",
    "admission_version",
    "audit_version",
    "release_version",
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
    "release_audit_path",
    "release_audit_sha256",
    "preflight_report_path",
    "preflight_report_sha256",
    "startup_report_path",
    "startup_report_sha256",
    "symbol",
    "as_of",
    "evaluation_at",
    "admission_status",
    "audit_status",
    "startup_status",
    "startup_ready",
    "service_started",
    "probe_status",
    "probe_exit_code",
    "stop_status",
    "service_stopped",
    "status",
    "run_ready",
    "issues",
    "decision_ready",
    "output_sha256",
}


class DailyResearchServiceReleaseRunAdmissionStartupSmokeError(ValueError):
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
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeError(
            "TIMEOUT_INVALID", f"{label} must be positive and finite", configuration=True
        )
    return float(value)


def _rooted_dir(path: Path, *, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeError(
            "PATH_OUTSIDE_ARTIFACT_ROOT", f"{label} is outside artifact root", configuration=True
        ) from exc
    if resolved.exists() and not resolved.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeError(
            "OUTPUT_DIR_INVALID", f"{label} is invalid", configuration=True
        )
    return resolved


def _safe_meta(path: Path, *, root: Path) -> tuple[str | None, str | None]:
    try:
        candidate = path.resolve()
        relative = candidate.relative_to(root).as_posix()
        if not candidate.is_file():
            return None, None
        return relative, sha256_bytes(candidate.read_bytes())
    except (OSError, ValueError):
        return None, None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _validate_node75_report(
    path: Path,
    *,
    expected_mode: str,
    expected_inputs: Mapping[str, Path],
    root: Path,
) -> dict[str, Any] | None:
    payload = _read_json(path)
    if payload is None or set(payload) != {
        "startup_version",
        "admission_version",
        "audit_version",
        "release_version",
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
        "release_audit_path",
        "release_audit_sha256",
        "symbol",
        "as_of",
        "evaluation_at",
        "admission_status",
        "admission_ready",
        "audit_status",
        "audit_ready",
        "startup_status",
        "startup_ready",
        "mode",
        "service_started",
        "status",
        "issues",
        "decision_ready",
        "output_sha256",
    }:
        return None
    declared = payload.get("output_sha256")
    canonical = dict(payload)
    canonical["output_sha256"] = None
    if not isinstance(declared, str) or declared != sha256_bytes(_json_bytes(canonical)):
        return None
    if payload["startup_version"] != DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION:
        return None
    expected_versions = {
        "admission_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_VERSION,
        "audit_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_VERSION,
        "release_version": DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
    }
    for field, expected in expected_versions.items():
        if payload["status"] == "ready" and payload[field] != expected:
            return None
        if (
            payload["status"] != "ready"
            and payload[field] is not None
            and payload[field] != expected
        ):
            return None
    if payload["mode"] != expected_mode or payload["decision_ready"] is not False:
        return None
    for field in ("admission_ready", "audit_ready", "startup_ready", "service_started"):
        if not isinstance(payload[field], bool):
            return None
    if payload["status"] not in {"ready", "blocked", "failed", "invalid"}:
        return None
    if payload["startup_status"] not in {"ready", "blocked", "failed", "invalid"}:
        return None
    if payload["admission_status"] not in {"ready", "blocked", "failed", "invalid"}:
        return None
    if payload["audit_status"] not in {"ready", "blocked", "failed", "invalid"}:
        return None
    issues = payload["issues"]
    if not isinstance(issues, list) or any(
        not isinstance(item, Mapping)
        or set(item) != {"code", "message"}
        or not isinstance(item["code"], str)
        or not isinstance(item["message"], str)
        for item in issues
    ):
        return None
    if payload["status"] == "ready":
        if (
            payload["admission_status"] != "ready"
            or payload["audit_status"] != "ready"
            or payload["admission_ready"] is not True
            or payload["audit_ready"] is not True
            or payload["startup_status"] != "ready"
            or payload["startup_ready"] is not True
            or bool(issues)
        ):
            return None
    elif (
        not issues
        or payload["startup_ready"] is not False
        or payload["service_started"] is not False
        or payload["startup_status"] != payload["status"]
    ):
        return None
    if expected_mode == "check_only" and payload["service_started"] is not False:
        return None
    if expected_mode == "serve" and payload["status"] == "ready":
        if payload["service_started"] is not True:
            return None
    field_map = {
        "admission": "admission_path",
        "admission_report": "admission_report_path",
        "admission_audit": "admission_audit_path",
        "release_manifest": "release_manifest_path",
        "release_report": "release_report_path",
        "release_audit": "release_audit_path",
    }
    if payload["status"] == "ready":
        for path_field in field_map.values():
            sha_field = path_field.replace("_path", "_sha256")
            if not isinstance(payload[path_field], str) or not payload[path_field].strip():
                return None
            if not isinstance(payload[sha_field], str) or not payload[sha_field].strip():
                return None
    for key, path_value in expected_inputs.items():
        path_field = field_map[key]
        sha_field = path_field.replace("_path", "_sha256")
        relative, digest = _safe_meta(path_value, root=root)
        if payload[path_field] is None and payload[sha_field] is None:
            if payload["status"] == "ready":
                return None
            continue
        if relative is None or digest is None:
            return None
        if payload[path_field] != relative or payload[sha_field] != digest:
            return None
    if expected_mode == "serve" and payload["status"] == "ready":
        if payload["startup_status"] != "ready" or payload["startup_ready"] is not True:
            return None
    if expected_mode == "check_only" and payload["status"] == "ready":
        if payload["startup_ready"] is not True:
            return None
    return payload


def _base_report(*, root: Path, inputs: Mapping[str, Path], preflight_path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "run_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION,
        "startup_version": DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_VERSION,
        "admission_version": None,
        "audit_version": None,
        "release_version": None,
        "admission_path": None,
        "admission_sha256": None,
        "admission_report_path": None,
        "admission_report_sha256": None,
        "admission_audit_path": None,
        "admission_audit_sha256": None,
        "release_manifest_path": None,
        "release_manifest_sha256": None,
        "release_report_path": None,
        "release_report_sha256": None,
        "release_audit_path": None,
        "release_audit_sha256": None,
        "preflight_report_path": None,
        "preflight_report_sha256": None,
        "startup_report_path": None,
        "startup_report_sha256": None,
        "symbol": None,
        "as_of": None,
        "evaluation_at": None,
        "admission_status": "invalid",
        "audit_status": "invalid",
        "startup_status": "invalid",
        "startup_ready": False,
        "service_started": False,
        "probe_status": "not_started",
        "probe_exit_code": None,
        "stop_status": "not_attempted",
        "service_stopped": False,
        "status": "invalid",
        "run_ready": False,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }
    for field, path in inputs.items():
        relative, digest = _safe_meta(path, root=root)
        report[f"{field}_path"] = relative
        report[f"{field}_sha256"] = digest
    relative, digest = _safe_meta(preflight_path, root=root)
    report["preflight_report_path"] = relative
    report["preflight_report_sha256"] = digest
    return report


def _update_from_node75(report: dict[str, Any], payload: Mapping[str, Any]) -> None:
    for field in (
        "admission_version",
        "audit_version",
        "release_version",
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
        "release_audit_path",
        "release_audit_sha256",
        "symbol",
        "as_of",
        "evaluation_at",
        "admission_status",
        "audit_status",
    ):
        report[field] = payload[field]


def _write_report(report: dict[str, Any], *, output_dir: Path) -> Path:
    canonical = dict(report)
    canonical["output_sha256"] = None
    report["output_sha256"] = sha256_bytes(_json_bytes(canonical))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME
        write_atomic(path, _json_bytes(report))
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeError(
            "OUTPUT_UNAVAILABLE", "smoke report output is unavailable"
        ) from exc
    return path


def _command(*, paths: Mapping[str, Path], root: Path, output_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "a_share_ai.cli",
        "serve-research-receipt",
        "--artifact-root",
        str(root),
        "--daily-run-admission",
        str(paths["admission"]),
        "--daily-run-admission-report",
        str(paths["admission_report"]),
        "--daily-run-admission-audit",
        str(paths["admission_audit"]),
        "--daily-release-manifest",
        str(paths["release_manifest"]),
        "--daily-release-report",
        str(paths["release_report"]),
        "--daily-release-audit-report",
        str(paths["release_audit"]),
        "--output-dir",
        str(output_dir),
    ]


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


def _finish(
    report: dict[str, Any], *, output_dir: Path, status: str, code: int
) -> tuple[dict[str, Any], int]:
    report["status"] = status
    report["run_ready"] = status == "ready"
    report["decision_ready"] = False
    _write_report(report, output_dir=output_dir)
    return report, code


def run_daily_research_service_release_run_admission_startup_smoke(
    *,
    admission_path: Path,
    admission_report_path: Path,
    admission_audit_path: Path,
    release_manifest_path: Path,
    release_report_path: Path,
    release_audit_path: Path,
    artifact_root: Path,
    startup_timeout_seconds: float,
    probe_timeout_seconds: float,
    output_dir: Path,
) -> tuple[dict[str, Any], int]:
    """Run Node75 check-only, serve once, probe four routes, and stop it."""

    startup_timeout = _timeout(startup_timeout_seconds, label="startup-timeout-seconds")
    probe_timeout = _timeout(probe_timeout_seconds, label="probe-timeout-seconds")
    try:
        root = artifact_root.resolve()
    except OSError as exc:
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        ) from exc
    if not root.is_dir():
        raise DailyResearchServiceReleaseRunAdmissionStartupSmokeError(
            "ARTIFACT_ROOT_INVALID", "artifact root is invalid", configuration=True
        )
    output = _rooted_dir(output_dir, root=root, label="output-dir")
    preflight_dir = _rooted_dir(output / "preflight", root=root, label="preflight directory")
    startup_dir = _rooted_dir(output / "startup", root=root, label="startup directory")
    paths = {
        "admission": admission_path,
        "admission_report": admission_report_path,
        "admission_audit": admission_audit_path,
        "release_manifest": release_manifest_path,
        "release_report": release_report_path,
        "release_audit": release_audit_path,
    }
    preflight_path = (
        preflight_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    report = _base_report(root=root, inputs=paths, preflight_path=preflight_path)

    try:
        preflight_code, _ = run_daily_research_service_release_run_admission_startup(
            admission_path=admission_path,
            report_path=admission_report_path,
            audit_path=admission_audit_path,
            release_manifest_path=release_manifest_path,
            release_report_path=release_report_path,
            release_audit_path=release_audit_path,
            artifact_root=root,
            output_dir=preflight_dir,
            check_only=True,
        )
    except DailyResearchServiceReleaseRunAdmissionStartupError as exc:
        report["issues"] = [_issue(exc.code, str(exc))]
        return _finish(report, output_dir=output, status="invalid", code=1)

    preflight = _validate_node75_report(
        preflight_path,
        expected_mode="check_only",
        expected_inputs=paths,
        root=root,
    )
    if preflight is None:
        report["issues"] = [_issue("PREFLIGHT_INVALID", "Node75 preflight report is invalid")]
        return _finish(report, output_dir=output, status="invalid", code=1)
    report["preflight_report_path"], report["preflight_report_sha256"] = _safe_meta(
        preflight_path, root=root
    )
    _update_from_node75(report, preflight)
    if preflight_code != 0 or preflight["status"] != "ready":
        report["startup_status"] = preflight["startup_status"]
        report["startup_ready"] = preflight["startup_ready"] is True
        report["issues"] = preflight["issues"]
        status = preflight["status"] if preflight["status"] in {"blocked", "invalid"} else "failed"
        return _finish(report, output_dir=output, status=status, code=1)

    try:
        release_startup = load_daily_research_service_release_startup(
            manifest_path=release_manifest_path,
            report_path=release_report_path,
            audit_report_path=release_audit_path,
            artifact_root=root,
        )
    except DailyResearchServiceReleaseStartupError as exc:
        report["issues"] = [_issue(exc.code, "release startup admission is invalid")]
        return _finish(report, output_dir=output, status="invalid", code=1)

    process: subprocess.Popen[bytes] | None = None
    probe_done = False
    uncontrolled_exit = False
    startup_report_path = (
        startup_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_REPORT_NAME
    )
    try:
        startup_report_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        report["issues"] = [_issue("OUTPUT_UNAVAILABLE", "startup report is unavailable")]
        return _finish(report, output_dir=output, status="invalid", code=1)
    deadline = time.monotonic() + startup_timeout
    try:
        process = subprocess.Popen(
            _command(paths=paths, root=root, output_dir=startup_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while time.monotonic() < deadline:
            if process.poll() is not None:
                report["startup_status"] = "failed"
                report["issues"] = [_issue("SERVICE_EXITED", "service process exited unexpectedly")]
                break
            startup_report = _validate_node75_report(
                startup_report_path,
                expected_mode="serve",
                expected_inputs=paths,
                root=root,
            )
            if startup_report is not None:
                bound_fields = (
                    "startup_version",
                    "admission_version",
                    "audit_version",
                    "release_version",
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
                    "release_audit_path",
                    "release_audit_sha256",
                    "symbol",
                    "as_of",
                    "evaluation_at",
                    "admission_status",
                    "admission_ready",
                    "audit_status",
                    "audit_ready",
                )
                if any(startup_report[field] != preflight[field] for field in bound_fields):
                    report["startup_status"] = "failed"
                    report["issues"] = [
                        _issue("STARTUP_CHAIN_MISMATCH", "startup report differs from preflight")
                    ]
                    break
                report["startup_report_path"], report["startup_report_sha256"] = _safe_meta(
                    startup_report_path, root=root
                )
                report["startup_status"] = startup_report["startup_status"]
                report["startup_ready"] = startup_report["startup_ready"] is True
                report["service_started"] = startup_report["service_started"] is True
                if report["startup_status"] != "ready" or not report["service_started"]:
                    report["issues"] = startup_report["issues"]
                    break
                break
            time.sleep(0.05)
        else:
            report["startup_status"] = "failed"
            report["issues"] = [_issue("STARTUP_TIMEOUT", "service did not become ready")]

        if report["service_started"] and report["startup_ready"]:
            host = release_startup.gate_config.host
            port = release_startup.gate_config.port
            if host not in {"127.0.0.1", "::1"} or not isinstance(port, int):
                report["startup_status"] = "failed"
                report["issues"] = [_issue("STARTUP_INVALID", "startup endpoint is invalid")]
            else:
                probe_deadline = time.monotonic() + startup_timeout
                while time.monotonic() < probe_deadline:
                    if process.poll() is not None:
                        uncontrolled_exit = True
                        report["probe_status"] = "service_exited"
                        report["issues"] = [
                            _issue("SERVICE_EXITED", "service process exited before probe")
                        ]
                        break
                    try:
                        remaining = max(0.01, probe_deadline - time.monotonic())
                        probe = probe_daily_research_service(
                            base_url=(
                                f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
                            ),
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
                            report["issues"] = [
                                _issue("SERVICE_EXITED", "service process exited before stop")
                            ]
                        else:
                            report["probe_status"] = "ready"
                        probe_done = True
                        break
                    issues = probe.get("issues")
                    first_issue = issues[0] if isinstance(issues, list) and issues else None
                    if (
                        probe.get("status") == "invalid"
                        and isinstance(first_issue, Mapping)
                        and first_issue.get("code") == "SERVICE_UNAVAILABLE"
                        and time.monotonic() < probe_deadline
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

    if uncontrolled_exit:
        report["issues"] = report["issues"] or [
            _issue("SERVICE_EXITED", "service exited unexpectedly")
        ]
        return _finish(report, output_dir=output, status="failed", code=1)
    if report["stop_status"] == "failed" or not report["service_stopped"]:
        report["issues"] = report["issues"] or [
            _issue("SERVICE_STOP_FAILED", "service did not stop")
        ]
        return _finish(report, output_dir=output, status="failed", code=1)
    if (
        report["startup_ready"]
        and report["probe_status"] == "ready"
        and report["stop_status"] == "controlled"
    ):
        if not _port_is_free(release_startup.gate_config.host, release_startup.gate_config.port):
            report["stop_status"] = "failed"
            report["issues"] = [
                _issue("SERVICE_PORT_NOT_RELEASED", "service port was not released")
            ]
            return _finish(report, output_dir=output, status="failed", code=1)
        if probe_done:
            return _finish(report, output_dir=output, status="ready", code=0)
    report["issues"] = report["issues"] or [_issue("RUN_NOT_READY", "smoke run is not ready")]
    return _finish(report, output_dir=output, status="failed", code=1)


__all__ = [
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_REPORT_NAME",
    "DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_VERSION",
    "DailyResearchServiceReleaseRunAdmissionStartupSmokeError",
    "run_daily_research_service_release_run_admission_startup_smoke",
]
