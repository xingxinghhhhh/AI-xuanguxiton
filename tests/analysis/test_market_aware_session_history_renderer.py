import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_history import build_market_aware_session_history
from a_share_ai.analysis.market_aware_session_history_audit import (
    audit_market_aware_session_history,
)
from a_share_ai.analysis.market_aware_session_history_renderer import (
    render_market_aware_session_history,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session import _write_json
from tests.analysis.test_market_aware_session_history import (
    _package_reference,
    _prepare_pair,
    _write_spec,
)


def _prepare_render_input(tmp_path: Path) -> dict[str, Path]:
    previous, current = _prepare_pair(tmp_path)
    spec = _write_spec(
        tmp_path,
        [_package_reference(previous, tmp_path), _package_reference(current, tmp_path)],
    )
    build_market_aware_session_history(
        spec_path=spec,
        history_root=tmp_path,
        output_dir=tmp_path / "history",
    )
    history = tmp_path / "history" / "market_aware_session_history.json"
    history_report = tmp_path / "history" / "market_aware_session_history_report.json"
    audit_dir = tmp_path / "audit"
    audit_market_aware_session_history(
        history_path=history,
        history_report_path=history_report,
        history_root=tmp_path,
        output_dir=audit_dir,
    )
    return {
        "history": history,
        "history_report": history_report,
        "audit_report": audit_dir / "market_aware_session_history_audit_report.json",
        "root": tmp_path,
    }


def _render(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return render_market_aware_session_history(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_audit_report_path=paths["audit_report"],
        history_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_ready_history_renders_deterministically(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)

    first = _render(paths, "render-one")
    second = _render(paths, "render-two")

    assert first["render_ready"] is True
    assert first["history_ready"] is True
    assert first["history_audit_ready"] is True
    assert first["decision_ready"] is False
    assert first["render_version"] == "market-aware-session-history-render-v1"
    assert first["output_sha256"] == second["output_sha256"]
    markdown_path = paths["root"] / "render-one" / "market_aware_session_history.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Session timeline" in markdown
    assert "## Package references" in markdown
    assert "does not infer market trends" in markdown
    report_path = paths["root"] / "render-one" / "market_aware_session_history_render_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["output_sha256"] == sha256_bytes(markdown_path.read_bytes())


def test_renderer_can_show_audit_block_without_ready_upgrade(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)
    audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
    audit_report["audit_ready"] = False
    audit_report["issues"] = [{"code": "BLOCKED", "message": "audit <blocked>"}]
    _write_json(paths["audit_report"], audit_report)

    report = _render(paths, "render")

    assert report["render_ready"] is False
    assert report["history_audit_ready"] is False
    markdown = (paths["root"] / "render" / "market_aware_session_history.md").read_text(
        encoding="utf-8"
    )
    assert "BLOCKED" in markdown
    assert "&lt;blocked&gt;" in markdown


@pytest.mark.parametrize("mutation", ["history", "history_report", "audit_report", "json"])
def test_renderer_fails_closed_for_input_tamper(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_render_input(tmp_path / mutation)
    if mutation == "history":
        paths["history"].write_bytes(paths["history"].read_bytes() + b"tampered\n")
    elif mutation == "history_report":
        report = json.loads(paths["history_report"].read_text(encoding="utf-8"))
        report["output_sha256"] = "0" * 64
        _write_json(paths["history_report"], report)
    elif mutation == "audit_report":
        audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
        audit_report["history_sha256"] = "0" * 64
        _write_json(paths["audit_report"], audit_report)
    else:
        paths["history"].write_text("bad-json\n", encoding="utf-8")

    report = _render(paths, "render")

    assert report["render_ready"] is False
    assert report["issues"][0]["code"] in {"HASH_MISMATCH", "INPUT_JSON_INVALID"}


def test_renderer_escapes_injected_issue_text(tmp_path: Path) -> None:
    paths = _prepare_render_input(tmp_path)
    audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
    audit_report["issues"] = [
        {"code": "<script>|#", "message": "[bad](javascript:x) `fence`"}
    ]
    _write_json(paths["audit_report"], audit_report)

    report = _render(paths, "render")

    assert report["render_ready"] is True
    markdown = (paths["root"] / "render" / "market_aware_session_history.md").read_text(
        encoding="utf-8"
    )
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\|" in markdown
    assert "\\#" in markdown


@pytest.mark.parametrize("mutation", ["version", "decision", "path"])
def test_renderer_rejects_invalid_contract(tmp_path: Path, mutation: str) -> None:
    paths = _prepare_render_input(tmp_path / mutation)
    if mutation == "version":
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        history["history_version"] = "other-v1"
        _write_json(paths["history"], history)
    elif mutation == "decision":
        history = json.loads(paths["history"].read_text(encoding="utf-8"))
        history["decision_ready"] = True
        _write_json(paths["history"], history)
    else:
        audit_report = json.loads(paths["audit_report"].read_text(encoding="utf-8"))
        audit_report["history_path"] = "../outside.json"
        _write_json(paths["audit_report"], audit_report)

    report = _render(paths, "render")

    assert report["render_ready"] is False
    expected = {
        "version": "VERSION_MISMATCH",
        "decision": "DECISION_GATE_INVALID",
        "path": "CHAIN_MISMATCH",
    }
    assert report["issues"][0]["code"] == expected[mutation]


def test_renderer_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    paths = _prepare_render_input(tmp_path)
    output_dir = tmp_path / "cli-render"
    exit_code = main(
        [
            "render-market-aware-session-history",
            "--history",
            str(paths["history"]),
            "--history-report",
            str(paths["history_report"]),
            "--history-audit-report",
            str(paths["audit_report"]),
            "--history-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["render_ready"] is True

    outside = tmp_path.parent / "outside-history-render"
    boundary = render_market_aware_session_history(
        history_path=paths["history"],
        history_report_path=paths["history_report"],
        history_audit_report_path=paths["audit_report"],
        history_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["render_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_HISTORY_ROOT"
    assert not (outside / "market_aware_session_history_render_report.json").exists()

    with pytest.raises(SystemExit):
        main(["render-market-aware-session-history"])
