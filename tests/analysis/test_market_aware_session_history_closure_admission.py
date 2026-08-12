import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_closure_admission import (
    build_market_aware_session_history_closure_admission,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_closure_render_audit import (
    _audit as _audit_node47,
)
from tests.analysis.test_market_aware_session_history_closure_render_audit import (
    _prepare_audit_input,
)
from tests.analysis.test_market_aware_session_history_closure_renderer import (
    _render as _render_node46,
)
from tests.analysis.test_market_aware_session_history_closure_renderer import (
    _rewrite_closure_state,
)


def _prepare_admission_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_audit_input(tmp_path)
    _audit_node47(paths, "render-audit")
    paths["render_audit_report"] = paths["root"] / "render-audit" / (
        "market_aware_session_history_closure_render_audit_report.json"
    )
    return paths


def _build(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return build_market_aware_session_history_closure_admission(
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_admission_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_admission_input(tmp_path)

    first = _build(paths, "admission-one")
    second = _build(paths, "admission-two")

    assert first["admission_ready"] is True
    assert first["status"] == "ready"
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    admission_path = (
        paths["root"]
        / "admission-one"
        / "market_aware_session_history_closure_admission.json"
    )
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    assert admission["admission_version"] == (
        "market-aware-session-history-closure-admission-v1"
    )
    assert admission["admission_ready"] is True
    assert admission["decision_ready"] is False
    assert admission["audit_ready"] is True
    assert first["symbol"] == admission["symbol"]
    assert first["package_count"] == admission["package_count"]


def test_stale_chain_is_not_admitted(tmp_path: Path) -> None:
    paths = _prepare_admission_input(tmp_path)
    _rewrite_closure_state(paths, status="stale", closure_ready=False)
    _render_node46(paths, "stale-render")
    paths["markdown"] = paths["root"] / "stale-render" / (
        "market_aware_session_history_closure.md"
    )
    paths["render_report"] = paths["root"] / "stale-render" / (
        "market_aware_session_history_closure_render_report.json"
    )
    _audit_node47(paths, "stale-audit")
    paths["render_audit_report"] = paths["root"] / "stale-audit" / (
        "market_aware_session_history_closure_render_audit_report.json"
    )

    report = _build(paths, "admission")

    assert report["admission_ready"] is False
    assert report["status"] == "stale"
    assert report["closure_status"] == "stale"
    assert report["closure_ready"] is False
    assert report["render_ready"] is False
    assert report["decision_ready"] is False


@pytest.mark.parametrize("mutation", ["closure", "markdown", "audit", "decision"])
def test_admission_fails_closed_on_tamper(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_admission_input(tmp_path / mutation)
    if mutation == "closure":
        paths["closure"].write_text("bad-json\n", encoding="utf-8")
    elif mutation == "markdown":
        paths["markdown"].write_bytes(paths["markdown"].read_bytes() + b"tampered\n")
    elif mutation == "audit":
        audit = json.loads(paths["render_audit_report"].read_text(encoding="utf-8"))
        audit["audit_ready"] = False
        _write_json(paths["render_audit_report"], audit)
    else:
        closure = json.loads(paths["closure"].read_text(encoding="utf-8"))
        closure["decision_ready"] = True
        _write_json(paths["closure"], closure)

    report = _build(paths, "admission")

    assert report["admission_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "DECISION_GATE_INVALID",
        "HASH_MISMATCH",
        "INPUT_JSON_INVALID",
        "FIELD_MISMATCH",
    }


def test_admission_report_records_actual_inputs(tmp_path: Path) -> None:
    paths = _prepare_admission_input(tmp_path)
    report = _build(paths, "admission")

    assert set(report["inputs"]) == {
        "closure",
        "closure_report",
        "markdown",
        "render_report",
        "render_audit_report",
    }
    for role, item in report["inputs"].items():
        path = paths[role]
        assert item["path"] == path.relative_to(paths["root"]).as_posix()
        assert item["byte_count"] == path.stat().st_size
        assert item["sha256"] == sha256_bytes(path.read_bytes())


def test_admission_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare_admission_input(tmp_path)
    output_dir = paths["root"] / "cli-admission"
    exit_code = main(
        [
            "build-market-aware-session-history-closure-admission",
            "--closure",
            str(paths["closure"]),
            "--closure-report",
            str(paths["closure_report"]),
            "--markdown",
            str(paths["markdown"]),
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
    assert json.loads(capsys.readouterr().out)["admission_ready"] is True

    outside = tmp_path.parent / "outside-admission"
    boundary = build_market_aware_session_history_closure_admission(
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["admission_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_closure_admission_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["build-market-aware-session-history-closure-admission"])
