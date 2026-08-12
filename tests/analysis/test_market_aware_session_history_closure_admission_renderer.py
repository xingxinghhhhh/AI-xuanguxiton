import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history_closure_admission_renderer import (
    render_market_aware_session_history_closure_admission,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history_closure_admission import (
    _build as _build_node48,
)
from tests.analysis.test_market_aware_session_history_closure_admission_audit import (
    _audit as _audit_node49,
)
from tests.analysis.test_market_aware_session_history_closure_admission_audit import (
    _prepare_audit_input,
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


def _prepare_render_input(tmp_path: Path) -> dict[str, Path]:
    paths = _prepare_audit_input(tmp_path)
    _build_node48(paths, "admission")
    paths["admission"] = paths["root"] / "admission" / (
        "market_aware_session_history_closure_admission.json"
    )
    paths["admission_report"] = paths["root"] / "admission" / (
        "market_aware_session_history_closure_admission_report.json"
    )
    _audit_node49(paths, "admission-audit")
    paths["admission_audit_report"] = paths["root"] / "admission-audit" / (
        "market_aware_session_history_closure_admission_audit_report.json"
    )
    return paths


def _render(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return render_market_aware_session_history_closure_admission(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        admission_audit_report_path=paths["admission_audit_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_admission_render_is_ready_and_deterministic(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)

    first = _render(paths, "render-one")
    second = _render(paths, "render-two")

    assert first["render_ready"] is True
    assert first["status"] == "ready"
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    first_markdown = (
        paths["root"]
        / "render-one"
        / "market_aware_session_history_closure_admission.md"
    ).read_bytes()
    second_markdown = (
        paths["root"]
        / "render-two"
        / "market_aware_session_history_closure_admission.md"
    ).read_bytes()
    assert first_markdown == second_markdown
    assert first["markdown_sha256"] == sha256_bytes(first_markdown)
    report_path = paths["root"] / "render-one" / (
        "market_aware_session_history_closure_admission_render_report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["output_sha256"] == (
        first["output_sha256"]
    )
    assert b"read-only evidence-chain view" in first_markdown


def test_stale_admission_render_preserves_state(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)
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
    _audit_node49(paths, "stale-admission-audit")
    paths["admission_audit_report"] = paths["root"] / "stale-admission-audit" / (
        "market_aware_session_history_closure_admission_audit_report.json"
    )

    report = _render(paths, "render")
    markdown = (
        paths["root"]
        / "render"
        / "market_aware_session_history_closure_admission.md"
    ).read_text(encoding="utf-8")

    assert report["render_ready"] is False
    assert report["status"] == "stale"
    assert report["admission_ready"] is False
    assert report["audit_ready"] is True
    assert report["decision_ready"] is False
    assert "stale" in markdown


@pytest.mark.parametrize(
    "mutation",
    ["admission", "admission_report", "audit", "decision", "missing"],
)
def test_admission_render_fails_closed_on_tamper(
    tmp_path: Path, mutation: str
) -> None:
    paths = _prepare_render_input(tmp_path / mutation)
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
    elif mutation == "decision":
        admission = json.loads(paths["admission"].read_text(encoding="utf-8"))
        admission["decision_ready"] = True
        _write_json(paths["admission"], admission)
    else:
        paths["admission_audit_report"] = paths["root"] / "missing.json"

    report = _render(paths, "render")

    assert report["render_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] in {
        "DECISION_GATE_INVALID",
        "FIELD_MISMATCH",
        "HASH_MISMATCH",
        "INPUT_UNAVAILABLE",
    }
    assert not (
        paths["root"]
        / "render"
        / "market_aware_session_history_closure_admission.md"
    ).exists()


def test_admission_render_escapes_literal_fields(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)
    admission = json.loads(paths["admission"].read_text(encoding="utf-8"))
    admission["symbol"] = "X|`<script>\n#[]()"
    _rewrite_self_hashed(paths["admission"], admission)

    admission_report = json.loads(
        paths["admission_report"].read_text(encoding="utf-8")
    )
    admission_report["admission_sha256"] = sha256_bytes(paths["admission"].read_bytes())
    for field in ("symbol",):
        admission_report[field] = admission[field]
    _rewrite_self_hashed(paths["admission_report"], admission_report)

    audit = json.loads(paths["admission_audit_report"].read_text(encoding="utf-8"))
    audit["admission_sha256"] = sha256_bytes(paths["admission"].read_bytes())
    audit["admission_report_sha256"] = sha256_bytes(
        paths["admission_report"].read_bytes()
    )
    audit["symbol"] = admission["symbol"]
    _rewrite_self_hashed(paths["admission_audit_report"], audit)

    result = _render(paths, "render")
    markdown = (
        paths["root"]
        / "render"
        / "market_aware_session_history_closure_admission.md"
    ).read_text(encoding="utf-8")

    assert result["render_ready"] is True
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\|" in markdown


def test_admission_render_cli_and_output_boundary(
    tmp_path: Path, capsys: Any
) -> None:
    paths = _prepare_render_input(tmp_path)
    output_dir = paths["root"] / "cli-render"
    exit_code = main(
        [
            "render-market-aware-session-history-closure-admission",
            "--admission",
            str(paths["admission"]),
            "--admission-report",
            str(paths["admission_report"]),
            "--admission-audit-report",
            str(paths["admission_audit_report"]),
            "--artifact-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["render_ready"] is True

    outside = tmp_path.parent / "outside-admission-render"
    boundary = render_market_aware_session_history_closure_admission(
        admission_path=paths["admission"],
        admission_report_path=paths["admission_report"],
        admission_audit_report_path=paths["admission_audit_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["render_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_history_closure_admission_render_report.json"
    ).exists()

    with pytest.raises(SystemExit):
        main(["render-market-aware-session-history-closure-admission"])
