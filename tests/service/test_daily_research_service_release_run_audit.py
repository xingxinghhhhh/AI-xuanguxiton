import json
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_release_run import run_daily_research_service_release
from tests.service.test_daily_research_service_release_run import (
    _LiveProcess,
    _ready_probe,
)
from tests.service.test_daily_research_service_release_startup import _prepare_release


def _make_run(tmp_path: Path, *, blocked: bool = False, failed: bool = False) -> tuple[Path, Path]:
    root, manifest, report, audit, *_ = _prepare_release(tmp_path / "release", blocked=blocked)
    if blocked:
        exit_code = run_daily_research_service_release(
            manifest_path=manifest,
            report_path=report,
            audit_report_path=audit,
            artifact_root=root,
            startup_timeout_seconds=1,
            probe_timeout_seconds=0.1,
            output_dir=root / "release-run",
        )[1]
        assert exit_code == 1
    else:
        process = _LiveProcess()
        probe = (
            {
                "status": "blocked",
                "daily_admission_ready": False,
                "decision_ready": False,
                "issues": [{"code": "SERVICE_NOT_READY", "message": "blocked"}],
            }
            if failed
            else _ready_probe()
        )
        with (
            patch(
                "a_share_ai.service.daily_research_service_release_run.subprocess.Popen",
                return_value=process,
            ),
            patch(
                "a_share_ai.service.daily_research_service_release_run.probe_daily_research_service",
                return_value=probe,
            ),
        ):
            exit_code = run_daily_research_service_release(
                manifest_path=manifest,
                report_path=report,
                audit_report_path=audit,
                artifact_root=root,
                startup_timeout_seconds=1,
                probe_timeout_seconds=0.1,
                output_dir=root / "release-run",
            )[1]
        assert exit_code == (1 if failed else 0)
    return root, root / "release-run/daily_research_service_release_run_report.json"


def _audit_args(root: Path, run_report: Path, output_dir: Path) -> list[str]:
    return [
        "audit-daily-research-service-release-run",
        "--run-report",
        str(run_report),
        "--artifact-root",
        str(root),
        "--output-dir",
        str(output_dir),
    ]


def _read_audit(output_dir: Path) -> tuple[dict, bytes]:
    raw = (output_dir / "daily_research_service_release_run_audit_report.json").read_bytes()
    return json.loads(raw), raw


