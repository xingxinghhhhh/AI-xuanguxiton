import json
import socket
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
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


def _audit_args(smoke_report: Path, root: Path, output_dir: Path) -> list[str]:
    return [
        "audit-daily-research-service-release-run-admission-startup-smoke",
        "--smoke-report",
        str(smoke_report),
        "--artifact-root",
        str(root),
        "--output-dir",
        str(output_dir),
    ]


def _read_audit(output_dir: Path) -> tuple[dict, bytes]:
    raw = (
        output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME
    ).read_bytes()
    return json.loads(raw), raw


def _assert_self_hash(report: dict) -> None:
    canonical = dict(report)
    declared = canonical.pop("output_sha256")
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _refresh_compact_hash(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    path.write_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_ready_smoke_receipt_is_audited_offline(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "ready")
    smoke_dir = paths["root"] / "smoke"
    smoke, smoke_code = _run_smoke(paths, smoke_dir)
    assert smoke_code == 0
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    audit_dir = paths["root"] / "smoke-audit"
    with (
        patch.object(subprocess, "Popen") as popen,
        patch.object(socket, "socket") as socket_factory,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        report, code = audit_daily_research_service_release_run_admission_startup_smoke(
            smoke_report_path=smoke_path,
            artifact_root=paths["root"],
            output_dir=audit_dir,
        )
    assert code == 0
    assert report["status"] == "ready"
    assert report["audit_ready"] is True
    assert report["run_ready"] is True
    assert report["decision_ready"] is False
    assert report["smoke_report_sha256"] == sha256_bytes(smoke_path.read_bytes())
    _assert_self_hash(report)
    popen.assert_not_called()
    socket_factory.assert_not_called()
    urlopen.assert_not_called()


@pytest.mark.parametrize("blocked", [True, False])
def test_blocked_or_failed_smoke_receipt_is_auditable_but_not_ready(
    tmp_path: Path, blocked: bool
) -> None:
    paths = _prepare_startup(
        tmp_path / ("blocked" if blocked else "failed"), blocked=blocked, failed=not blocked
    )
    smoke_dir = paths["root"] / "smoke"
    smoke, smoke_code = _run_smoke(paths, smoke_dir)
    assert smoke_code == 1
    assert smoke["status"] in {"blocked", "failed"}
    report, code = audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_dir
        / "daily_research_service_release_run_admission_startup_smoke_report.json",
        artifact_root=paths["root"],
        output_dir=paths["root"] / "smoke-audit",
    )
    assert code == 1
    assert report["status"] in {"blocked", "failed"}
    assert report["audit_ready"] is True
    assert report["run_ready"] is False
    assert report["decision_ready"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "decision",
        "path",
        "preflight",
    ],
)
def test_tampered_smoke_chain_is_invalid(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_startup(tmp_path / mutation)
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    if mutation == "preflight":
        preflight = (
            smoke_dir
            / "preflight"
            / "daily_research_service_release_run_admission_startup_report.json"
        )
        payload = json.loads(preflight.read_text(encoding="utf-8"))
        payload["symbol"] = "OTHER.SYMBOL"
        _refresh_compact_hash(preflight, payload)
    else:
        payload = json.loads(smoke_path.read_text(encoding="utf-8"))
        if mutation == "unknown":
            payload["unknown"] = True
        elif mutation == "decision":
            payload["decision_ready"] = True
        else:
            payload["release_report_path"] = "wrong/report.json"
        _refresh_compact_hash(smoke_path, payload)
    report, code = audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "smoke-audit",
    )
    assert code == 1
    assert report["status"] == "invalid"
    assert report["audit_ready"] is False
    assert report["run_ready"] is False
    assert report["decision_ready"] is False


def test_audit_cli_returns_two_for_output_escape(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "cli")
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    assert main(_audit_args(smoke_path, paths["root"], tmp_path / "outside")) == 2


def test_audit_is_deterministic_and_preserves_inputs(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "deterministic")
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    tracked = list(paths["root"].rglob("*.json"))
    before = {path: path.read_bytes() for path in tracked}
    audit_dir = paths["root"] / "smoke-audit"
    assert main(_audit_args(smoke_path, paths["root"], audit_dir)) == 0
    first = (
        audit_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME
    ).read_bytes()
    assert main(_audit_args(smoke_path, paths["root"], audit_dir)) == 0
    second = (
        audit_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_STARTUP_SMOKE_AUDIT_REPORT_NAME
    ).read_bytes()
    assert first == second
    assert {path: path.read_bytes() for path in tracked} == before
