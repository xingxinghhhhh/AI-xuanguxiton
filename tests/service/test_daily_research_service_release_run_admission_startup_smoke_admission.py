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
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME,
    build_daily_research_service_release_run_admission_startup_smoke_admission,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup_smoke import (
    run_daily_research_service_release_run_admission_startup_smoke,
)
from a_share_ai.service.daily_research_service_release_run_admission_startup_smoke_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME,
    audit_daily_research_service_release_run_admission_startup_smoke,
)
from tests.service.test_daily_research_service_release_run_admission_startup import (
    _prepare_startup,
)


def _run_smoke(paths: dict[str, Path], output_dir: Path) -> tuple[dict, int]:
    return run_daily_research_service_release_run_admission_startup_smoke(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        admission_audit_path=paths["admission_audit"],
        release_manifest_path=paths["manifest"],
        release_report_path=paths["release_report"],
        release_audit_path=paths["release_audit"],
        artifact_root=paths["root"],
        startup_timeout_seconds=10,
        probe_timeout_seconds=1,
        output_dir=output_dir,
    )


def _build(paths: dict[str, Path], *, name: str = "admission") -> tuple[dict, dict, int]:
    smoke_dir = paths["root"] / "smoke"
    audit_dir = paths["root"] / "smoke-audit"
    admission_dir = paths["root"] / name
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    audit_path = (
        audit_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME
    )
    _run_smoke(paths, smoke_dir)
    audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=audit_dir,
    )
    return build_daily_research_service_release_run_admission_startup_smoke_admission(
        smoke_report_path=smoke_path,
        smoke_audit_report_path=audit_path,
        artifact_root=paths["root"],
        output_dir=admission_dir,
    )


def _self_hash(payload: dict) -> None:
    canonical = dict(payload)
    declared = canonical["output_sha256"]
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _refresh_hash(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_ready_smoke_and_audit_build_ready_admission_offline(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "ready")
    smoke_dir = paths["root"] / "smoke"
    audit_dir = paths["root"] / "smoke-audit"
    smoke, smoke_code = _run_smoke(paths, smoke_dir)
    assert smoke_code == 0
    with (
        patch.object(subprocess, "Popen") as popen,
        patch.object(socket, "socket") as socket_factory,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        audit, audit_code = audit_daily_research_service_release_run_admission_startup_smoke(
            smoke_report_path=smoke_dir
            / "daily_research_service_release_run_admission_startup_smoke_report.json",
            artifact_root=paths["root"],
            output_dir=audit_dir,
        )
        manifest, report, code = (
            build_daily_research_service_release_run_admission_startup_smoke_admission(
                smoke_report_path=smoke_dir
                / "daily_research_service_release_run_admission_startup_smoke_report.json",
                smoke_audit_report_path=audit_dir
                / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME,
                artifact_root=paths["root"],
                output_dir=paths["root"] / "admission",
            )
        )
    assert audit_code == 0
    assert audit["audit_ready"] is True
    assert code == 0
    assert manifest["status"] == "ready"
    assert manifest["admission_ready"] is True
    assert manifest["decision_ready"] is False
    assert (
        report["admission_path"]
        == "admission/" + DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_ADMISSION_NAME
    )
    _self_hash(manifest)
    _self_hash(report)
    popen.assert_not_called()
    socket_factory.assert_not_called()
    urlopen.assert_not_called()


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_blocked_or_failed_smoke_builds_non_ready_admission(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    paths = _prepare_startup(tmp_path / next(iter(kwargs)), **kwargs)
    manifest, report, code = _build(paths)
    assert code == 1
    assert manifest["status"] in {"blocked", "failed"}
    assert manifest["admission_ready"] is False
    assert manifest["audit_ready"] is True
    assert manifest["run_ready"] is False
    assert report["admission_sha256"]
    _self_hash(manifest)
    _self_hash(report)


def test_invalid_input_builds_invalid_admission(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "invalid")
    smoke_dir = paths["root"] / "smoke"
    audit_dir = paths["root"] / "smoke-audit"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=audit_dir,
    )
    audit_path = (
        audit_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME
    )
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload["decision_ready"] = True
    _refresh_hash(audit_path, payload)
    manifest, report, code = (
        build_daily_research_service_release_run_admission_startup_smoke_admission(
            smoke_report_path=smoke_path,
            smoke_audit_report_path=audit_path,
            artifact_root=paths["root"],
            output_dir=paths["root"] / "admission",
        )
    )
    assert code == 1
    assert manifest["status"] == "invalid"
    assert manifest["admission_ready"] is False
    assert manifest["audit_ready"] is False
    assert report["status"] == "invalid"


def test_admission_cli_returns_two_for_output_escape(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "cli")
    _run_smoke(paths, paths["root"] / "smoke")
    audit_dir = paths["root"] / "smoke-audit"
    smoke_path = (
        paths["root"]
        / "smoke"
        / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=audit_dir,
    )
    args = [
        "build-daily-research-service-release-run-admission-startup-smoke-admission",
        "--smoke-report",
        str(smoke_path),
        "--smoke-audit-report",
        str(
            audit_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME
        ),
        "--artifact-root",
        str(paths["root"]),
        "--output-dir",
        str(tmp_path / "outside"),
    ]
    assert main(args) == 2
