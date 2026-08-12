import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_closure_admission_render_audit import (
    audit_market_aware_session_history_closure_admission_render,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_closure_admission_renderer import (
    _prepare_render_input,
)
from tests.analysis.test_market_aware_session_history_closure_admission_renderer import (
    _render as _render_node50,
)


def _rewrite_self_hashed(path: Path, payload: dict[str, Any]) -> None:
    payload["output_sha256"] = None
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    payload["output_sha256"] = sha256_bytes(canonical)
    _write_json(path, payload)


def _prepare_audit_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_render_input(tmp_path)
    _render_node50(paths, "admission-render")
    paths["markdown"] = paths["root"] / "admission-render" / (
        "market_aware_session_history_closure_admission.md"
    )
    paths["render_report"] = paths["root"] / "admission-render" / (
        "market_aware_session_history_closure_admission_render_report.json"
    )
    return paths


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return audit_market_aware_session_history_closure_admission_render(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        admission_audit_report_path=paths["admission_audit_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_admission_render_audit_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["render_audit_ready"] is True
    assert first["status"] == "ready"
    assert first["render_ready"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert first["markdown_sha256"] == sha256_bytes(paths["markdown"].read_bytes())
    report_path = paths["root"] / "audit-one" / (
        "market_aware_session_history_closure_admission_render_audit_report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )


def test_stale_admission_render_audit_preserves_state(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)
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

    audit = json.loads(paths["admission_audit_report"].read_text(encoding="utf-8"))
    audit["status"] = "stale"
    audit["admission_ready"] = False
    audit["render_ready"] = False
    audit["admission_sha256"] = sha256_bytes(paths["admission"].read_bytes())
    audit["admission_report_sha256"] = sha256_bytes(
        paths["admission_report"].read_bytes()
    )
    _rewrite_self_hashed(paths["admission_audit_report"], audit)

    render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
    render_report["status"] = "stale"
    render_report["admission_ready"] = False
    render_report["render_ready"] = False
    render_report["issues"] = admission["issues"]
    render_report["admission_sha256"] = sha256_bytes(paths["admission"].read_bytes())
    render_report["admission_report_sha256"] = sha256_bytes(
        paths["admission_report"].read_bytes()
    )
    render_report["admission_audit_report_sha256"] = sha256_bytes(
        paths["admission_audit_report"].read_bytes()
    )
    _rewrite_self_hashed(paths["render_report"], render_report)

    report = _audit(paths, "audit")

    assert report["render_audit_ready"] is True
    assert report["status"] == "stale"
    assert report["admission_ready"] is False
    assert report["render_ready"] is False
    assert report["decision_ready"] is False


@pytest.mark.parametrize(
    "mutation",
    ["admission", "admission_report", "audit", "markdown", "render_report", "decision", "missing"],
)
def test_admission_render_audit_fails_closed_on_tamper(
    tmp_path: Path, mutation: str
) -> None:
    paths = _prepare_audit_input(tmp_path / mutation)
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
    elif mutation == "audit":
        audit = json.loads(
            paths["admission_audit_report"].read_text(encoding="utf-8")
        )
        audit["audit_ready"] = False
        _rewrite_self_hashed(paths["admission_audit_report"], audit)
    elif mutation == "markdown":
        paths["markdown"].write_bytes(paths["markdown"].read_bytes() + b"tampered\n")
    elif mutation == "render_report":
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["render_ready"] = False
        _write_json(paths["render_report"], render_report)
    elif mutation == "decision":
        admission = json.loads(paths["admission"].read_text(encoding="utf-8"))
        admission["decision_ready"] = True
        _write_json(paths["admission"], admission)
    else:
        paths["markdown"] = paths["root"] / "missing.md"

    report = _audit(paths, "audit")

    assert report["render_audit_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "DECISION_GATE_INVALID",
        "FIELD_MISMATCH",
        "HASH_MISMATCH",
        "INPUT_UNAVAILABLE",
    }


def test_admission_render_audit_records_actual_artifacts(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)
    report = _audit(paths, "audit")

    expected = {
        "admission": paths["admission"],
        "admission_report": paths["admission_report"],
        "admission_audit_report": paths["admission_audit_report"],
        "markdown": paths["markdown"],
        "render_report": paths["render_report"],
    }
    for role, path in expected.items():
        field = f"{role}_path"
        sha_field = f"{role}_sha256"
        assert report[field] == path.relative_to(paths["root"]).as_posix()
        assert report[sha_field] == sha256_bytes(path.read_bytes())


def test_admission_render_audit_cli_and_output_boundary(
    tmp_path: Path, capsys: Any
) -> None:
    paths = _prepare_audit_input(tmp_path)
    output_dir = paths["root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-history-closure-admission-render",
            "--admission",
            str(paths["admission"]),
            "--admission-report",
            str(paths["admission_report"]),
            "--admission-audit-report",
            str(paths["admission_audit_report"]),
            "--markdown",
            str(paths["markdown"]),
            "--render-report",
            str(paths["render_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["render_audit_ready"] is True

    outside = tmp_path.parent / "outside-admission-render-audit"
    boundary = audit_market_aware_session_history_closure_admission_render(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        admission_audit_report_path=paths["admission_audit_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["render_audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside
        / "market_aware_session_history_closure_admission_render_audit_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["audit-market-aware-session-history-closure-admission-render"])
