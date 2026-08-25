import json
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import daily_research_service_release_run as run_module
from a_share_ai.service.daily_research_service_probe import DailyResearchServiceProbeError
from tests.service.test_daily_research_service_release_startup import _prepare_release


def _args(root: Path, manifest: Path, report: Path, audit: Path, output: Path) -> list[str]:
    return [
        "run-daily-research-service-release",
        "--daily-release-manifest",
        str(manifest),
        "--daily-release-report",
        str(report),
        "--daily-release-audit-report",
        str(audit),
        "--artifact-root",
        str(root),
        "--startup-timeout-seconds",
        "1",
        "--probe-timeout-seconds",
        "0.1",
        "--output-dir",
        str(output),
    ]


def _read_report(output: Path) -> dict:
    return json.loads(
        (output / "daily_research_service_release_run_report.json").read_text(encoding="utf-8")
    )


def _assert_self_hash(report: dict) -> None:
    canonical = dict(report)
    canonical["output_sha256"] = None
    expected = sha256_bytes(
        (json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    assert report["output_sha256"] == expected


class _LiveProcess:
    def __init__(self) -> None:
        self.stopped = False

    def poll(self) -> int | None:
        return 0 if self.stopped else None

    def terminate(self) -> None:
        self.stopped = True

    def wait(self, timeout: float) -> int:
        self.stopped = True
        return 0

    def kill(self) -> None:
        self.stopped = True


def _ready_probe() -> dict:
    return {
        "status": "ready",
        "daily_admission_ready": True,
        "decision_ready": False,
        "issues": [],
    }


def test_ready_release_run_is_controlled_and_audited(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "ready")
    process = _LiveProcess()
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process) as popen,
        patch.object(run_module, "probe_daily_research_service", return_value=_ready_probe()),
    ):
        code = main(_args(root, manifest, report, audit, root / "run"))

    receipt = _read_report(root / "run")
    assert code == 0
    assert receipt["run_version"] == "daily-research-service-release-run-v1"
    assert receipt["startup_status"] == "ready"
    assert receipt["startup_ready"] is True
    assert receipt["probe_status"] == "ready"
    assert receipt["probe_exit_code"] == 0
    assert receipt["stop_status"] == "controlled"
    assert receipt["service_stopped"] is True
    assert receipt["run_status"] == "ready"
    assert receipt["run_ready"] is True
    assert receipt["decision_ready"] is False
    assert str(root) not in json.dumps(receipt, ensure_ascii=False)
    _assert_self_hash(receipt)
    command = popen.call_args.args[0]
    assert "--daily-release-manifest" in command


def test_ready_release_run_real_loopback_e2e(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "real-e2e")
    args = _args(root, manifest, report, audit, root / "run")
    args[args.index("--startup-timeout-seconds") + 1] = "10"
    assert main(args) == 0
    receipt = _read_report(root / "run")
    assert receipt["probe_status"] == "ready"
    assert receipt["probe_exit_code"] == 0
    assert receipt["stop_status"] == "controlled"
    assert receipt["run_ready"] is True
    assert receipt["decision_ready"] is False
    _assert_self_hash(receipt)


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_unready_release_never_starts_service(tmp_path: Path, kwargs: dict[str, bool]) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "unready", **kwargs)
    with patch.object(run_module.subprocess, "Popen") as popen:
        assert main(_args(root, manifest, report, audit, root / "run")) == 1
    receipt = _read_report(root / "run")
    assert receipt["startup_status"] == "blocked"
    assert receipt["startup_ready"] is False
    assert receipt["probe_status"] == "not_started"
    assert receipt["stop_status"] == "not_attempted"
    assert receipt["run_ready"] is False
    popen.assert_not_called()
    _assert_self_hash(receipt)


def test_tampered_release_fails_closed_before_start(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "tamper")
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["status"] = "blocked"
    report.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    with patch.object(run_module.subprocess, "Popen") as popen:
        assert main(_args(root, manifest, report, audit, root / "run")) == 1
    receipt = _read_report(root / "run")
    assert receipt["run_ready"] is False
    assert receipt["issues"]
    popen.assert_not_called()


def test_service_exit_before_probe_is_uncontrolled(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "exit")

    class Exited:
        def poll(self) -> int:
            return 1

    with patch.object(run_module.subprocess, "Popen", return_value=Exited()):
        assert main(_args(root, manifest, report, audit, root / "run")) == 1
    receipt = _read_report(root / "run")
    assert receipt["probe_status"] == "service_exited"
    assert receipt["stop_status"] == "uncontrolled_exit"
    assert receipt["service_stopped"] is False
    assert receipt["run_ready"] is False


def test_probe_failure_is_not_ready_even_when_stop_succeeds(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "probe-fail")
    process = _LiveProcess()
    failed_probe = {
        "status": "blocked",
        "daily_admission_ready": False,
        "decision_ready": False,
        "issues": [{"code": "SERVICE_NOT_READY", "message": "blocked"}],
    }
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(run_module, "probe_daily_research_service", return_value=failed_probe),
    ):
        assert main(_args(root, manifest, report, audit, root / "run")) == 1
    receipt = _read_report(root / "run")
    assert receipt["probe_status"] == "failed"
    assert receipt["stop_status"] == "controlled"
    assert receipt["run_ready"] is False


def test_probe_timeout_is_fail_closed(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "timeout")
    process = _LiveProcess()

    def unavailable(**_: object) -> dict:
        raise DailyResearchServiceProbeError("SERVICE_UNAVAILABLE", "service unavailable")

    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(run_module, "probe_daily_research_service", side_effect=unavailable),
    ):
        assert main(_args(root, manifest, report, audit, root / "run")) == 1
    receipt = _read_report(root / "run")
    assert receipt["probe_status"] == "timeout"
    assert receipt["stop_status"] == "controlled"
    assert receipt["run_ready"] is False


def test_stop_failure_is_fail_closed(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "stop-fail")
    process = _LiveProcess()
    with (
        patch.object(run_module.subprocess, "Popen", return_value=process),
        patch.object(run_module, "probe_daily_research_service", return_value=_ready_probe()),
        patch.object(run_module, "_stop_process", return_value=(False, False)),
    ):
        assert main(_args(root, manifest, report, audit, root / "run")) == 1
    receipt = _read_report(root / "run")
    assert receipt["stop_status"] == "failed"
    assert receipt["service_stopped"] is False
    assert receipt["run_ready"] is False
    assert receipt["issues"][0]["code"] == "SERVICE_STOP_FAILED"


def test_invalid_configuration_returns_two(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "config")
    args = _args(root, manifest, report, audit, root / "run")
    args[args.index("--startup-timeout-seconds") + 1] = "0"
    assert main(args) == 2


def test_output_outside_artifact_root_returns_two(tmp_path: Path) -> None:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "outside")
    assert main(_args(root, manifest, report, audit, tmp_path / "outside-output")) == 2
