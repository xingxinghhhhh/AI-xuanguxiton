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
from a_share_ai.service.daily_research_service_release_run_audit import (
    DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME,
    audit_daily_research_service_release_run,
)
from tests.service.test_daily_research_service_release_run_audit import _make_run


def _make_admission(
    tmp_path: Path, *, blocked: bool = False, failed: bool = False
) -> tuple[Path, Path, Path]:
    root, run_report = _make_run(tmp_path / "run", blocked=blocked, failed=failed)
    audit_dir = root / "run-audit"
    audit_daily_research_service_release_run(
        run_report_path=run_report,
        artifact_root=root,
        output_dir=audit_dir,
    )
    return root, run_report, audit_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_AUDIT_REPORT_NAME


def _args(root: Path, run_report: Path, audit_report: Path, output_dir: Path) -> list[str]:
    return [
        "build-daily-research-service-release-run-admission",
        "--run-report",
        str(run_report),
        "--run-audit-report",
        str(audit_report),
        "--artifact-root",
        str(root),
        "--output-dir",
        str(output_dir),
    ]


def _read_outputs(output_dir: Path) -> tuple[dict, dict, bytes, bytes]:
    admission_raw = (output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_NAME).read_bytes()
    report_raw = (
        output_dir / DAILY_RESEARCH_SERVICE_RELEASE_RUN_ADMISSION_REPORT_NAME
    ).read_bytes()
    return json.loads(admission_raw), json.loads(report_raw), admission_raw, report_raw


