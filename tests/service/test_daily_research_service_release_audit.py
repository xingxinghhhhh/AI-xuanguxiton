import json
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_release import (
    build_daily_research_service_release,
)
from a_share_ai.service.daily_research_service_release_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION,
    audit_daily_research_service_release,
)
from tests.service.test_daily_research_service_release import _prepare_chain


def _prepare_release(tmp_path: Path, *, blocked: bool = False, failed: bool = False):
    root, run_path, audit_path = _prepare_chain(tmp_path, blocked=blocked, failed=failed)
    release_dir = root / "release"
    build_daily_research_service_release(
        run_report_path=run_path,
        run_audit_report_path=audit_path,
        artifact_root=root,
        output_dir=release_dir,
    )
    return (
        root,
        release_dir / "daily_research_service_release_manifest.json",
        release_dir / "daily_research_service_release_report.json",
        run_path,
        audit_path,
    )


def _sign(payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def test_ready_release_is_independently_audited_without_service_or_network_io(
    tmp_path: Path,
) -> None:
    root, manifest_path, report_path, run_path, audit_path = _prepare_release(tmp_path / "ready")
    before = {
        path: path.read_bytes() for path in (manifest_path, report_path, run_path, audit_path)
    }
    with (
        patch("subprocess.Popen") as popen,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        first = audit_daily_research_service_release(
            manifest_path=manifest_path,
            report_path=report_path,
            artifact_root=root,
            output_dir=root / "release-audit",
        )
        second = audit_daily_research_service_release(
            manifest_path=manifest_path,
            report_path=report_path,
            artifact_root=root,
            output_dir=root / "release-audit-second",
        )
    popen.assert_not_called()
    urlopen.assert_not_called()
    assert first["audit_version"] == DAILY_RESEARCH_SERVICE_RELEASE_AUDIT_VERSION
    assert first["audit_ready"] is True
    assert first["status"] == "ready"
    assert first["release_ready"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert {path: path.read_bytes() for path in before} == before


def test_blocked_and_failed_releases_are_auditable_but_not_promoted(tmp_path: Path) -> None:
    for name, kwargs in (("blocked", {"blocked": True}), ("failed", {"failed": True})):
        root, manifest_path, report_path, _, _ = _prepare_release(tmp_path / name, **kwargs)
        result = audit_daily_research_service_release(
            manifest_path=manifest_path,
            report_path=report_path,
            artifact_root=root,
            output_dir=root / "release-audit",
        )
        assert result["audit_ready"] is True
        assert result["status"] == "blocked"
        assert result["release_ready"] is False


def test_tamper_and_false_ready_fail_closed(tmp_path: Path) -> None:
    root, manifest_path, report_path, _, _ = _prepare_release(tmp_path / "tamper")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_ready"] = False
    manifest["status"] = "blocked"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["release_ready"] = False
    report["status"] = "blocked"
    _sign(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report["manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    _sign(report)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    result = audit_daily_research_service_release(
        manifest_path=manifest_path,
        report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert result["audit_ready"] is False
    assert result["issues"][0]["code"] == "STATE_MISMATCH"

    root, manifest_path, report_path, _, _ = _prepare_release(tmp_path / "schema")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    _sign(payload)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    result = audit_daily_research_service_release(
        manifest_path=manifest_path,
        report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert result["audit_ready"] is False
    assert result["issues"][0]["code"] == "UNKNOWN_FIELD"


@pytest.mark.parametrize(
    ("run_updates", "audit_updates", "expected_code"),
    [
        (
            {"startup_status": "invalid", "probe_status": "invalid", "probe_exit_code": 1},
            {"startup_status": "invalid", "probe_status": "invalid", "probe_exit_code": 1},
            "STATE_MISMATCH",
        ),
        ({}, {"probe_status": "invalid", "probe_exit_code": 1}, "FIELD_MISMATCH"),
    ],
)
def test_upstream_state_chain_is_recomputed_and_aligned(
    tmp_path: Path,
    run_updates: dict[str, object],
    audit_updates: dict[str, object],
    expected_code: str,
) -> None:
    root, manifest_path, report_path, run_path, audit_path = _prepare_release(
        tmp_path / "upstream-state"
    )
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run.update(run_updates)
    _sign(run)
    run_path.write_text(
        json.dumps(run, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit.update(audit_updates)
    audit["run_report_sha256"] = sha256_bytes(run_path.read_bytes())
    _sign(audit)
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_report_sha256"] = sha256_bytes(run_path.read_bytes())
    manifest["run_audit_report_sha256"] = sha256_bytes(audit_path.read_bytes())
    _sign(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    report["run_report_sha256"] = manifest["run_report_sha256"]
    report["run_audit_report_sha256"] = manifest["run_audit_report_sha256"]
    _sign(report)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    result = audit_daily_research_service_release(
        manifest_path=manifest_path,
        report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert result["audit_ready"] is False
    assert result["issues"][0]["code"] == expected_code


@pytest.mark.parametrize(
    ("release_kwargs", "field", "value"),
    [
        ({}, "run_ready", False),
        ({"blocked": True}, "service_stopped", True),
        ({"failed": True}, "run_ready", True),
    ],
)
def test_release_readiness_summary_is_bound_to_node66(
    tmp_path: Path,
    release_kwargs: dict[str, bool],
    field: str,
    value: bool,
) -> None:
    root, manifest_path, report_path, _, _ = _prepare_release(
        tmp_path / "release-summary", **release_kwargs
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    _sign(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report[field] = value
    report["manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    _sign(report)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    result = audit_daily_research_service_release(
        manifest_path=manifest_path,
        report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert result["audit_ready"] is False
    assert result["issues"][0]["code"] == "FIELD_MISMATCH"


def test_missing_input_and_cli_configuration_are_reported(tmp_path: Path) -> None:
    root, manifest_path, report_path, _, _ = _prepare_release(tmp_path / "cli")
    missing = audit_daily_research_service_release(
        manifest_path=root / "missing/daily_research_service_release_manifest.json",
        report_path=report_path,
        artifact_root=root,
        output_dir=root / "missing-audit",
    )
    assert missing["audit_ready"] is False
    assert missing["issues"][0]["code"] == "INPUT_UNAVAILABLE"
    assert (
        main(
            [
                "audit-daily-research-service-release",
                "--manifest",
                str(manifest_path),
                "--report",
                str(report_path),
                "--artifact-root",
                str(root),
                "--output-dir",
                str(root / "cli-audit"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "audit-daily-research-service-release",
                "--manifest",
                str(manifest_path),
                "--report",
                str(report_path),
                "--artifact-root",
                str(root),
                "--output-dir",
                str(tmp_path / "outside"),
            ]
        )
        == 2
    )


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    import os

    root, manifest_path, report_path, _, _ = _prepare_release(tmp_path / "symlink")
    outside = tmp_path / "outside" / manifest_path.name
    outside.parent.mkdir(parents=True)
    outside.write_bytes(manifest_path.read_bytes())
    linked = root / "linked" / manifest_path.name
    linked.parent.mkdir(parents=True)
    try:
        os.symlink(outside, linked)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this Windows host: {exc}")
    result = audit_daily_research_service_release(
        manifest_path=linked,
        report_path=report_path,
        artifact_root=root,
        output_dir=root / "audit",
    )
    assert result["audit_ready"] is False
    assert result["issues"][0]["code"] == "PATH_OUTSIDE_ROOT"