def _assert_self_hash(report: dict) -> None:
    canonical = dict(report)
    canonical["output_sha256"] = None
    expected = sha256_bytes(
        (json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    assert report["output_sha256"] == expected


def _write_hashed(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def test_ready_run_is_independently_audited_without_process_or_http(
    tmp_path: Path,
) -> None:
    root, run_report = _make_run(tmp_path / "ready")
    before = {path: path.read_bytes() for path in root.rglob("*.json")}
    with (
        patch.object(subprocess, "Popen") as popen,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        assert main(_audit_args(root, run_report, root / "run-audit")) == 0
    report, raw = _read_audit(root / "run-audit")
    assert report["audit_version"] == "daily-research-service-release-run-audit-v1"
    assert report["status"] == "ready"
    assert report["audit_ready"] is True
    assert report["run_ready"] is True
    assert report["startup_ready"] is True
    assert report["probe_status"] == "ready"
    assert report["stop_status"] == "controlled"
    assert report["decision_ready"] is False
    assert str(root) not in json.dumps(report, ensure_ascii=False)
    assert raw.endswith(b"\n")
    _assert_self_hash(report)
    popen.assert_not_called()
    urlopen.assert_not_called()
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_valid_blocked_or_failed_run_is_auditable_but_cli_is_nonzero(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    root, run_report = _make_run(
        tmp_path / ("blocked" if kwargs.get("blocked") else "failed"), **kwargs
    )
    assert main(_audit_args(root, run_report, root / "run-audit")) == 1
    report, _ = _read_audit(root / "run-audit")
    assert report["audit_ready"] is True
    assert report["status"] in {"blocked", "failed"}
    assert report["run_ready"] is False
    assert report["decision_ready"] is False
    _assert_self_hash(report)


def test_ready_flag_cannot_be_forged(tmp_path: Path) -> None:
    root, run_report = _make_run(tmp_path / "forged")
    payload = json.loads(run_report.read_text(encoding="utf-8"))
    payload["run_ready"] = False
    payload["run_status"] = "ready"
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    run_report.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    assert main(_audit_args(root, run_report, root / "run-audit")) == 1
    report, _ = _read_audit(root / "run-audit")
    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "STATE_MISMATCH"


def test_upstream_tamper_fails_closed(tmp_path: Path) -> None:
    root, run_report = _make_run(tmp_path / "tamper")
    run_payload = json.loads(run_report.read_text(encoding="utf-8"))
    release_report = root / run_payload["release_report_path"]
    release_payload = json.loads(release_report.read_text(encoding="utf-8"))
    release_payload["status"] = "blocked"
    release_report.write_text(
        json.dumps(release_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    assert main(_audit_args(root, run_report, root / "run-audit")) == 1
    report, _ = _read_audit(root / "run-audit")
    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] in {"HASH_MISMATCH", "SELF_HASH_MISMATCH"}


def test_synchronously_rewritten_release_state_is_still_rejected(tmp_path: Path) -> None:
    root, run_report = _make_run(tmp_path / "sync-rewrite")
    run = json.loads(run_report.read_text(encoding="utf-8"))
    manifest_path = root / run["release_manifest_path"]
    report_path = root / run["release_report_path"]
    audit_path = root / run["release_audit_report_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    release_report = json.loads(report_path.read_text(encoding="utf-8"))
    release_audit = json.loads(audit_path.read_text(encoding="utf-8"))
    for payload in (manifest, release_report, release_audit):
        payload["run_status"] = "blocked"
        payload["release_ready"] = False
        payload["status"] = "blocked"
    _write_hashed(manifest_path, manifest)
    release_report["manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    _write_hashed(report_path, release_report)
    release_audit["manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    release_audit["report_sha256"] = sha256_bytes(report_path.read_bytes())
    _write_hashed(audit_path, release_audit)
    run["release_manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    run["release_report_sha256"] = sha256_bytes(report_path.read_bytes())
    run["release_audit_report_sha256"] = sha256_bytes(audit_path.read_bytes())
    _write_hashed(run_report, run)
    assert main(_audit_args(root, run_report, root / "run-audit")) == 1
    audited, _ = _read_audit(root / "run-audit")
    assert audited["audit_ready"] is False
    assert audited["issues"][0]["code"] == "STATE_MISMATCH"


def test_invalid_upstream_boolean_type_is_rejected(tmp_path: Path) -> None:
    root, run_report = _make_run(tmp_path / "invalid-type")
    run = json.loads(run_report.read_text(encoding="utf-8"))
    manifest_path = root / run["release_manifest_path"]
    report_path = root / run["release_report_path"]
    audit_path = root / run["release_audit_report_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    release_report = json.loads(report_path.read_text(encoding="utf-8"))
    release_audit = json.loads(audit_path.read_text(encoding="utf-8"))
    manifest["release_ready"] = "true"
    _write_hashed(manifest_path, manifest)
    release_report["manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    _write_hashed(report_path, release_report)
    release_audit["manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    release_audit["report_sha256"] = sha256_bytes(report_path.read_bytes())
    _write_hashed(audit_path, release_audit)
    run["release_manifest_sha256"] = sha256_bytes(manifest_path.read_bytes())
    run["release_report_sha256"] = sha256_bytes(report_path.read_bytes())
    run["release_audit_report_sha256"] = sha256_bytes(audit_path.read_bytes())
    _write_hashed(run_report, run)
    assert main(_audit_args(root, run_report, root / "run-audit")) == 1
    audited, _ = _read_audit(root / "run-audit")
    assert audited["audit_ready"] is False
    assert audited["issues"][0]["code"] == "FIELD_MISMATCH"


def test_external_run_report_is_rejected_without_reading_it(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    run_report = outside / "daily_research_service_release_run_report.json"
    run_report.write_text('{"secret":"must not be read"}\n', encoding="utf-8")
    assert main(_audit_args(root, run_report, root / "run-audit")) == 1
    report, _ = _read_audit(root / "run-audit")
    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ROOT"


def test_invalid_configuration_returns_two(tmp_path: Path) -> None:
    root, run_report = _make_run(tmp_path / "config")
    assert main(_audit_args(root, run_report, tmp_path / "outside-output")) == 2
