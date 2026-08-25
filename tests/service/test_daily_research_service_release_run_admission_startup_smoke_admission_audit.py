import json
import socket
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
    audit_daily_research_service_release_run_admission_startup_smoke_admission,
)
from tests.service.test_daily_research_service_release_run_admission_startup import (
    _prepare_startup,
)
from tests.service.test_daily_research_service_release_run_admission_startup_smoke_admission import (  # noqa: E501
    _build,
)


def _pair(paths: dict[str, Path], *, status: str = "ready") -> tuple[Path, Path]:
    _build(paths, name="admission")
    if status == "ready":
        output_dir = paths["root"] / "admission"
    else:
        _build(paths, name="admission")
        output_dir = paths["root"] / "admission"
    return (
        output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
        output_dir
        / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_REPORT_NAME,
    )


def _audit(paths: dict[str, Path], *, output_dir: Path | None = None) -> tuple[dict, int]:
    admission_path, report_path = _pair(paths)
    return audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=output_dir or paths["root"] / "audit",
    )


def _refresh(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _self_hash(payload: dict) -> None:
    canonical = dict(payload)
    declared = canonical["output_sha256"]
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_ready_pair_audits_offline_and_self_hashes(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "ready")
    admission_path, report_path = _pair(paths)
    before = {path: path.read_bytes() for path in (admission_path, report_path)}
    with (
        patch.object(subprocess, "Popen") as popen,
        patch.object(socket, "socket") as socket_factory,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
            admission_path=admission_path,
            report_path=report_path,
            artifact_root=paths["root"],
            output_dir=paths["root"] / "audit",
        )
    assert code == 0
    assert result["status"] == "ready"
    assert result["audit_ready"] is True
    assert result["admission_ready"] is True
    assert result["decision_ready"] is False
    _self_hash(result)
    assert {path: path.read_bytes() for path in (admission_path, report_path)} == before
    popen.assert_not_called()
    socket_factory.assert_not_called()
    urlopen.assert_not_called()


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_blocked_or_failed_pair_is_audited_but_not_ready(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    paths = _prepare_startup(tmp_path / next(iter(kwargs)), **kwargs)
    admission_path, report_path = _pair(paths)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] in {"blocked", "failed"}
    assert result["audit_ready"] is True
    assert result["admission_ready"] is False
    assert result["run_ready"] is False


@pytest.mark.parametrize("target", ["manifest", "report"])
def test_single_file_tamper_is_invalid(tmp_path: Path, target: str) -> None:
    paths = _prepare_startup(tmp_path / target)
    admission_path, report_path = _pair(paths)
    target_path = admission_path if target == "manifest" else report_path
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    payload["status"] = "blocked"
    _refresh(target_path, payload)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] == "invalid"
    assert result["audit_ready"] is False


@pytest.mark.parametrize("field, value", [("symbol", ""), ("as_of", None)])
def test_ready_identity_is_required(tmp_path: Path, field: str, value: object) -> None:
    paths = _prepare_startup(tmp_path / field)
    admission_path, report_path = _pair(paths)
    manifest = json.loads(admission_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest[field] = value
    report[field] = value
    _refresh(admission_path, manifest)
    _refresh(report_path, report)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] == "invalid"


def test_ready_time_order_and_timezone_are_required(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "time-order")
    admission_path, report_path = _pair(paths)
    manifest = json.loads(admission_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for payload in (manifest, report):
        payload["as_of"] = "2026-08-11T02:00:00Z"
        payload["evaluation_at"] = "2026-08-11T01:00:00"
    _refresh(admission_path, manifest)
    report["admission_sha256"] = sha256_bytes(admission_path.read_bytes())
    _refresh(report_path, report)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] == "invalid"


