import json
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_release_run_admission import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
)
from a_share_ai.service.daily_research_service_release_run_admission_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME,
)
from tests.service.test_daily_research_service_release_run_admission import _make_admission


def _make_pair(
    tmp_path: Path, *, blocked: bool = False, failed: bool = False
) -> tuple[Path, Path, Path]:
    root, run_report, audit_report = _make_admission(
        tmp_path / "inputs", blocked=blocked, failed=failed
    )
    admission_dir = root / "admission"
    assert main(
        [
            "build-daily-research-service-release-run-admission",
            "--run-report",
            str(run_report),
            "--run-audit-report",
            str(audit_report),
            "--artifact-root",
            str(root),
            "--output-dir",
            str(admission_dir),
        ]
    ) == (1 if blocked or failed else 0)
    return (
        root,
        admission_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME,
        admission_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME,
    )


def _args(root: Path, admission: Path, report: Path, output_dir: Path) -> list[str]:
    return [
        "audit-daily-research-service-release-run-admission",
        "--admission",
        str(admission),
        "--report",
        str(report),
        "--artifact-root",
        str(root),
        "--output-dir",
        str(output_dir),
    ]


def _read_audit(output_dir: Path) -> tuple[dict, bytes]:
    raw = (output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_AUDIT_REPORT_NAME).read_bytes()
    return json.loads(raw), raw


