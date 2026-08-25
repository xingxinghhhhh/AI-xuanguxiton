import json
import os
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_release import (
    DAILY_RESEARCH_SERVICE_RELEASE_VERSION,
    build_daily_research_service_release,
)
from tests.service.test_daily_research_service_run import _root_and_gate
from tests.service.test_daily_research_service_run_audit import _audit, _run_report


def _prepare_chain(tmp_path: Path, *, blocked: bool = False, failed: bool = False):
    root, gate_dir, gate_audit, _ = _root_and_gate(tmp_path, blocked=blocked)
    updates = {}
    if blocked:
        updates = {
            "startup_status": "blocked",
            "probe_status": None,
            "probe_exit_code": None,
            "service_stopped": False,
            "run_ready": False,
            "issues": [{"code": "STARTUP_NOT_READY", "message": "launch gate is blocked"}],
        }
    if failed:
        updates = {
            "probe_status": "invalid",
            "probe_exit_code": 1,
            "service_stopped": False,
            "run_ready": False,
            "issues": [{"code": "SERVICE_STOP_FAILED", "message": "service did not stop"}],
        }
    run_path = _run_report(root, gate_dir, gate_audit, **updates)
    audit_path = root / "run-audit" / "daily_research_service_run_audit_report.json"
    _audit(run_path, root, "run-audit")
    return root, run_path, audit_path


def _self_hash(payload: dict) -> None:
    canonical = dict(payload)
    canonical["output_sha256"] = None
    assert payload["output_sha256"] == sha256_bytes(
        (json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def test_ready_chain_builds_deterministic_release_without_network_or_service_io(
    tmp_path: Path,
) -> None:
    root, run_path, audit_path = _prepare_chain(tmp_path / "ready")
    run_before = run_path.read_bytes()
    audit_before = audit_path.read_bytes()
    with (
        patch("subprocess.Popen") as popen,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        manifest, report, code = build_daily_research_service_release(
            run_report_path=run_path,
            run_audit_report_path=audit_path,
            artifact_root=root,
            output_dir=root / "release",
        )
        first = (root / "release/daily_research_service_release_report.json").read_bytes()
        _, _, second_code = build_daily_research_service_release(
            run_report_path=run_path,
            run_audit_report_path=audit_path,
            artifact_root=root,
            output_dir=root / "release",
        )
        second = (root / "release/daily_research_service_release_report.json").read_bytes()

    popen.assert_not_called()
    urlopen.assert_not_called()
    assert code == 0
    assert second_code == 0
    assert manifest["release_version"] == DAILY_RESEARCH_SERVICE_RELEASE_VERSION
    assert manifest["status"] == "ready"
    assert manifest["release_ready"] is True
    assert manifest["decision_ready"] is False
    assert report["manifest_sha256"] == sha256_bytes(
        (root / "release/daily_research_service_release_manifest.json").read_bytes()
    )
    _self_hash(manifest)
    _self_hash(report)
    assert first == second
    assert run_path.read_bytes() == run_before
    assert audit_path.read_bytes() == audit_before


def test_failed_and_blocked_chains_are_not_publishable(tmp_path: Path) -> None:
    failed_root, failed_run, failed_audit = _prepare_chain(tmp_path / "failed", failed=True)
    failed_manifest, _, failed_code = build_daily_research_service_release(
        run_report_path=failed_run,
        run_audit_report_path=failed_audit,
        artifact_root=failed_root,
        output_dir=failed_root / "release",
    )
    assert failed_code == 1
    assert failed_manifest["status"] == "blocked"
    assert failed_manifest["release_ready"] is False
    assert failed_manifest["issues"][0]["code"] == "SERVICE_STOP_FAILED"

    blocked_root, blocked_run, blocked_audit = _prepare_chain(tmp_path / "blocked", blocked=True)
    blocked_manifest, _, blocked_code = build_daily_research_service_release(
        run_report_path=blocked_run,
        run_audit_report_path=blocked_audit,
        artifact_root=blocked_root,
        output_dir=blocked_root / "release",
    )
    assert blocked_code == 1
    assert blocked_manifest["status"] == "blocked"
    assert blocked_manifest["release_ready"] is False


def test_tamper_unknown_fields_time_and_boundaries_fail_closed(tmp_path: Path) -> None:
    root, run_path, audit_path = _prepare_chain(tmp_path / "tamper")
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    audit_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest, _, code = build_daily_research_service_release(
        run_report_path=run_path,
        run_audit_report_path=audit_path,
        artifact_root=root,
        output_dir=root / "release",
    )
    assert code == 1
    assert manifest["status"] == "invalid"
    assert manifest["issues"][0]["code"] == "UNKNOWN_FIELD"

    root, run_path, audit_path = _prepare_chain(tmp_path / "time")
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload["as_of"] = "2026-08-11T08:00:00+00:00"
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    run_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest, _, code = build_daily_research_service_release(
        run_report_path=run_path,
        run_audit_report_path=audit_path,
        artifact_root=root,
        output_dir=root / "release",
    )
    assert code == 1
    assert manifest["issues"][0]["code"] == "HASH_MISMATCH"


def test_cli_returns_zero_for_ready_and_two_for_invalid_configuration(tmp_path: Path) -> None:
    root, run_path, audit_path = _prepare_chain(tmp_path / "cli")
    assert (
        main(
            [
                "build-daily-research-service-release",
                "--run-report",
                str(run_path),
                "--run-audit-report",
                str(audit_path),
                "--artifact-root",
                str(root),
                "--output-dir",
                str(root / "release"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "build-daily-research-service-release",
                "--run-report",
                str(run_path),
                "--run-audit-report",
                str(audit_path),
                "--artifact-root",
                str(root),
                "--output-dir",
                str(tmp_path / "outside"),
            ]
        )
        == 2
    )


def test_symlink_outside_artifact_root_is_rejected(tmp_path: Path) -> None:
    root, run_path, audit_path = _prepare_chain(tmp_path / "symlink")
    outside = tmp_path / "outside" / "daily_research_service_run_report.json"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(run_path.read_bytes())
    linked = root / "linked" / "daily_research_service_run_report.json"
    linked.parent.mkdir(parents=True)
    try:
        os.symlink(outside, linked)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this Windows host: {exc}")

    manifest, _, code = build_daily_research_service_release(
        run_report_path=linked,
        run_audit_report_path=audit_path,
        artifact_root=root,
        output_dir=root / "release",
    )
    assert code == 1
    assert manifest["issues"][0]["code"] == "PATH_OUTSIDE_ROOT"
