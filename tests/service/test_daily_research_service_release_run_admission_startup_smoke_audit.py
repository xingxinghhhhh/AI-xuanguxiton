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


def _refresh_pretty_hash(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    path.write_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
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


def test_status_chain_mismatch_is_invalid(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "status-chain")
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "admission_status": "blocked",
            "audit_status": "blocked",
            "startup_status": "blocked",
            "startup_ready": False,
            "service_started": False,
            "probe_status": "not_started",
            "probe_exit_code": None,
            "stop_status": "not_attempted",
            "service_stopped": False,
            "status": "blocked",
            "run_ready": False,
            "issues": [{"code": "MUTATED", "message": "status chain was mutated"}],
        }
    )
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


@pytest.mark.parametrize("probe_status", ["failed", "timeout", "service_exited"])
def test_probe_failure_after_ready_start_is_auditable(
    tmp_path: Path, probe_status: str
) -> None:
    paths = _prepare_startup(tmp_path / probe_status)
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "probe_status": probe_status,
            "probe_exit_code": (
                None
                if probe_status == "timeout"
                else 0
                if probe_status == "service_exited"
                else 1
            ),
            "status": "failed",
            "run_ready": False,
            "issues": [{"code": "PROBE_FAILED", "message": "probe did not complete"}],
        }
    )
    _refresh_compact_hash(smoke_path, payload)
    report, code = audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "smoke-audit",
    )
    assert code == 1
    assert report["status"] == "failed"
    assert report["audit_ready"] is True
    assert report["run_ready"] is False
    assert report["decision_ready"] is False


@pytest.mark.parametrize(
    ("probe_status", "probe_exit_code", "stop_status", "service_stopped"),
    [
        ("timeout", 1, "controlled", True),
        ("service_exited", 1, "controlled", True),
        ("failed", None, "failed", True),
        ("failed", None, "controlled", False),
    ],
)
def test_invalid_probe_failure_state_is_rejected(
    tmp_path: Path,
    probe_status: str,
    probe_exit_code: int | None,
    stop_status: str,
    service_stopped: bool,
) -> None:
    paths = _prepare_startup(tmp_path / f"{probe_status}-{stop_status}")
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "probe_status": probe_status,
            "probe_exit_code": probe_exit_code,
            "stop_status": stop_status,
            "service_stopped": service_stopped,
            "status": "failed",
            "run_ready": False,
            "issues": [{"code": "PROBE_FAILED", "message": "invalid probe state"}],
        }
    )
    _refresh_compact_hash(smoke_path, payload)
    report, code = audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "smoke-audit",
    )
    assert code == 1
    assert report["status"] == "invalid"
    assert report["audit_ready"] is False


def test_array_enum_is_rejected_without_type_error(tmp_path: Path) -> None:
    paths = _prepare_startup(tmp_path / "array-enum")
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    payload = json.loads(smoke_path.read_text(encoding="utf-8"))
    payload["status"] = []
    _refresh_compact_hash(smoke_path, payload)
    report, code = audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "smoke-audit",
    )
    assert code == 1
    assert report["status"] == "invalid"
    assert report["audit_ready"] is False


@pytest.mark.parametrize(
    "target",
    [
        "admission",
        "admission_report",
        "admission_audit",
        "release_manifest",
        "release_report",
        "release_audit",
    ],
)
def test_each_upstream_receipt_schema_is_audited_independently(
    tmp_path: Path, target: str
) -> None:
    paths = _prepare_startup(tmp_path / target)
    smoke_dir = paths["root"] / "smoke"
    _run_smoke(paths, smoke_dir)
    smoke_path = (
        smoke_dir / "daily_research_service_release_run_admission_startup_smoke_report.json"
    )
    target_path = {
        "admission": paths["admission"],
        "admission_report": paths["admission_report"],
        "admission_audit": paths["admission_audit"],
        "release_manifest": paths["manifest"],
        "release_report": paths["release_report"],
        "release_audit": paths["release_audit"],
    }[target]
    target_payload = json.loads(target_path.read_text(encoding="utf-8"))
    target_payload["decision_ready"] = True
    _refresh_pretty_hash(target_path, target_payload)
    target_sha = sha256_bytes(target_path.read_bytes())
    sha_field = {
        "admission": "admission_sha256",
        "admission_report": "admission_report_sha256",
        "admission_audit": "admission_audit_sha256",
        "release_manifest": "release_manifest_sha256",
        "release_report": "release_report_sha256",
        "release_audit": "release_audit_sha256",
    }[target]
    for report_path in (
        smoke_dir
        / "preflight"
        / "daily_research_service_release_run_admission_startup_report.json",
        smoke_dir
        / "startup"
        / "daily_research_service_release_run_admission_startup_report.json",
        smoke_path,
    ):
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        report_payload[sha_field] = target_sha
        if report_path == smoke_path:
            _refresh_compact_hash(report_path, report_payload)
        else:
            _refresh_compact_hash(report_path, report_payload)
    report, code = audit_daily_research_service_release_run_admission_startup_smoke(
        smoke_report_path=smoke_path,
        artifact_root=paths["root"],
        output_dir=paths["root"] / "smoke-audit",
    )
    assert code == 1
    assert report["status"] == "invalid"
    assert report["audit_ready"] is False
    assert report["run_ready"] is False


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
