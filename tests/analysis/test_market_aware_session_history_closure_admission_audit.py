import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_closure_admission_audit import (
    audit_market_aware_session_history_closure_admission,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_closure_admission import (
    _build as _build_node48,
)
from tests.analysis.test_market_aware_session_history_closure_admission import (
    _prepare_admission_input,
)
from tests.analysis.test_market_aware_session_history_closure_render_audit import (
    _audit as _audit_node47,
)
from tests.analysis.test_market_aware_session_history_closure_renderer import (
    _render as _render_node46,
)
from tests.analysis.test_market_aware_session_history_closure_renderer import (
    _rewrite_closure_state,
)


def _rewrite_self_hashed(path: Path, payload: dict[str, Any]) -> None:
    payload["output_sha256"] = None
    canonical = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    payload["output_sha256"] = sha256_bytes(canonical)
    _write_json(path, payload)


def _prepare_audit_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_admission_input(tmp_path)
    _build_node48(paths, "admission")
    paths["admission"] = paths["root"] / "admission" / (
        "market_aware_session_history_closure_admission.json"
    )
    paths["admission_report"] = paths["root"] / "admission" / (
        "market_aware_session_history_closure_admission_report.json"
    )
    return paths


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return audit_market_aware_session_history_closure_admission(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_admission_audit_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["audit_ready"] is True
    assert first["status"] == "ready"
    assert first["admission_ready"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert {item["role"] for item in first["artifacts"]} == {
        "admission",
        "admission_report",
        "closure",
        "closure_report",
        "closure_markdown",
        "closure_render_report",
        "closure_render_audit_report",
    }


def test_stale_admission_audit_preserves_state(tmp_path: Path) -> None:
    paths = _prepare_audit_input(tmp_path)
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
    _build_node48(paths, "stale-admission")
    paths["admission"] = paths["root"] / "stale-admission" / (
        "market_aware_session_history_closure_admission.json"
    )
    paths["admission_report"] = paths["root"] / "stale-admission" / (
        "market_aware_session_history_closure_admission_report.json"
    )

    report = _audit(paths, "audit")

    assert report["audit_ready"] is True
    assert report["status"] == "stale"
    assert report["admission_ready"] is False
    assert report["closure_status"] == "stale"
    assert report["decision_ready"] is False


@pytest.mark.parametrize(
    "mutation",
    ["admission", "admission_report", "closure", "markdown", "audit", "decision", "missing"],
)
def test_admission_audit_fails_closed_on_tamper(
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
        admission_report["inputs"]["markdown"]["sha256"] = "0" * 64
        _rewrite_self_hashed(paths["admission_report"], admission_report)
    elif mutation == "closure":
        paths["closure"].write_text("bad-json\n", encoding="utf-8")
    elif mutation == "markdown":
        paths["markdown"].write_bytes(paths["markdown"].read_bytes() + b"tampered\n")
    elif mutation == "audit":
        audit = json.loads(paths["render_audit_report"].read_text(encoding="utf-8"))
        audit["audit_ready"] = False
        _rewrite_self_hashed(paths["render_audit_report"], audit)
    elif mutation == "decision":
        admission = json.loads(paths["admission"].read_text(encoding="utf-8"))
        admission["decision_ready"] = True
        _write_json(paths["admission"], admission)
    else:
        paths["render_audit_report"] = paths["root"] / "missing.json"

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "CHAIN_MISMATCH",
        "DECISION_GATE_INVALID",
        "FIELD_MISMATCH",
        "HASH_MISMATCH",
        "INPUT_UNAVAILABLE",
        "INPUT_JSON_INVALID",
        "SIZE_MISMATCH",
    }


def test_admission_audit_records_actual_artifacts(
    tmp_path: Path,
) -> None:
    paths = _prepare_audit_input(tmp_path)
    report = _audit(paths, "audit")

    for item in report["artifacts"]:
        role = item["role"]
        path = paths[
            {
                "closure_markdown": "markdown",
                "closure_render_report": "render_report",
                "closure_render_audit_report": "render_audit_report",
            }.get(role, role)
        ]
        assert item["path"] == path.relative_to(paths["root"]).as_posix()
        assert item["byte_count"] == path.stat().st_size
        assert item["sha256"] == sha256_bytes(path.read_bytes())


def test_admission_audit_cli_and_output_boundary(
    tmp_path: Path, capsys: Any
) -> None:
    paths = _prepare_audit_input(tmp_path)
    output_dir = paths["root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-history-closure-admission",
            "--admission",
            str(paths["admission"]),
            "--admission-report",
            str(paths["admission_report"]),
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
    assert json.loads(capsys.readouterr().out)["audit_ready"] is True

    outside = tmp_path.parent / "outside-admission-audit"
    boundary = audit_market_aware_session_history_closure_admission(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        closure_path=paths["closure"],
        closure_report_path=paths["closure_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        render_audit_report_path=paths["render_audit_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_closure_admission_audit_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["audit-market-aware-session-history-closure-admission"])
