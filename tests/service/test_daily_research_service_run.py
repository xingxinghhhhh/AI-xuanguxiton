import json
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import daily_research_service_run as run_module
from a_share_ai.service.daily_research_service_run import run_daily_research_service
from tests.service.test_daily_research_service_launch_gate import (
    _build_gate,
    _build_gate_audit,
    _prepare_gate,
)


def _root_and_gate(tmp_path: Path, *, blocked: bool = False) -> tuple[Path, Path, Path, int]:
    inputs = _prepare_gate(tmp_path / "inputs", blocked=blocked)
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    audit_path = _build_gate_audit(root, gate_dir)
    manifest = json.loads(inputs["launch_manifest"].read_text())
    return root, gate_dir, audit_path, manifest["port"]


def _run_command(root: Path, gate_dir: Path, audit_path: Path, output_dir: Path) -> int:
    return main(
        [
            "run-daily-research-service",
            "--daily-launch-gate",
            str(gate_dir / "daily_research_service_launch_gate.json"),
            "--daily-launch-gate-root",
            str(root),
            "--daily-launch-gate-audit",
            str(audit_path),
            "--startup-timeout-seconds",
            "20",
            "--probe-timeout-seconds",
            "0.2",
            "--output-dir",
            str(output_dir),
        ]
    )


def _read_report(output_dir: Path) -> tuple[dict, bytes]:
    path = output_dir / "daily_research_service_run_report.json"
    raw = path.read_bytes()
    return json.loads(raw), raw


def _assert_self_hash(report: dict) -> None:
    declared = report["output_sha256"]
    canonical = dict(report)
    canonical["output_sha256"] = None
    expected = sha256_bytes(
        (json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    assert declared == expected


def _port_is_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def test_ready_run_probes_stops_and_writes_audited_report(tmp_path: Path) -> None:
    root, gate_dir, audit_path, port = _root_and_gate(tmp_path)
    gate_before = (gate_dir / "daily_research_service_launch_gate.json").read_bytes()
    audit_before = audit_path.read_bytes()

    assert _run_command(root, gate_dir, audit_path, root / "run-report") == 0
    report, raw = _read_report(root / "run-report")

    assert set(report) == {
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
    assert report["run_version"] == "daily-research-service-run-v1"
    assert report["startup_status"] == "ready"
    assert report["probe_status"] == "ready"
    assert report["probe_exit_code"] == 0
    assert report["service_stopped"] is True
    assert report["run_ready"] is True
    assert report["decision_ready"] is False
    _assert_self_hash(report)
    assert raw.endswith(b"\n")
    assert gate_before == (gate_dir / "daily_research_service_launch_gate.json").read_bytes()
    assert audit_before == audit_path.read_bytes()
    assert _port_is_closed(port)


def test_ready_run_is_deterministic_and_releases_port(tmp_path: Path) -> None:
    root, gate_dir, audit_path, port = _root_and_gate(tmp_path)
    output_dir = root / "run-report"
    assert _run_command(root, gate_dir, audit_path, output_dir) == 0
    first = (output_dir / "daily_research_service_run_report.json").read_bytes()
    assert _port_is_closed(port)
    assert _run_command(root, gate_dir, audit_path, output_dir) == 0
    second = (output_dir / "daily_research_service_run_report.json").read_bytes()
    assert first == second
    assert _port_is_closed(port)


def test_blocked_gate_does_not_start_service(tmp_path: Path) -> None:
    root, gate_dir, audit_path, port = _root_and_gate(tmp_path, blocked=True)
    assert _run_command(root, gate_dir, audit_path, root / "run-report") == 1
    report, _ = _read_report(root / "run-report")
    assert report["startup_status"] == "blocked"
    assert report["probe_status"] is None
    assert report["run_ready"] is False
    assert report["issues"][0]["code"] == "STARTUP_NOT_READY"
    assert _port_is_closed(port)


def test_tampered_audit_fails_closed_without_listening(tmp_path: Path) -> None:
    root, gate_dir, audit_path, port = _root_and_gate(tmp_path)
    payload = json.loads(audit_path.read_text())
    payload["gate_sha256"] = "0" * 64
    canonical = dict(payload)
    canonical["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    audit_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )

    assert _run_command(root, gate_dir, audit_path, root / "run-report") == 1
    report, _ = _read_report(root / "run-report")
    assert report["run_ready"] is False
    assert report["issues"][0]["code"] == "CHAIN_MISMATCH"
    assert _port_is_closed(port)


def test_external_gate_and_audit_are_rejected_before_hashing(tmp_path: Path) -> None:
    root = tmp_path / "artifact-root"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":"must not be read by the runner"}\n')

    report, exit_code = run_daily_research_service(
        gate_path=outside,
        artifact_root=root,
        audit_path=outside,
        startup_timeout_seconds=1,
        probe_timeout_seconds=0.1,
        output_dir=root / "run-report",
    )

    assert exit_code == 1
    assert report["gate_path"] is None
    assert report["gate_sha256"] is None
    assert report["audit_path"] is None
    assert report["audit_sha256"] is None
    assert report["issues"][0]["code"] in {"PATH_OUTSIDE_ARTIFACT_ROOT", "INPUT_INVALID"}


def test_probe_success_after_child_exit_never_becomes_ready(tmp_path: Path) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path)

    class ExitedAfterProbe:
        def __init__(self) -> None:
            self.poll_calls = 0

        def poll(self) -> int | None:
            self.poll_calls += 1
            return None if self.poll_calls == 1 else 1

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 1

        def kill(self) -> None:
            return None

    process = ExitedAfterProbe()
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(
            run_module,
            "probe_daily_research_service",
            return_value={
                "status": "ready",
                "daily_admission_ready": True,
                "decision_ready": False,
            },
        ),
    ):
        report, exit_code = run_daily_research_service(
            gate_path=gate_dir / "daily_research_service_launch_gate.json",
            artifact_root=root,
            audit_path=audit_path,
            startup_timeout_seconds=1,
            probe_timeout_seconds=0.1,
            output_dir=root / "run-report",
        )

    assert exit_code == 1
    assert report["probe_status"] == "ready"
    assert report["probe_exit_code"] == 0
    assert report["service_stopped"] is False
    assert report["run_ready"] is False
    assert report["issues"][0]["code"] == "SERVICE_EXITED"


def test_probe_timeout_stops_child_and_never_becomes_ready(tmp_path: Path) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path)

    class AliveProcess:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> int | None:
            return 0 if self.terminated else None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            self.terminated = True

    process = AliveProcess()
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(
            run_module,
            "probe_daily_research_service",
            return_value={
                "status": "invalid",
                "issues": [{"code": "SERVICE_UNAVAILABLE", "message": "service request failed"}],
            },
        ),
    ):
        report, exit_code = run_daily_research_service(
            gate_path=gate_dir / "daily_research_service_launch_gate.json",
            artifact_root=root,
            audit_path=audit_path,
            startup_timeout_seconds=0.1,
            probe_timeout_seconds=0.01,
            output_dir=root / "run-report",
        )

    assert exit_code == 1
    assert report["run_ready"] is False
    assert report["service_stopped"] is True
    assert process.terminated is True


