import json
import subprocess
import sys
from pathlib import Path

from a_share_ai.cli import main
from a_share_ai.service.daily_research_service_launch_gate_startup import (
    load_daily_research_service_launch_gate_startup,
)
from tests.runtime.test_daily_research_handoff_audit import _sign as _sign_json
from tests.service.test_daily_research_service_launch_gate import (
    _build_gate,
    _build_gate_audit,
    _prepare_gate,
)


def _root_and_gate(tmp_path: Path) -> tuple[Path, Path, Path]:
    inputs = _prepare_gate(tmp_path / "startup")
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    audit_path = _build_gate_audit(root, gate_dir)
    return root, gate_dir, audit_path


def test_ready_gate_and_independent_audit_are_required_and_bound(tmp_path: Path) -> None:
    root, gate_dir, audit_path = _root_and_gate(tmp_path)
    config = load_daily_research_service_launch_gate_startup(
        gate_path=gate_dir / "daily_research_service_launch_gate.json",
        audit_path=audit_path,
        artifact_root=root,
    )
    assert config.launch_ready is True
    assert config.audit_report["audit_ready"] is True
    assert config.audit_report["gate_ready"] is True

    code = main(
        [
            "serve-research-receipt",
            "--daily-launch-gate",
            str(gate_dir / "daily_research_service_launch_gate.json"),
            "--daily-launch-gate-root",
            str(root),
            "--daily-launch-gate-audit",
            str(audit_path),
            "--artifact-root",
            str(root),
            "--check-only",
        ]
    )
    assert code == 0


def test_actual_gate_start_requires_audit_report(tmp_path: Path) -> None:
    root, gate_dir, _ = _root_and_gate(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "serve-research-receipt",
            "--daily-launch-gate",
            str(gate_dir / "daily_research_service_launch_gate.json"),
            "--daily-launch-gate-root",
            str(root),
            "--artifact-root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "requires --daily-launch-gate-audit" in result.stderr


def test_tampered_audit_reference_refuses_startup(tmp_path: Path) -> None:
    root, gate_dir, audit_path = _root_and_gate(tmp_path)
    report = json.loads(audit_path.read_text(encoding="utf-8"))
    report["gate_sha256"] = "0" * 64
    audit_path.write_text(
        json.dumps(_sign_json(report), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "serve-research-receipt",
            "--daily-launch-gate",
            str(gate_dir / "daily_research_service_launch_gate.json"),
            "--daily-launch-gate-root",
            str(root),
            "--daily-launch-gate-audit",
            str(audit_path),
            "--artifact-root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "CHAIN_MISMATCH" in result.stderr
