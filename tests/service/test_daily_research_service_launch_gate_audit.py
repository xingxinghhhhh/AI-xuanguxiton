import json
import os
from pathlib import Path

import pytest

from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_launch_gate_audit import (
    DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION,
    audit_daily_research_service_launch_gate,
)
from tests.runtime.test_daily_research_handoff_audit import _sign as _sign_json
from tests.runtime.test_daily_research_handoff_audit import _write_json
from tests.service.test_daily_research_service_launch_gate import (
    _build_gate,
    _prepare_gate,
)


def _audit(inputs: dict[str, Path], output_dir: Path) -> dict[str, object]:
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    return audit_daily_research_service_launch_gate(
        gate_path=gate_dir / "daily_research_service_launch_gate.json",
        gate_report_path=gate_dir / "daily_research_service_launch_gate_report.json",
        artifact_root=root,
        output_dir=output_dir,
    )


def _rewrite_receipt_chain(inputs: dict[str, Path], mutate: object) -> None:
    receipt_path = inputs["receipt"]
    report_path = inputs["receipt_report"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    mutate(receipt, report)
    _write_json(receipt_path, _sign_json(receipt))
    report["receipt_sha256"] = sha256_bytes(receipt_path.read_bytes())
    _write_json(report_path, _sign_json(report))

    manifest = json.loads(inputs["launch_manifest"].read_text(encoding="utf-8"))
    manifest["receipt_sha256"] = sha256_bytes(receipt_path.read_bytes())
    manifest["receipt_report_sha256"] = sha256_bytes(report_path.read_bytes())
    _write_json(inputs["launch_manifest"], manifest)

    gate_path = (
        inputs["handoff"].parent.parent / "service-gate/daily_research_service_launch_gate.json"
    )
    gate_report_path = gate_path.with_name("daily_research_service_launch_gate_report.json")
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["launch_manifest_sha256"] = sha256_bytes(inputs["launch_manifest"].read_bytes())
    _write_json(gate_path, _sign_json(gate))
    _write_json(gate_report_path, _sign_json(gate))


@pytest.mark.parametrize(
    ("name", "as_of", "blocked", "expected_status"),
    [
        ("ready", "2026-08-10T08:00:00+00:00", False, "ready"),
        ("stale", "2026-08-07T12:00:00+00:00", False, "stale"),
        ("blocked", "2026-08-10T08:00:00+00:00", True, "blocked"),
    ],
)
def test_audits_ready_stale_and_blocked_gates_without_mutating_inputs(
    tmp_path: Path, name: str, as_of: str, blocked: bool, expected_status: str
) -> None:
    inputs = _prepare_gate(tmp_path / name, as_of=as_of, blocked=blocked)
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    tracked = [
        inputs["launch_manifest"],
        inputs["handoff"],
        inputs["handoff_report"],
        inputs["handoff_audit"],
        inputs["admission"],
        inputs["admission_report"],
        gate_dir / "daily_research_service_launch_gate.json",
        gate_dir / "daily_research_service_launch_gate_report.json",
    ]
    before = {path: path.read_bytes() for path in tracked}

    report = _audit(inputs, root / "gate-audit")

    assert report["audit_version"] == DAILY_RESEARCH_SERVICE_LAUNCH_GATE_AUDIT_VERSION
    assert report["status"] == expected_status
    assert report["gate_ready"] is (expected_status == "ready")
    assert report["audit_ready"] is True
    assert report["decision_ready"] is False
    assert report["issues"] == []
    assert before == {path: path.read_bytes() for path in tracked}
    output = root / "gate-audit/daily_research_service_launch_gate_audit_report.json"
    assert report["output_sha256"] == sha256_bytes(
        (
            json.dumps(
                {**report, "output_sha256": None}, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode()
    )
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_audit_output_is_deterministic_and_cli_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _prepare_gate(tmp_path / "deterministic")
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    first = _audit(inputs, root / "gate-audit")
    second = _audit(inputs, root / "gate-audit")
    assert first == second

    code = main(
        [
            "audit-daily-research-service-launch-gate",
            "--gate",
            str(gate_dir / "daily_research_service_launch_gate.json"),
            "--gate-report",
            str(gate_dir / "daily_research_service_launch_gate_report.json"),
            "--artifact-root",
            str(root),
            "--output-dir",
            str(root / "cli-audit"),
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["audit_ready"] is True


def test_audit_fails_closed_for_tamper_state_and_redacts_paths(tmp_path: Path) -> None:
    inputs = _prepare_gate(tmp_path / "tamper")
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    handoff = json.loads(inputs["handoff"].read_text(encoding="utf-8"))
    handoff["symbol"] = "000001.SZ"
    inputs["handoff"].write_text(json.dumps(handoff) + "\n", encoding="utf-8")

    report = _audit(inputs, root / "gate-audit")

    assert report["audit_ready"] is False
    assert report["status"] == "invalid"
    assert report["gate_ready"] is False
    assert report["issues"][0]["code"] == "HASH_MISMATCH"
    assert str(tmp_path) not in json.dumps(report)


def test_audit_rejects_rewritten_gate_state_and_path_escape(tmp_path: Path) -> None:
    inputs = _prepare_gate(tmp_path / "rewrite")
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    gate_path = gate_dir / "daily_research_service_launch_gate.json"
    report_path = gate_dir / "daily_research_service_launch_gate_report.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload["status"] = "blocked"
    payload["gate_ready"] = False
    _write_json(gate_path, _sign_json(payload))
    _write_json(report_path, _sign_json(payload))

    rewritten = _audit(inputs, root / "gate-audit-rewritten")
    assert rewritten["audit_ready"] is False
    assert rewritten["issues"][0]["code"] == "STATE_INVALID"

    _build_gate(inputs, gate_dir)
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload["launch_manifest_path"] = "../outside.json"
    _write_json(gate_path, _sign_json(payload))
    _write_json(report_path, _sign_json(payload))
    escaped = _audit(inputs, root / "gate-audit-escaped")
    assert escaped["audit_ready"] is False
    assert escaped["issues"][0]["code"] == "PATH_INVALID"


def test_audit_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    inputs = _prepare_gate(tmp_path / "symlink")
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    outside = tmp_path / "outside-handoff.json"
    outside.write_bytes(inputs["handoff"].read_bytes())
    link = root / "linked-handoff.json"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows host")
    gate_path = gate_dir / "daily_research_service_launch_gate.json"
    report_path = gate_dir / "daily_research_service_launch_gate_report.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload["handoff_path"] = link.relative_to(root).as_posix()
    payload["handoff_sha256"] = sha256_bytes(outside.read_bytes())
    _write_json(gate_path, _sign_json(payload))
    _write_json(report_path, _sign_json(payload))

    report = _audit(inputs, root / "gate-audit-symlink")
    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt, report: receipt.update({"unexpected": True}),
        lambda receipt, report: report.update({"unexpected": True}),
        lambda receipt, report: receipt.pop("symbol"),
        lambda receipt, report: report.pop("inputs"),
        lambda receipt, report: (
            receipt.update({"render_ready": False}),
            report.update({"render_ready": False}),
        ),
        lambda receipt, report: (
            receipt.update({"symbol": "000001.SZ"}),
            report.update({"symbol": "000001.SZ"}),
        ),
    ],
    ids=[
        "receipt-unknown-field",
        "report-unknown-field",
        "receipt-missing-field",
        "report-missing-field",
        "readiness-contradiction",
        "symbol-mismatch",
    ],
)
def test_audit_rejects_rewritten_receipt_contracts(tmp_path: Path, mutation: object) -> None:
    inputs = _prepare_gate(tmp_path / "receipt-contract")
    root = inputs["handoff"].parent.parent
    _build_gate(inputs, root / "service-gate")
    _rewrite_receipt_chain(inputs, mutation)

    report = _audit(inputs, root / "gate-audit")

    assert report["audit_ready"] is False
    assert report["status"] == "invalid"
    assert report["gate_ready"] is False


def test_audit_cli_returns_one_for_invalid_input_and_two_for_bad_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _prepare_gate(tmp_path / "cli-errors")
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    gate_path = gate_dir / "daily_research_service_launch_gate.json"
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    payload["decision_ready"] = True
    _write_json(gate_path, _sign_json(payload))
    _write_json(gate_dir / "daily_research_service_launch_gate_report.json", _sign_json(payload))

    invalid_code = main(
        [
            "audit-daily-research-service-launch-gate",
            "--gate",
            str(gate_path),
            "--gate-report",
            str(gate_dir / "daily_research_service_launch_gate_report.json"),
            "--artifact-root",
            str(root),
            "--output-dir",
            str(root / "cli-invalid"),
        ]
    )
    assert invalid_code == 1
    assert json.loads(capsys.readouterr().out)["audit_ready"] is False

    bad_root_code = main(
        [
            "audit-daily-research-service-launch-gate",
            "--gate",
            str(gate_path),
            "--gate-report",
            str(gate_dir / "daily_research_service_launch_gate_report.json"),
            "--artifact-root",
            str(tmp_path / "missing-root"),
            "--output-dir",
            str(tmp_path / "bad-root-output"),
        ]
    )
    assert bad_root_code == 2
    assert "ARTIFACT_ROOT_INVALID" in capsys.readouterr().err
