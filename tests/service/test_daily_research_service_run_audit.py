import json
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_run_audit import (
    DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION,
    audit_daily_research_service_run,
)
from tests.service.test_daily_research_service_run import _root_and_gate


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _sign(payload: dict[str, object]) -> dict[str, object]:
    result = dict(payload)
    result["output_sha256"] = None
    result["output_sha256"] = sha256_bytes(
        (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )
    return result


def _run_report(root: Path, gate_dir: Path, audit_path: Path, **updates: object) -> Path:
    gate_path = gate_dir / "daily_research_service_launch_gate.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    payload: dict[str, object] = {
        "run_version": "daily-research-service-run-v1",
        "gate_path": gate_path.relative_to(root).as_posix(),
        "gate_sha256": sha256_bytes(gate_path.read_bytes()),
        "audit_path": audit_path.relative_to(root).as_posix(),
        "audit_sha256": sha256_bytes(audit_path.read_bytes()),
        "symbol": audit["symbol"],
        "as_of": audit["as_of"],
        "evaluation_at": audit["evaluation_at"],
        "startup_status": "ready",
        "probe_status": "ready",
        "probe_exit_code": 0,
        "service_stopped": True,
        "run_ready": True,
        "issues": [],
        "decision_ready": False,
        "output_sha256": None,
    }
    payload.update(updates)
    path = root / "service-run" / "daily_research_service_run_report.json"
    _write_json(path, _sign(payload))
    return path


def _audit(run_path: Path, root: Path, output_name: str = "audit") -> dict[str, object]:
    return audit_daily_research_service_run(
        run_report_path=run_path,
        artifact_root=root,
        output_dir=root / output_name,
    )


def test_ready_run_is_independently_audited_deterministically_without_service_io(
    tmp_path: Path,
) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path / "ready")
    run_path = _run_report(root, gate_dir, audit_path)
    run_before = run_path.read_bytes()
    gate_before = (gate_dir / "daily_research_service_launch_gate.json").read_bytes()
    audit_before = audit_path.read_bytes()

    with (
        patch("a_share_ai.service.daily_research_service_run.subprocess.Popen") as popen,
        patch.object(urllib.request, "urlopen") as urlopen,
    ):
        first = _audit(run_path, root)
        second = _audit(run_path, root, "audit-second")

    popen.assert_not_called()
    urlopen.assert_not_called()
    assert first["audit_version"] == DAILY_RESEARCH_SERVICE_RUN_AUDIT_VERSION
    assert first["audit_ready"] is True
    assert first["run_status"] == "ready"
    assert first["run_ready"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert (root / "audit/daily_research_service_run_audit_report.json").read_bytes() == (
        root / "audit-second/daily_research_service_run_audit_report.json"
    ).read_bytes()
    assert run_path.read_bytes() == run_before
    assert (gate_dir / "daily_research_service_launch_gate.json").read_bytes() == gate_before
    assert audit_path.read_bytes() == audit_before


def test_blocked_and_failed_runs_are_auditable_but_never_promoted(tmp_path: Path) -> None:
    blocked_root, blocked_gate, blocked_audit, _ = _root_and_gate(
        tmp_path / "blocked", blocked=True
    )
    blocked_path = _run_report(
        blocked_root,
        blocked_gate,
        blocked_audit,
        startup_status="blocked",
        probe_status=None,
        probe_exit_code=None,
        service_stopped=False,
        run_ready=False,
        issues=[{"code": "STARTUP_NOT_READY", "message": "audited launch gate is not ready"}],
    )
    blocked = _audit(blocked_path, blocked_root)
    assert blocked["audit_ready"] is True
    assert blocked["run_status"] == "blocked"
    assert blocked["run_ready"] is False

    failed_root, failed_gate, failed_audit, _ = _root_and_gate(tmp_path / "failed")
    failed_path = _run_report(
        failed_root,
        failed_gate,
        failed_audit,
        probe_status="invalid",
        probe_exit_code=1,
        service_stopped=False,
        run_ready=False,
        issues=[{"code": "SERVICE_STOP_FAILED", "message": "service process did not stop"}],
    )
    failed = _audit(failed_path, failed_root)
    assert failed["audit_ready"] is True
    assert failed["run_status"] == "failed"
    assert failed["run_ready"] is False
    assert failed["issues"][0]["code"] == "SERVICE_STOP_FAILED"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda report: report.update({"unknown": True}), "SCHEMA_INVALID"),
        (lambda report: report.update({"decision_ready": True}), "DECISION_GATE_INVALID"),
        (lambda report: report.update({"run_ready": False, "issues": []}), "RUN_STATE_MISMATCH"),
        (lambda report: report.update({"gate_path": "../outside/gate.json"}), "PATH_INVALID"),
    ],
)
def test_tampered_state_paths_and_schema_fail_closed(
    tmp_path: Path, change, expected: str
) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path / expected)
    run_path = _run_report(root, gate_dir, audit_path)
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    change(payload)
    _write_json(run_path, _sign(payload))

    report = _audit(run_path, root)
    assert report["audit_ready"] is False
    assert report["run_ready"] is False
    assert report["issues"][0]["code"] == expected


def test_changed_gate_bytes_and_missing_run_report_are_rejected(tmp_path: Path) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path / "hash")
    run_path = _run_report(root, gate_dir, audit_path)
    gate_path = gate_dir / "daily_research_service_launch_gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["symbol"] = "000001.SZ"
    _write_json(gate_path, _sign(gate))
    changed = _audit(run_path, root)
    assert changed["audit_ready"] is False
    assert changed["issues"][0]["code"] in {"HASH_MISMATCH", "CHAIN_MISMATCH"}

    missing = _audit(root / "missing/daily_research_service_run_report.json", root, "missing-audit")
    assert missing["audit_ready"] is False
    assert missing["issues"][0]["code"] == "INPUT_UNAVAILABLE"


def test_cli_returns_zero_for_auditable_run_and_two_for_invalid_configuration(
    tmp_path: Path,
) -> None:
    root, gate_dir, audit_path, _ = _root_and_gate(tmp_path / "cli")
    run_path = _run_report(root, gate_dir, audit_path)
    code = main(
        [
            "audit-daily-research-service-run",
            "--run-report",
            str(run_path),
            "--artifact-root",
            str(root),
            "--output-dir",
            str(root / "cli-audit"),
        ]
    )
    assert code == 0

    assert (
        main(
            [
                "audit-daily-research-service-run",
                "--run-report",
                str(run_path),
                "--artifact-root",
                str(root),
                "--output-dir",
                str(tmp_path / "outside"),
            ]
        )
        == 2
    )