def test_failed_stop_never_reports_ready(tmp_path: Path) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path)

    class UnstoppableProcess:
        def __init__(self) -> None:
            self.killed = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise OSError("terminate failed")

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            self.killed = True

    process = UnstoppableProcess()
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(
            run_module,
            "probe_daily_research_service",
            return_value={
                "status": "ready",
                "daily_admission_ready": True,
                "decision_ready": False,
            },
        ),
    ):
        report, exit_code = run_daily_research_service(
            gate_path=gate_dir / "daily_research_service_launch_gate.json",
            artifact_root=root,
            audit_path=audit_path,
            startup_timeout_seconds=1,
            probe_timeout_seconds=0.1,
            output_dir=root / "run-report",
        )

    assert exit_code == 1
    assert report["run_ready"] is False
    assert report["service_stopped"] is False
    assert report["issues"][0]["code"] == "SERVICE_STOP_FAILED"
    assert process.killed is True


def test_exit_between_finally_check_and_terminate_is_not_controlled(
    tmp_path: Path,
) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path)

    class RaceProcess:
        def __init__(self) -> None:
            self.poll_calls = 0
            self.terminate_attempted = False

        def poll(self) -> int | None:
            self.poll_calls += 1
            return None if self.poll_calls <= 3 else 1

        def terminate(self) -> None:
            self.terminate_attempted = True
            raise OSError("process exited before terminate")

        def wait(self, timeout: float | None = None) -> int:
            return 1

        def kill(self) -> None:
            return None

    process = RaceProcess()
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(
            run_module,
            "probe_daily_research_service",
            return_value={
                "status": "ready",
                "daily_admission_ready": True,
                "decision_ready": False,
            },
        ),
    ):
        report, exit_code = run_daily_research_service(
            gate_path=gate_dir / "daily_research_service_launch_gate.json",
            artifact_root=root,
            audit_path=audit_path,
            startup_timeout_seconds=1,
            probe_timeout_seconds=0.1,
            output_dir=root / "run-report",
        )

    assert exit_code == 1
    assert process.terminate_attempted is True
    assert report["service_stopped"] is False
    assert report["run_ready"] is False
    assert report["issues"][0]["code"] == "SERVICE_EXITED"


def test_failed_terminate_and_kill_while_alive_is_stop_failure(tmp_path: Path) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path)

    class KillFailureProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise OSError("terminate failed")

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("service", timeout or 0)

        def kill(self) -> None:
            raise OSError("kill failed")

    process = KillFailureProcess()
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(
            run_module,
            "probe_daily_research_service",
            return_value={
                "status": "ready",
                "daily_admission_ready": True,
                "decision_ready": False,
            },
        ),
    ):
        report, exit_code = run_daily_research_service(
            gate_path=gate_dir / "daily_research_service_launch_gate.json",
            artifact_root=root,
            audit_path=audit_path,
            startup_timeout_seconds=1,
            probe_timeout_seconds=0.1,
            output_dir=root / "run-report",
        )

    assert exit_code == 1
    assert report["service_stopped"] is False
    assert report["run_ready"] is False
    assert report["issues"][0]["code"] == "SERVICE_STOP_FAILED"


@pytest.mark.parametrize(
    ("option", "value"),
    [("--startup-timeout-seconds", "0"), ("--probe-timeout-seconds", "nan")],
)
def test_invalid_timeout_returns_configuration_error(
    tmp_path: Path, option: str, value: str
) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "run-daily-research-service",
            "--daily-launch-gate",
            str(gate_dir / "daily_research_service_launch_gate.json"),
            "--daily-launch-gate-root",
            str(root),
            "--daily-launch-gate-audit",
            str(audit_path),
            option,
            value,
            "--output-dir",
            str(root / "run-report"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "TIMEOUT_INVALID" in result.stderr
