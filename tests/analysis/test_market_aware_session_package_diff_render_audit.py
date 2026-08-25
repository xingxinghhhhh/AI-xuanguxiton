import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.market_aware_session_package_diff_render_audit import (
    audit_market_aware_session_package_diff_render,
)
from a_share_ai.analysis.market_aware_session_package_diff_renderer import (
    render_market_aware_session_package_diff,
)
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_session_package import _write_json
from tests.analysis.test_market_aware_session_package_diff import (
    _compare,
    _package_paths,
    _prepare_pair,
)


def _prepare_rendered(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    previous, current = _prepare_pair(tmp_path)
    _compare(previous, current, "diff")
    diff = current["artifact_root"] / "diff" / "market_aware_session_package_diff.json"
    diff_report = (
        current["artifact_root"] / "diff" / "market_aware_session_package_diff_report.json"
    )
    render_dir = current["artifact_root"] / "render"
    render_market_aware_session_package_diff(
        diff_path=diff,
        diff_report_path=diff_report,
        input_root=current["artifact_root"],
        output_dir=render_dir,
    )
    return current, {
        "diff": diff,
        "diff_report": diff_report,
        "markdown": render_dir / "market_aware_session_package_diff.md",
        "render_report": render_dir / "market_aware_session_package_diff_render_report.json",
        "root": current["artifact_root"],
    }


def _audit(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return audit_market_aware_session_package_diff_render(
        diff_path=paths["diff"],
        diff_report_path=paths["diff_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        artifact_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def test_ready_render_audit_is_deterministic_and_records_sizes(tmp_path: Path) -> None:
    _current, paths = _prepare_rendered(tmp_path)

    first = _audit(paths, "audit-one")
    second = _audit(paths, "audit-two")

    assert first["audit_ready"] is True
    assert first["comparison_ready"] is True
    assert first["render_ready"] is True
    assert first["decision_ready"] is False
    assert first["audit_version"] == "market-aware-session-package-diff-render-audit-v1"
    assert first["output_sha256"] == second["output_sha256"]
    assert first["diff_size_bytes"] == paths["diff"].stat().st_size
    assert first["diff_report_size_bytes"] == paths["diff_report"].stat().st_size
    assert first["markdown_size_bytes"] == paths["markdown"].stat().st_size
    assert first["render_report_size_bytes"] == paths["render_report"].stat().st_size
    assert first["diff_sha256"] == sha256_bytes(paths["diff"].read_bytes())


def test_blocked_render_audit_preserves_blocked_state(tmp_path: Path) -> None:
    previous, current = _prepare_pair(tmp_path)
    paths = _package_paths(current)
    package = json.loads(paths["package"].read_text(encoding="utf-8"))
    package["decision_ready"] = True
    _write_json(paths["package"], package)
    _compare(previous, current, "blocked-diff")
    diff = current["artifact_root"] / "blocked-diff" / "market_aware_session_package_diff.json"
    diff_report = (
        current["artifact_root"]
        / "blocked-diff"
        / "market_aware_session_package_diff_report.json"
    )
    render_dir = current["artifact_root"] / "blocked-render"
    render_market_aware_session_package_diff(
        diff_path=diff,
        diff_report_path=diff_report,
        input_root=current["artifact_root"],
        output_dir=render_dir,
    )
    blocked = {
        "diff": diff,
        "diff_report": diff_report,
        "markdown": render_dir / "market_aware_session_package_diff.md",
        "render_report": render_dir / "market_aware_session_package_diff_render_report.json",
        "root": current["artifact_root"],
    }

    report = _audit(blocked, "audit")

    assert report["audit_ready"] is True
    assert report["comparison_ready"] is False
    assert report["render_ready"] is False
    assert report["decision_ready"] is False


@pytest.mark.parametrize("mutation", ["diff", "diff_report", "markdown", "render_report"])
def test_render_audit_fails_closed_for_tamper(tmp_path: Path, mutation: str) -> None:
    _current, paths = _prepare_rendered(tmp_path / mutation)
    target = paths[mutation]
    if mutation == "markdown":
        target.write_bytes(target.read_bytes() + b"tampered\n")
    elif mutation == "render_report":
        render_report = json.loads(target.read_text(encoding="utf-8"))
        render_report["symbol"] = "tampered"
        _write_json(target, render_report)
    else:
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["decision_ready"] = True
        _write_json(target, payload)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] in {
        "HASH_MISMATCH",
        "DECISION_GATE_INVALID",
        "FIELD_MISMATCH",
    }


@pytest.mark.parametrize("mutation", ["version", "path", "json", "decision"])
def test_render_audit_rejects_invalid_contract(tmp_path: Path, mutation: str) -> None:
    _current, paths = _prepare_rendered(tmp_path / mutation)
    if mutation == "version":
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["render_version"] = "other-v1"
        _write_json(paths["render_report"], render_report)
    elif mutation == "path":
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["diff_path"] = "../outside.json"
        _write_json(paths["render_report"], render_report)
    elif mutation == "json":
        paths["diff_report"].write_text("bad-json\n", encoding="utf-8")
    else:
        render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
        render_report["decision_ready"] = True
        _write_json(paths["render_report"], render_report)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    expected = {
        "version": "VERSION_MISMATCH",
        "path": "PATH_INVALID",
        "json": "INPUT_JSON_INVALID",
        "decision": "DECISION_GATE_INVALID",
    }
    assert report["issues"][0]["code"] == expected[mutation]


def test_render_audit_detects_symbol_time_mismatch(tmp_path: Path) -> None:
    _current, paths = _prepare_rendered(tmp_path)
    render_report = json.loads(paths["render_report"].read_text(encoding="utf-8"))
    render_report["symbol"] = "600001.SZ"
    _write_json(paths["render_report"], render_report)

    report = _audit(paths, "audit")

    assert report["audit_ready"] is False
    assert report["issues"][0]["code"] == "FIELD_MISMATCH"


def test_render_audit_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    _current, paths = _prepare_rendered(tmp_path)
    output_dir = paths["root"] / "cli-audit"
    exit_code = main(
        [
            "audit-market-aware-session-package-diff-render",
            "--diff",
            str(paths["diff"]),
            "--diff-report",
            str(paths["diff_report"]),
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
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["audit_ready"] is True

    outside = paths["root"].parent / "outside-audit"
    boundary = audit_market_aware_session_package_diff_render(
        diff_path=paths["diff"],
        diff_report_path=paths["diff_report"],
        markdown_path=paths["markdown"],
        render_report_path=paths["render_report"],
        artifact_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["audit_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"
    assert not (
        outside / "market_aware_session_package_diff_render_audit_report.json"
    ).exists()
