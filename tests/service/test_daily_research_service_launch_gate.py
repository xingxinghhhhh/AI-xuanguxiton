import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_final_receipt import (
    MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from a_share_ai.service.daily_research_service_launch_gate import (
    DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION,
    DailyResearchServiceLaunchGateError,
    build_daily_research_service_launch_gate,
    check_daily_research_service_launch_gate,
)
from a_share_ai.service.daily_research_service_probe import (
    daily_research_service_probe_exit_code,
    probe_daily_research_service,
)
from a_share_ai.service.read_only_receipt_server import READ_ONLY_RECEIPT_SERVICE_VERSION
from tests.runtime.test_daily_research_handoff import _build_handoff, _prepare_handoff
from tests.runtime.test_daily_research_handoff_audit import _sign as _sign_json
from tests.runtime.test_daily_research_handoff_audit import (
    _write_json,
    audit_daily_research_handoff,
)
from tests.service.test_launch_config import _free_port, _write_manifest
from tests.service.test_read_only_receipt_server import _write_self_hashed


def _write_receipt_pair(root: Path, *, status: str = "ready") -> tuple[Path, Path]:
    receipt_path = root / "final-receipt" / "receipt.json"
    report_path = root / "final-receipt" / "receipt-report.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    ready = status == "ready"
    receipt = {
        "admission_ready": ready,
        "admission_report_sha256": "b" * 64,
        "admission_sha256": "a" * 64,
        "audit_ready": True,
        "decision_ready": False,
        "first_as_of": "2026-08-10T00:00:00+00:00",
        "issues": [] if ready else [{"code": "STALE_INPUT", "message": "stale"}],
        "last_as_of": "2026-08-11T00:00:00+00:00",
        "output_sha256": None,
        "package_count": 2,
        "receipt_ready": ready,
        "receipt_version": MARKET_AWARE_SESSION_HISTORY_FINAL_RECEIPT_VERSION,
        "render_audit_report_sha256": "d" * 64,
        "render_ready": ready,
        "render_report_sha256": "c" * 64,
        "status": status,
        "symbol": "600000.SH",
    }
    _write_self_hashed(receipt_path, receipt)
    report = {
        "admission_ready": receipt["admission_ready"],
        "artifact_root": ".",
        "audit_ready": receipt["audit_ready"],
        "decision_ready": False,
        "first_as_of": receipt["first_as_of"],
        "inputs": {
            "admission": {
                "byte_count": 1,
                "path": "upstream/admission.json",
                "sha256": "a" * 64,
            },
            "admission_report": {
                "byte_count": 1,
                "path": "upstream/admission-report.json",
                "sha256": "b" * 64,
            },
            "render_report": {
                "byte_count": 1,
                "path": "upstream/render-report.json",
                "sha256": "c" * 64,
            },
            "render_audit_report": {
                "byte_count": 1,
                "path": "upstream/render-audit-report.json",
                "sha256": "d" * 64,
            },
        },
        "issues": receipt["issues"],
        "last_as_of": receipt["last_as_of"],
        "output_sha256": None,
        "package_count": receipt["package_count"],
        "receipt_ready": receipt["receipt_ready"],
        "receipt_sha256": sha256_bytes(receipt_path.read_bytes()),
        "receipt_version": receipt["receipt_version"],
        "render_ready": receipt["render_ready"],
        "status": receipt["status"],
        "symbol": receipt["symbol"],
    }
    _write_self_hashed(report_path, report)
    return receipt_path, report_path


def _prepare_gate(
    root: Path, *, as_of: str = "2026-08-10T08:00:00+00:00", blocked: bool = False
) -> dict[str, Path]:
    inputs = _prepare_handoff(root, as_of=as_of, blocked=blocked)
    handoff_dir = root / "handoff"
    _build_handoff(inputs, handoff_dir)
    audit_dir = root / "handoff-audit"
    audit_daily_research_handoff(
        handoff_path=handoff_dir / "daily_research_handoff.json",
        handoff_report_path=handoff_dir / "daily_research_handoff_report.json",
        artifact_root=root,
        output_dir=audit_dir,
    )
    receipt_path, receipt_report_path = _write_receipt_pair(root)
    launch_manifest = _write_manifest(root, receipt_path, receipt_report_path, port=_free_port())
    return {
        **inputs,
        "handoff": handoff_dir / "daily_research_handoff.json",
        "handoff_report": handoff_dir / "daily_research_handoff_report.json",
        "handoff_audit": audit_dir / "daily_research_handoff_audit_report.json",
        "launch_manifest": launch_manifest,
        "receipt": receipt_path,
        "receipt_report": receipt_report_path,
    }


def _build_gate(inputs: dict[str, Path], output_dir: Path) -> dict[str, Any]:
    return build_daily_research_service_launch_gate(
        handoff_path=inputs["handoff"],
        handoff_report_path=inputs["handoff_report"],
        handoff_audit_report_path=inputs["handoff_audit"],
        launch_manifest_path=inputs["launch_manifest"],
        artifact_root=inputs["handoff"].parent.parent,
        output_dir=output_dir,
    )


def test_ready_gate_is_self_hashed_and_reloads_inputs(tmp_path: Path) -> None:
    inputs = _prepare_gate(tmp_path / "ready")
    report = _build_gate(inputs, inputs["handoff"].parent.parent / "service-gate")

    assert report["gate_version"] == DAILY_RESEARCH_SERVICE_LAUNCH_GATE_VERSION
    assert report["status"] == "ready"
    assert report["gate_ready"] is True
    assert report["receipt_ready"] is True
    assert report["daily_admission_ready"] is True
    assert report["audit_ready"] is True
    assert report["handoff_ready"] is True
    assert report["decision_ready"] is False
    loaded = check_daily_research_service_launch_gate(
        gate_path=inputs["handoff"].parent.parent
        / "service-gate/daily_research_service_launch_gate.json",
        artifact_root=inputs["handoff"].parent.parent,
    )
    assert loaded.gate_ready is True
    assert loaded.receipt_path == inputs["receipt"].resolve()
    assert report["output_sha256"] == sha256_bytes(
        (
            json.dumps(
                {**report, "output_sha256": None}, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode()
    )


@pytest.mark.parametrize(
    ("as_of", "blocked", "status"),
    [
        ("2026-08-07T12:00:00+00:00", False, "stale"),
        ("2026-08-10T08:00:00+00:00", True, "blocked"),
    ],
)
def test_non_ready_inputs_never_promote_gate(
    tmp_path: Path, as_of: str, blocked: bool, status: str
) -> None:
    inputs = _prepare_gate(tmp_path / status, as_of=as_of, blocked=blocked)
    report = _build_gate(inputs, inputs["handoff"].parent.parent / "service-gate")
    assert report["status"] == status
    assert report["gate_ready"] is False
    loaded = check_daily_research_service_launch_gate(
        gate_path=inputs["handoff"].parent.parent
        / "service-gate/daily_research_service_launch_gate.json",
        artifact_root=inputs["handoff"].parent.parent,
    )
    assert loaded.gate_ready is False


def test_gate_rejects_tamper_and_report_state_rewrite(tmp_path: Path) -> None:
    inputs = _prepare_gate(tmp_path / "tamper")
    gate_dir = inputs["handoff"].parent.parent / "service-gate"
    _build_gate(inputs, gate_dir)
    payload = json.loads((gate_dir / "daily_research_service_launch_gate.json").read_text())
    payload["gate_ready"] = False
    _write_json(gate_dir / "daily_research_service_launch_gate.json", _sign_json(payload))
    with pytest.raises(DailyResearchServiceLaunchGateError, match="pair differs"):
        check_daily_research_service_launch_gate(
            gate_path=gate_dir / "daily_research_service_launch_gate.json",
            artifact_root=inputs["handoff"].parent.parent,
        )

    _build_gate(inputs, gate_dir)
    payload = json.loads((gate_dir / "daily_research_service_launch_gate.json").read_text())
    payload["status"] = "blocked"
    payload["gate_ready"] = False
    _write_json(gate_dir / "daily_research_service_launch_gate.json", _sign_json(payload))
    _write_json(gate_dir / "daily_research_service_launch_gate_report.json", _sign_json(payload))
    with pytest.raises(DailyResearchServiceLaunchGateError, match="references differ"):
        check_daily_research_service_launch_gate(
            gate_path=gate_dir / "daily_research_service_launch_gate.json",
            artifact_root=inputs["handoff"].parent.parent,
        )


def test_cli_gate_and_gate_check_only_do_not_listen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _prepare_gate(tmp_path / "cli")
    root = inputs["handoff"].parent.parent
    output_dir = root / "service-gate"
    code = main(
        [
            "build-daily-research-service-launch-gate",
            "--handoff",
            str(inputs["handoff"]),
            "--handoff-report",
            str(inputs["handoff_report"]),
            "--handoff-audit-report",
            str(inputs["handoff_audit"]),
            "--launch-manifest",
            str(inputs["launch_manifest"]),
            "--artifact-root",
            str(root),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert code == 0
    capsys.readouterr()
    check = main(
        [
            "serve-research-receipt",
            "--daily-launch-gate",
            str(output_dir / "daily_research_service_launch_gate.json"),
            "--daily-launch-gate-root",
            str(root),
            "--artifact-root",
            str(root),
            "--check-only",
        ]
    )
    assert check == 0
    assert json.loads(capsys.readouterr().out)["gate_ready"] is True


def test_old_launch_manifest_remains_compatible(tmp_path: Path) -> None:
    inputs = _prepare_gate(tmp_path / "compat")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "a_share_ai.cli",
            "serve-research-receipt",
            "--launch-manifest",
            str(inputs["launch_manifest"]),
            "--artifact-root",
            str(inputs["handoff"].parent.parent),
            "--check-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["service_version"] == READ_ONLY_RECEIPT_SERVICE_VERSION


def test_ready_gate_starts_service_for_node60_probe(tmp_path: Path) -> None:
    inputs = _prepare_gate(tmp_path / "e2e")
    root = inputs["handoff"].parent.parent
    gate_dir = root / "service-gate"
    _build_gate(inputs, gate_dir)
    port = json.loads(inputs["launch_manifest"].read_text())["port"]
    process = subprocess.Popen(
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(30):
            try:
                probe = probe_daily_research_service(
                    base_url=f"http://127.0.0.1:{port}", timeout_seconds=0.2
                )
                if probe["status"] == "ready":
                    assert daily_research_service_probe_exit_code(probe) == 0
                    break
            except Exception:  # noqa: BLE001 - bounded startup polling
                pass
        else:
            stderr = process.stderr.read() if process.stderr else ""
            pytest.fail(f"gate service did not start: {stderr}")
    finally:
        process.terminate()
        process.wait(timeout=5)
