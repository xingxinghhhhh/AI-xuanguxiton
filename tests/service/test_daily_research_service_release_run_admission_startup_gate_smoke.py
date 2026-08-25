import json
from pathlib import Path
from unittest.mock import patch

import pytest

import a_share_ai.service.daily_research_service_release_run_admission_startup_gate_smoke as smoke_module  # noqa: E501
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
    run_daily_research_service_release_run_admission_startup_gate_smoke,
)
from tests.service.test_daily_research_service_release_run_admission_startup_gate import (
    _prepare_gate,
)


def _args(paths: dict[str, Path], output_dir: Path, *, startup: str = "10") -> list[str]:
    return [
        "run-daily-research-service-release-run-admission-startup-gate-smoke",
        "--daily-smoke-admission",
        str(paths["smoke_admission"]),
        "--daily-smoke-admission-report",
        str(paths["smoke_admission_report"]),
        "--daily-smoke-admission-audit-report",
        str(paths["smoke_admission_audit"]),
        "--daily-release-manifest",
        str(paths["manifest"]),
        "--daily-release-report",
        str(paths["release_report"]),
        "--daily-release-audit-report",
        str(paths["release_audit"]),
        "--artifact-root",
        str(paths["root"]),
        "--startup-timeout-seconds",
        startup,
        "--probe-timeout-seconds",
        "1",
        "--output-dir",
        str(output_dir),
    ]


def _report(output_dir: Path) -> dict:
    return json.loads(
        (output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME)
        .read_text(encoding="utf-8")
    )


def _self_hash(payload: dict) -> None:
    canonical = dict(payload)
    declared = canonical["output_sha256"]
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _refresh(payload: dict, path: Path) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_ready_gate_smoke_runs_real_node80_loopback_e2e(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path)
    before = {
        key: value.read_bytes()
        for key, value in {
            "admission": paths["smoke_admission"],
            "report": paths["smoke_admission_report"],
            "audit": paths["smoke_admission_audit"],
        }.items()
    }
    output_dir = paths["root"] / "node81-smoke"
    result, code = run_daily_research_service_release_run_admission_startup_gate_smoke(
        admission_path=paths["smoke_admission"],
        admission_report_path=paths["smoke_admission_report"],
        admission_audit_path=paths["smoke_admission_audit"],
        release_manifest_path=paths["manifest"],
        release_report_path=paths["release_report"],
        release_audit_report_path=paths["release_audit"],
        artifact_root=paths["root"],
        startup_timeout_seconds=10,
        probe_timeout_seconds=1,
        output_dir=output_dir,
    )
    assert code == 0
    assert result["gate_status"] == "ready"
    assert result["gate_ready"] is True
    assert result["startup_status"] == "ready"
    assert result["service_started"] is True
    assert result["probe_status"] == "ready"
    assert result["probe_exit_code"] == 0
    assert result["stop_status"] == "controlled"
    assert result["service_stopped"] is True
    assert result["port_released"] is True
    assert result["smoke_status"] == "ready"
    assert result["smoke_ready"] is True
    assert result["decision_ready"] is False
    assert before == {
        "admission": paths["smoke_admission"].read_bytes(),
        "report": paths["smoke_admission_report"].read_bytes(),
        "audit": paths["smoke_admission_audit"].read_bytes(),
    }
    _self_hash(result)
    assert _report(output_dir) == result


def test_blocked_gate_does_not_start_a_process(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path, blocked=True)
    with patch.object(smoke_module.subprocess, "Popen") as popen:
        result, code = run_daily_research_service_release_run_admission_startup_gate_smoke(
            admission_path=paths["smoke_admission"],
            admission_report_path=paths["smoke_admission_report"],
            admission_audit_path=paths["smoke_admission_audit"],
            release_manifest_path=paths["manifest"],
            release_report_path=paths["release_report"],
            release_audit_report_path=paths["release_audit"],
            artifact_root=paths["root"],
            startup_timeout_seconds=10,
            probe_timeout_seconds=1,
            output_dir=paths["root"] / "blocked-smoke",
        )
    assert code == 1
    assert result["gate_status"] in {"blocked", "failed", "invalid"}
    assert result["smoke_ready"] is False
    assert result["decision_ready"] is False
    popen.assert_not_called()


def test_invalid_gate_does_not_start_a_process(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path)
    payload = json.loads(paths["smoke_admission_report"].read_text(encoding="utf-8"))
    payload["decision_ready"] = True
    _refresh(payload, paths["smoke_admission_report"])
    with patch.object(smoke_module.subprocess, "Popen") as popen:
        result, code = run_daily_research_service_release_run_admission_startup_gate_smoke(
            admission_path=paths["smoke_admission"],
            admission_report_path=paths["smoke_admission_report"],
            admission_audit_path=paths["smoke_admission_audit"],
            release_manifest_path=paths["manifest"],
            release_report_path=paths["release_report"],
            release_audit_report_path=paths["release_audit"],
            artifact_root=paths["root"],
            startup_timeout_seconds=10,
            probe_timeout_seconds=1,
            output_dir=paths["root"] / "invalid-smoke",
        )
    assert code == 1
    assert result["gate_status"] == "invalid"
    popen.assert_not_called()


@pytest.mark.parametrize("timeout", ["0", "301", "nan"])
def test_cli_rejects_invalid_timeout(tmp_path: Path, timeout: str) -> None:
    paths = _prepare_gate(tmp_path)
    assert main(_args(paths, paths["root"] / "timeout", startup=timeout)) == 2


def test_cli_rejects_output_escape(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path)
    assert main(_args(paths, tmp_path / "outside")) == 2


def test_cli_dispatches_ready_smoke_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _prepare_gate(tmp_path)
    fake_report = {"smoke_status": "ready", "smoke_ready": True, "decision_ready": False}
    with patch(
        "a_share_ai.cli.run_daily_research_service_release_run_admission_startup_gate_smoke",
        return_value=(fake_report, 0),
    ) as runner:
        assert main(_args(paths, paths["root"] / "cli-smoke")) == 0
    runner.assert_called_once()
    assert json.loads(capsys.readouterr().out) == fake_report
