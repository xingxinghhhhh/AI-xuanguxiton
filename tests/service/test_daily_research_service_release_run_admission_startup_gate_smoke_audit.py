import json
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME,
    audit_daily_research_service_release_run_admission_startup_gate_smoke,
    run_daily_research_service_release_run_admission_startup_gate_smoke,
)
from tests.service.test_daily_research_service_release_run_admission_startup_gate import (
    _prepare_gate,
)


def _run_node81(paths: dict[str, Path], output_dir: Path) -> dict:
    report, code = run_daily_research_service_release_run_admission_startup_gate_smoke(
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
    return report


def _refresh(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _audit(paths: dict[str, Path], smoke_path: Path, output_dir: Path) -> tuple[dict, int]:
    return audit_daily_research_service_release_run_admission_startup_gate_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=output_dir,
    )


def _smoke_path(smoke_dir: Path) -> Path:
    return smoke_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_REPORT_NAME


def test_ready_smoke_audit_is_offline_and_ready(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path)
    smoke_dir = paths["root"] / "node81"
    _run_node81(paths, smoke_dir)
    smoke_path = _smoke_path(smoke_dir)
    before = smoke_path.read_bytes()
    with (
        patch("subprocess.Popen", side_effect=AssertionError("subprocess")),
        patch("urllib.request.urlopen", side_effect=AssertionError("http")),
    ):
        result, code = _audit(paths, smoke_path, paths["root"] / "audit")
    assert code == 0
    assert result["status"] == "ready"
    assert result["audit_ready"] is True
    assert result["decision_ready"] is False
    assert result["smoke_report_path"].endswith(".json")
    assert smoke_path.read_bytes() == before
    output = (
        paths["root"]
        / "audit"
        / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_GATE_SMOKE_AUDIT_REPORT_NAME
    )
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_blocked_smoke_audit_is_valid_non_ready_evidence(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path, blocked=True)
    smoke_dir = paths["root"] / "node81"
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
        output_dir=smoke_dir,
    )
    assert code == 1
    smoke_path = _smoke_path(smoke_dir)
    audited, audit_code = _audit(paths, smoke_path, paths["root"] / "audit")
    assert audited["status"] == result["smoke_status"]
    assert audited["audit_ready"] is True
    assert audit_code == 1


def test_smoke_self_hash_tamper_is_invalid(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path)
    smoke_dir = paths["root"] / "node81"
    _run_node81(paths, smoke_dir)
    smoke_path = _smoke_path(smoke_dir)
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload["probe_status"] = "failed"
    smoke_path.write_text(json.dumps(payload), encoding="utf-8")
    result, code = _audit(paths, smoke_path, paths["root"] / "audit")
    assert code == 1
    assert result["status"] == "invalid"
    assert result["audit_ready"] is False


def test_input_sha_tamper_is_invalid_after_smoke_self_hash_refresh(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path)
    smoke_dir = paths["root"] / "node81"
    _run_node81(paths, smoke_dir)
    smoke_path = _smoke_path(smoke_dir)
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload["admission_sha256"] = "0" * 64
    _refresh(smoke_path, payload)
    result, code = _audit(paths, smoke_path, paths["root"] / "audit")
    assert code == 1
    assert result["status"] == "invalid"
    assert result["audit_ready"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("decision_ready", True), ("probe_exit_code", []), ("issues", {})],
)
def test_invalid_types_are_rejected_after_self_hash_refresh(
    tmp_path: Path, field: str, value: object
) -> None:
    paths = _prepare_gate(tmp_path)
    smoke_dir = paths["root"] / "node81"
    _run_node81(paths, smoke_dir)
    smoke_path = _smoke_path(smoke_dir)
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload[field] = value
    _refresh(smoke_path, payload)
    result, code = _audit(paths, smoke_path, paths["root"] / "audit")
    assert code == 1
    assert result["status"] == "invalid"
    assert result["audit_ready"] is False


def test_cli_audit_returns_zero_for_ready_and_two_for_output_escape(tmp_path: Path) -> None:
    paths = _prepare_gate(tmp_path)
    smoke_dir = paths["root"] / "node81"
    _run_node81(paths, smoke_dir)
    smoke_path = _smoke_path(smoke_dir)
    args = [
        "audit-daily-research-service-release-run-admission-startup-gate-smoke",
        "--smoke-report",
        str(smoke_path),
        "--artifact-root",
        str(paths["root"]),
        "--output-dir",
        str(paths["root"] / "cli-audit"),
    ]
    assert main(args) == 0
    escape = list(args)
    escape[-1] = str(tmp_path / "outside")
    assert main(escape) == 2