def _write_hashed(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _assert_self_hash(payload: dict) -> None:
    canonical = dict(payload)
    declared = canonical.pop("output_sha256")
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        (json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def test_ready_pair_is_audited_without_upstream_or_service_side_effects(tmp_path: Path) -> None:
    root, admission, report = _make_pair(tmp_path / "ready")
    before = {path: path.read_bytes() for path in root.rglob("*.json")}
    with (
        patch.object(subprocess, "Popen") as popen,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        assert main(_args(root, admission, report, root / "audit")) == 0
    audited, raw = _read_audit(root / "audit")
    assert audited["audit_ready"] is True
    assert audited["status"] == "ready"
    assert audited["admission_ready"] is True
    assert audited["decision_ready"] is False
    assert (
        audited["admission_path"] == "admission/daily_research_service_release_run_admission.json"
    )
    assert (
        audited["report_path"]
        == "admission/daily_research_service_release_run_admission_report.json"
    )
    assert str(root) not in raw.decode("utf-8")
    _assert_self_hash(audited)
    popen.assert_not_called()
    urlopen.assert_not_called()
    assert {
        path: path.read_bytes()
        for path in root.rglob("*.json")
        if path != root / "audit/daily_research_service_release_run_admission_audit_report.json"
    } == before


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_blocked_or_failed_pair_is_auditable_but_not_ready(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    root, admission, report = _make_pair(tmp_path, **kwargs)
    assert main(_args(root, admission, report, root / "audit")) == 1
    audited, _ = _read_audit(root / "audit")
    assert audited["audit_ready"] is True
    assert audited["status"] in {"blocked", "failed"}
    assert audited["admission_ready"] is False
    _assert_self_hash(audited)


def test_invalid_pair_is_reported_fail_closed_without_upstream_reads(tmp_path: Path) -> None:
    root, admission, report = _make_pair(tmp_path / "invalid", failed=True)
    admission_payload = json.loads(admission.read_text(encoding="utf-8"))
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    admission_payload["status"] = "invalid"
    admission_payload["run_status"] = "invalid"
    admission_payload["audit_ready"] = False
    admission_payload["admission_ready"] = False
    admission_payload["issues"] = [{"code": "UPSTREAM_INVALID", "message": "invalid input"}]
    admission_payload["run_version"] = None
    admission_payload["release_version"] = None
    admission_payload["startup_version"] = None
    admission_payload["audit_version"] = None
    admission_payload["run_report_path"] = None
    admission_payload["run_report_sha256"] = None
    admission_payload["run_audit_report_path"] = None
    admission_payload["run_audit_report_sha256"] = None
    admission_payload["symbol"] = None
    admission_payload["as_of"] = None
    admission_payload["evaluation_at"] = None
    admission_payload["startup_status"] = None
    admission_payload["probe_status"] = None
    admission_payload["stop_status"] = None
    admission_payload["probe_exit_code"] = None
    _write_hashed(admission, admission_payload)
    report_payload.update(admission_payload)
    report_payload["admission_sha256"] = sha256_bytes(admission.read_bytes())
    _write_hashed(report, report_payload)
    with patch.object(subprocess, "Popen") as popen:
        assert main(_args(root, admission, report, root / "audit")) == 1
    audited, _ = _read_audit(root / "audit")
    assert audited["audit_ready"] is False
    assert audited["status"] == "invalid"
    assert audited["admission_ready"] is False
    _assert_self_hash(audited)
    popen.assert_not_called()


@pytest.mark.parametrize("target", ["admission", "report"])
def test_single_input_tamper_fails_closed(tmp_path: Path, target: str) -> None:
    root, admission, report = _make_pair(tmp_path / target)
    path = admission if target == "admission" else report
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "blocked"
    _write_hashed(path, payload)
    assert main(_args(root, admission, report, root / "audit")) == 1
    audited, _ = _read_audit(root / "audit")
    assert audited["audit_ready"] is False
    assert audited["status"] == "invalid"


def test_admission_report_sha_and_path_bindings_are_required(tmp_path: Path) -> None:
    root, admission, report = _make_pair(tmp_path / "binding")
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["admission_sha256"] = "0" * 64
    _write_hashed(report, payload)
    assert main(_args(root, admission, report, root / "sha-audit")) == 1
    audited, _ = _read_audit(root / "sha-audit")
    assert audited["issues"][0]["code"] == "HASH_MISMATCH"

    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["admission_path"] = "outside/admission.json"
    _write_hashed(report, payload)
    assert main(_args(root, admission, report, root / "path-audit")) == 1
    audited, _ = _read_audit(root / "path-audit")
    assert audited["issues"][0]["code"] == "CHAIN_MISMATCH"


@pytest.mark.parametrize("field", ["status", "audit_ready", "admission_ready", "run_ready"])
def test_internal_state_types_and_derivation_fail_closed(tmp_path: Path, field: str) -> None:
    root, admission, report = _make_pair(tmp_path / "state")
    admission_payload = json.loads(admission.read_text(encoding="utf-8"))
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    invalid_value: object = [] if field == "status" else "true"
    admission_payload[field] = invalid_value
    report_payload[field] = invalid_value
    _write_hashed(admission, admission_payload)
    report_payload["admission_sha256"] = sha256_bytes(admission.read_bytes())
    _write_hashed(report, report_payload)
    assert main(_args(root, admission, report, root / "audit")) == 1
    audited, _ = _read_audit(root / "audit")
    assert audited["audit_ready"] is False


def test_declared_upstream_paths_and_shas_are_validated_without_reading_upstream(
    tmp_path: Path,
) -> None:
    root, admission, report = _make_pair(tmp_path / "declared")
    admission_payload = json.loads(admission.read_text(encoding="utf-8"))
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    admission_payload["run_report_path"] = "../outside.json"
    report_payload["run_report_path"] = admission_payload["run_report_path"]
    _write_hashed(admission, admission_payload)
    report_payload["admission_sha256"] = sha256_bytes(admission.read_bytes())
    _write_hashed(report, report_payload)
    assert main(_args(root, admission, report, root / "audit")) == 1
    audited, _ = _read_audit(root / "audit")
    assert audited["issues"][0]["code"] == "PATH_OUTSIDE_ROOT"


def test_unknown_fields_configuration_and_determinism(tmp_path: Path) -> None:
    root, admission, report = _make_pair(tmp_path / "config")
    payload = json.loads(admission.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    _write_hashed(admission, payload)
    assert main(_args(root, admission, report, root / "unknown")) == 1
    audited, _ = _read_audit(root / "unknown")
    assert audited["issues"][0]["code"] == "UNKNOWN_FIELD"
    assert main(_args(root, admission, report, root / "deterministic")) == 1
    first = _read_audit(root / "deterministic")[1]
    assert main(_args(root, admission, report, root / "deterministic")) == 1
    assert _read_audit(root / "deterministic")[1] == first
    assert main(_args(tmp_path / "missing-root", admission, report, root / "invalid-root")) == 2
    assert main(_args(root, admission, report, tmp_path / "outside-output")) == 2
