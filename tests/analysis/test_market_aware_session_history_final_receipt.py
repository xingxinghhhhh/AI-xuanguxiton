import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_final_receipt import (
    build_market_aware_session_history_final_receipt,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_closure_admission_render_audit import (
    _audit as _audit_node51,
)
from tests.analysis.test_market_aware_session_history_closure_admission_render_audit import (
    _prepare_audit_input,
)


def _rewrite_self_hashed(path: Path, payload: dict[str, Any]) -> None:
    payload["output_sha256"] = None
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    payload["output_sha256"] = sha256_bytes(canonical)
    _write_json(path, payload)


def _prepare_receipt_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_audit_input(tmp_path)
    _audit_node51(paths, "render-audit")
    paths["render_audit_report"] = paths["root"] / "render-audit" / (
        "market_aware_session_history_closure_admission_render_audit_report.json"
    )
    return paths


def _build(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return build_market_aware_session_history_final_receipt(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_final_receipt_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_receipt_input(tmp_path)

    first = _build(paths, "receipt-one")
    second = _build(paths, "receipt-two")

    assert first["receipt_ready"] is True
    assert first["status"] == "ready"
    assert first["admission_ready"] is True
    assert first["render_ready"] is True
    assert first["audit_ready"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    receipt_path = paths["root"] / "receipt-one" / (
        "market_aware_session_history_final_receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_ready"] is True
    assert receipt["decision_ready"] is False
    report_path = paths["root"] / "receipt-one" / (
        "market_aware_session_history_final_receipt_report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )


def test_stale_final_receipt_preserves_state(tmp_path: Path) -> None:
    paths = _prepare_receipt_input(tmp_path)

    admission = json.loads(paths["admission"].read_text(encoding="utf-8"))
    admission["status"] = "stale"
    admission["admission_ready"] = False
    admission["render_ready"] = False
    admission["issues"] = [{"code": "STALE_INPUT", "message": "stale"}]
    _rewrite_self_hashed(paths["admission"], admission)

    admission_report = json.loads(
        paths["admission_report"].read_text(encoding="utf-8")
    )
    admission_report["status"] = "stale"
    admission_report["admission_ready"] = False
    admission_report["render_ready"] = False
    admission_report["issues"] = admission["issues"]
    admission_report["admission_sha256"] = sha256_bytes(paths["admission"].read_bytes())
    _rewrite_self_hashed(paths["admission_report"], admission_report)

    render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
    render_report["status"] = "stale"
    render_report["admission_ready"] = False
    render_report["render_ready"] = False
    render_report["issues"] = admission["issues"]
    render_report["admission_sha256"] = sha256_bytes(paths["admission"].read_bytes())
    render_report["admission_report_sha256"] = sha256_bytes(
        paths["admission_report"].read_bytes()
    )
    _rewrite_self_hashed(paths["render_report"], render_report)

    audit = json.loads(paths["render_audit_report"].read_text(encoding="utf-8"))
    audit["status"] = "stale"
    audit["admission_ready"] = False
    audit["render_ready"] = False
    audit["issues"] = admission["issues"]
    audit["admission_sha256"] = sha256_bytes(paths["admission"].read_bytes())
    audit["admission_report_sha256"] = sha256_bytes(
        paths["admission_report"].read_bytes()
    )
    audit["render_report_sha256"] = sha256_bytes(paths["render_report"].read_bytes())
    _rewrite_self_hashed(paths["render_audit_report"], audit)

    report = _build(paths, "receipt")

    assert report["receipt_ready"] is False
    assert report["status"] == "stale"
    assert report["admission_ready"] is False
    assert report["render_ready"] is False
    assert report["audit_ready"] is True
    assert report["decision_ready"] is False


@pytest.mark.parametrize(
    "mutation",
    ["admission", "admission_report", "render_report", "render_audit", "decision", "missing"],
)
def test_final_receipt_fails_closed_on_tamper(
    tmp_path: Path, mutation: str
) -> None:
    paths = _prepare_receipt_input(tmp_path / mutation)
    if mutation == "admission":
        admission = json.loads(paths["admission"].read_text(encoding="utf-8"))
        admission["symbol"] = "tampered"
        _write_json(paths["admission"], admission)
    elif mutation == "admission_report":
        admission_report = json.loads(
            paths["admission_report"].read_text(encoding="utf-8")
        )
        admission_report["admission_ready"] = False
        _write_json(paths["admission_report"], admission_report)
    elif mutation == "render_report":
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["render_ready"] = False
        _write_json(paths["render_report"], render_report)
    elif mutation == "render_audit":
        audit = json.loads(paths["render_audit_report"].read_text(encoding="utf-8"))
        audit["render_audit_ready"] = False
        _rewrite_self_hashed(paths["render_audit_report"], audit)
    elif mutation == "decision":
        admission = json.loads(paths["admission"].read_text(encoding="utf-8"))
        admission["decision_ready"] = True
        _write_json(paths["admission"], admission)
    else:
        paths["render_report"] = paths["root"] / "missing.json"

    report = _build(paths, "receipt")

    assert report["receipt_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "DECISION_GATE_INVALID",
        "FIELD_MISMATCH",
        "HASH_MISMATCH",
        "INPUT_UNAVAILABLE",
    }


def test_final_receipt_records_actual_inputs(tmp_path: Path) -> None:
    paths = _prepare_receipt_input(tmp_path)
    report = _build(paths, "receipt")

    expected = {
        "admission": paths["admission"],
        "admission_report": paths["admission_report"],
        "render_report": paths["render_report"],
        "render_audit_report": paths["render_audit_report"],
    }
    for role, path in expected.items():
        item = report["inputs"][role]
        assert item["path"] == path.relative_to(paths["root"]).as_posix()
        assert item["byte_count"] == path.stat().st_size
        assert item["sha256"] == sha256_bytes(path.read_bytes())


def test_final_receipt_cli_and_output_boundary(
    tmp_path: Path, capsys: Any
) -> None:
    paths = _prepare_receipt_input(tmp_path)
    output_dir = paths["root"] / "cli-receipt"
    exit_code = main(
        [
            "build-market-aware-session-history-final-receipt",
            "--admission",
            str(paths["admission"]),
            "--admission-report",
            str(paths["admission_report"]),
            "--render-report",
            str(paths["render_report"]),
            "--render-audit-report",
            str(paths["render_audit_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["receipt_ready"] is True

    outside = tmp_path.parent / "outside-final-receipt"
    boundary = build_market_aware_session_history_final_receipt(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["receipt_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_final_receipt_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["build-market-aware-session-history-final-receipt"])