def _assert_self_hash(payload: dict) -> None:
    canonical = dict(payload)
    declared = canonical.pop("output_sha256")
    canonical["output_sha256"] = None
    assert declared == sha256_bytes(
        (json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def _write_hashed(path: Path, payload: dict) -> None:
    payload["output_sha256"] = None
    payload["output_sha256"] = sha256_bytes(
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def test_ready_run_builds_only_when_node71_and_node72_are_ready(tmp_path: Path) -> None:
    root, run_report, audit_report = _make_admission(tmp_path / "ready")
    before = {path: path.read_bytes() for path in root.rglob("*.json")}
    with (
        patch.object(subprocess, "Popen") as popen,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        assert main(_args(root, run_report, audit_report, root / "admission")) == 0
    admission, report, admission_raw, report_raw = _read_outputs(root / "admission")
    assert admission["admission_ready"] is True
    assert admission["audit_ready"] is True
    assert admission["status"] == "ready"
    assert admission["decision_ready"] is False
    assert report["admission_path"] == "admission/daily_research_service_release_run_admission.json"
    assert report["admission_sha256"] == sha256_bytes(admission_raw)
    assert str(root) not in admission_raw.decode("utf-8")
    assert str(root) not in report_raw.decode("utf-8")
    _assert_self_hash(admission)
    _assert_self_hash(report)
    popen.assert_not_called()
    urlopen.assert_not_called()
    assert {
        path: path.read_bytes()
        for path in root.rglob("*.json")
        if path
        not in {
            root / "admission/daily_research_service_release_run_admission.json",
            root / "admission/daily_research_service_release_run_admission_report.json",
        }
    } == before


@pytest.mark.parametrize("kwargs", [{"blocked": True}, {"failed": True}])
def test_blocked_or_failed_chain_is_auditable_but_not_admitted(
    tmp_path: Path, kwargs: dict[str, bool]
) -> None:
    root, run_report, audit_report = _make_admission(tmp_path, **kwargs)
    assert main(_args(root, run_report, audit_report, root / "admission")) == 1
    admission, report, _, _ = _read_outputs(root / "admission")
    assert admission["audit_ready"] is True
    assert admission["admission_ready"] is False
    assert admission["status"] in {"blocked", "failed"}
    assert admission["decision_ready"] is False
    _assert_self_hash(admission)
    _assert_self_hash(report)


@pytest.mark.parametrize("target", ["run", "audit"])
def test_tampered_input_fails_closed(tmp_path: Path, target: str) -> None:
    root, run_report, audit_report = _make_admission(tmp_path / target)
    path = run_report if target == "run" else audit_report
    payload = json.loads(path.read_text(encoding="utf-8"))
    if target == "run":
        payload["run_ready"] = False
    else:
        payload["audit_ready"] = False
    _write_hashed(path, payload)
    assert main(_args(root, run_report, audit_report, root / "admission")) == 1
    admission, _, _, _ = _read_outputs(root / "admission")
    assert admission["admission_ready"] is False
    assert admission["audit_ready"] is False
    assert admission["status"] == "invalid"


def test_state_mismatch_and_path_escape_fail_closed(tmp_path: Path) -> None:
    root, run_report, audit_report = _make_admission(tmp_path / "mismatch")
    audit = json.loads(audit_report.read_text(encoding="utf-8"))
    audit["service_stopped"] = False
    _write_hashed(audit_report, audit)
    assert main(_args(root, run_report, audit_report, root / "state-mismatch")) == 1
    mismatch, _, _, _ = _read_outputs(root / "state-mismatch")
    assert mismatch["issues"][0]["code"] == "STATE_MISMATCH"

    audit = json.loads(audit_report.read_text(encoding="utf-8"))
    audit["run_report_path"] = str(tmp_path / "outside" / run_report.name)
    _write_hashed(audit_report, audit)
    assert main(_args(root, run_report, audit_report, root / "path-mismatch")) == 1
    escaped, _, _, _ = _read_outputs(root / "path-mismatch")
    assert escaped["issues"][0]["code"] == "PATH_OUTSIDE_ROOT"


@pytest.mark.parametrize("invalid_value", [[], {}, None, 1, "not-a-status"])
def test_invalid_upstream_enum_type_is_rejected(tmp_path: Path, invalid_value: object) -> None:
    root, run_report, audit_report = _make_admission(tmp_path / "enum")
    audit = json.loads(audit_report.read_text(encoding="utf-8"))
    audit["status"] = invalid_value
    _write_hashed(audit_report, audit)
    assert main(_args(root, run_report, audit_report, root / "admission")) == 1
    admission, _, _, _ = _read_outputs(root / "admission")
    assert admission["audit_ready"] is False
    assert admission["issues"][0]["code"] == "FIELD_MISMATCH"


@pytest.mark.parametrize("invalid_value", [1, None, "true"])
def test_invalid_upstream_boolean_type_is_rejected(tmp_path: Path, invalid_value: object) -> None:
    root, run_report, audit_report = _make_admission(tmp_path / "boolean")
    audit = json.loads(audit_report.read_text(encoding="utf-8"))
    audit["audit_ready"] = invalid_value
    _write_hashed(audit_report, audit)
    assert main(_args(root, run_report, audit_report, root / "admission")) == 1
    admission, _, _, _ = _read_outputs(root / "admission")
    assert admission["audit_ready"] is False
    assert admission["issues"][0]["code"] == "FIELD_MISMATCH"


def test_unknown_field_and_invalid_configuration_have_stable_codes(tmp_path: Path) -> None:
    root, run_report, audit_report = _make_admission(tmp_path / "config")
    audit = json.loads(audit_report.read_text(encoding="utf-8"))
    audit["unexpected"] = True
    _write_hashed(audit_report, audit)
    assert main(_args(root, run_report, audit_report, root / "unknown")) == 1
    unknown, _, _, _ = _read_outputs(root / "unknown")
    assert unknown["issues"][0]["code"] == "UNKNOWN_FIELD"
    assert main(_args(root, run_report, audit_report, tmp_path / "outside-output")) == 2
    assert (
        main(_args(tmp_path / "missing-root", run_report, audit_report, root / "invalid-root")) == 2
    )


def test_admission_output_is_deterministic(tmp_path: Path) -> None:
    root, run_report, audit_report = _make_admission(tmp_path / "deterministic")
    output_dir = root / "admission"
    assert main(_args(root, run_report, audit_report, output_dir)) == 0
    first = _read_outputs(output_dir)[2:]
    assert main(_args(root, run_report, audit_report, output_dir)) == 0
    second = _read_outputs(output_dir)[2:]
    assert first == second