def test_blocked_runtime_state_cannot_be_promoted_to_ready(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "blocked-runtime", blocked=True)
    admission_path, report_path = _pair(paths)
    manifest = json.loads(admission_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for payload in (manifest, report):
        payload["startup_ready"] = True
        payload["service_started"] = True
        payload["probe_status"] = "ready"
        payload["probe_exit_code"] = 0
        payload["stop_status"] = "controlled"
        payload["service_stopped"] = True
    _refresh(admission_path, manifest)
    _refresh(report_path, report)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] == "invalid"


def test_failed_probe_runtime_state_requires_valid_failure_shape(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "failed-runtime")
    admission_path, report_path = _pair(paths)
    manifest = json.loads(admission_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for payload in (manifest, report):
        payload["status"] = "failed"
        payload["smoke_status"] = "failed"
        payload["audit_status"] = "failed"
        payload["admission_ready"] = False
        payload["audit_ready"] = True
        payload["run_ready"] = False
        payload["startup_status"] = "ready"
        payload["startup_ready"] = True
        payload["service_started"] = True
        payload["probe_status"] = "timeout"
        payload["probe_exit_code"] = None
        payload["stop_status"] = "controlled"
        payload["service_stopped"] = True
        payload["issues"] = [{"code": "PROBE_TIMEOUT", "message": "service probe timed out"}]
    _refresh(admission_path, manifest)
    report["admission_sha256"] = sha256_bytes(admission_path.read_bytes())
    _refresh(report_path, report)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] == "failed"
    assert result["audit_ready"] is True


@pytest.mark.parametrize("mutation", ["unknown", "bad_type", "bad_sha", "decision"])
def test_schema_and_gate_mutations_are_invalid(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_startup(tmp_path / mutation)
    admission_path, report_path = _pair(paths)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if mutation == "unknown":
        payload["unexpected"] = True
    elif mutation == "bad_type":
        payload["audit_ready"] = []
    elif mutation == "bad_sha":
        payload["admission_sha256"] = "0" * 64
    else:
        payload["decision_ready"] = True
    _refresh(report_path, payload)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] == "invalid"
    assert result["audit_ready"] is False


def test_absolute_source_path_and_invalid_utf8_are_invalid(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "paths")
    admission_path, report_path = _pair(paths)
    payload = json.loads(admission_path.read_text(encoding="utf-8"))
    payload["smoke_report_path"] = r"C:\\secret.json"
    _refresh(admission_path, payload)
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit",
    )
    assert code == 1
    assert result["status"] == "invalid"

    admission_path.write_bytes(b"\xff")
    result, code = audit_daily_research_service_release_run_admission_startup_smoke_admission(
        admission_path=admission_path,
        report_path=report_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "audit-utf8",
    )
    assert code == 1
    assert result["status"] == "invalid"


def test_cli_returns_zero_one_and_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = _prepare_startup(tmp_path / "cli")
    admission_path, report_path = _pair(paths)
    base_args = [
        "audit-daily-research-service-release-run-admission-startup-smoke-admission",
        "--admission",
        str(admission_path),
        "--report",
        str(report_path),
        "--artifact-root",
        str(paths["root"]),
        "--output-dir",
        str(paths["root"] / "cli-audit"),
    ]
    assert main(base_args) == 0
    json.loads(capsys.readouterr().out)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["decision_ready"] = True
    _refresh(report_path, payload)
    assert main(base_args) == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "invalid"

    escape_args = base_args.copy()
    escape_args[-1] = str(tmp_path / "outside")
    assert main(escape_args) == 2
    capsys.readouterr()


def test_audit_output_is_deterministic(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "deterministic")
    admission_path, report_path = _pair(paths)
    first, first_code = _audit(paths, output_dir=paths["root"] / "audit")
    first_bytes = (
        paths["root"]
        / "audit"
        / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME
    ).read_bytes()
    second, second_code = _audit(paths, output_dir=paths["root"] / "audit")
    second_bytes = (
        paths["root"]
        / "audit"
        / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_AUDIT_REPORT_NAME
    ).read_bytes()
    assert first_code == second_code == 0
    assert first == second
    assert first_bytes == second_bytes
    assert admission_path.is_file()
    assert report_path.is_file()
