import json
from pathlib import Path
from typing import Any

import pytest

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


def _build_render_input(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    previous, current = _prepare_pair(tmp_path)
    _compare(previous, current, "diff")
    paths = {
        "diff": current["artifact_root"] / "diff" / "market_aware_session_package_diff.json",
        "diff_report": current["artifact_root"]
        / "diff"
        / "market_aware_session_package_diff_report.json",
        "root": current["artifact_root"],
    }
    return current, paths


def _render(paths: dict[str, Path], output_name: str) -> dict[str, Any]:
    return render_market_aware_session_package_diff(
        diff_path=paths["diff"],
        diff_report_path=paths["diff_report"],
        input_root=paths["root"],
        output_dir=paths["root"] / output_name,
    )


def _refresh_diff_report(paths: dict[str, Path], *, symbol: str | None = None) -> None:
    diff = json.loads(paths["diff"].read_text(encoding="utf-8"))
    report = json.loads(paths["diff_report"].read_text(encoding="utf-8"))
    if symbol is not None:
        diff["symbol"] = symbol
        report["symbol"] = symbol
    _write_json(paths["diff"], diff)
    report["output_sha256"] = sha256_bytes(paths["diff"].read_bytes())
    _write_json(paths["diff_report"], report)


def test_changed_diff_renders_deterministically(tmp_path: Path) -> None:
    _current, paths = _build_render_input(tmp_path)

    first = _render(paths, "render-one")
    second = _render(paths, "render-two")

    assert first["render_ready"] is True
    assert first["comparison_ready"] is True
    assert first["decision_ready"] is False
    assert first["render_version"] == "market-aware-session-package-diff-render-v1"
    assert first["output_sha256"] == second["output_sha256"]
    markdown_path = paths["root"] / "render-one" / "market_aware_session_package_diff.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Field changes" in markdown
    assert "## Artifact changes" in markdown
    assert "## SHA-256 changes" in markdown
    assert "does not judge market direction" in markdown
    report_path = (
        paths["root"] / "render-one" / "market_aware_session_package_diff_render_report.json"
    )
    rendered_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert rendered_report["output_sha256"] == sha256_bytes(markdown_path.read_bytes())


def test_renderer_preserves_unchanged_field_and_artifact_rows(tmp_path: Path) -> None:
    _current, paths = _build_render_input(tmp_path)

    report = _render(paths, "render")
    diff = json.loads(paths["diff"].read_text(encoding="utf-8"))
    markdown = (paths["root"] / "render" / "market_aware_session_package_diff.md").read_text(
        encoding="utf-8"
    )

    assert report["render_ready"] is True
    assert "`package_version`" in markdown
    assert "`session_markdown`" in markdown
    assert any(
        change["status"] == "unchanged"
        for change in diff["field_changes"].values()
    )
    assert any(
        change["status"] == "unchanged"
        for change in diff["artifact_changes"].values()
    )


def test_blocked_diff_renders_issue_without_becoming_ready(tmp_path: Path) -> None:
    previous, current = _prepare_pair(tmp_path)
    _compare(previous, current, "diff")
    paths = _package_paths(current)
    package = json.loads(paths["package"].read_text(encoding="utf-8"))
    package["symbol"] = "600001.SZ"
    _write_json(paths["package"], package)
    _compare(previous, current, "blocked")
    blocked_paths = {
        "diff": current["artifact_root"] / "blocked" / "market_aware_session_package_diff.json",
        "diff_report": current["artifact_root"]
        / "blocked"
        / "market_aware_session_package_diff_report.json",
        "root": current["artifact_root"],
    }

    report = _render(blocked_paths, "blocked-render")

    assert report["render_ready"] is False
    assert report["comparison_ready"] is False
    assert report["issues"][0]["code"] == "PACKAGE_AUDIT_FAILED"
    markdown = (
        current["artifact_root"] / "blocked-render" / "market_aware_session_package_diff.md"
    ).read_text(encoding="utf-8")
    assert "## Comparison issues" in markdown


@pytest.mark.parametrize("mutation", ["sha", "version", "decision", "json"])
def test_renderer_fails_closed_for_invalid_diff_inputs(tmp_path: Path, mutation: str) -> None:
    _current, paths = _build_render_input(tmp_path / mutation)
    if mutation == "sha":
        report = json.loads(paths["diff_report"].read_text(encoding="utf-8"))
        report["output_sha256"] = "0" * 64
        _write_json(paths["diff_report"], report)
    elif mutation == "version":
        diff = json.loads(paths["diff"].read_text(encoding="utf-8"))
        diff["diff_version"] = "other-v1"
        _write_json(paths["diff"], diff)
    elif mutation == "decision":
        diff = json.loads(paths["diff"].read_text(encoding="utf-8"))
        diff["decision_ready"] = True
        _write_json(paths["diff"], diff)
        report = json.loads(paths["diff_report"].read_text(encoding="utf-8"))
        report["output_sha256"] = sha256_bytes(paths["diff"].read_bytes())
        _write_json(paths["diff_report"], report)
    else:
        paths["diff"].write_text("not-json\n", encoding="utf-8")

    report = _render(paths, "render")

    assert report["render_ready"] is False
    expected = {
        "sha": "HASH_MISMATCH",
        "version": "VERSION_MISMATCH",
        "decision": "DECISION_GATE_INVALID",
        "json": "INPUT_JSON_INVALID",
    }
    assert report["issues"][0]["code"] == expected[mutation]


def test_renderer_escapes_markdown_injection(tmp_path: Path) -> None:
    _current, paths = _build_render_input(tmp_path)
    diff = json.loads(paths["diff"].read_text(encoding="utf-8"))
    diff["symbol"] = "<script>|# [x](javascript:bad)`"
    diff["artifact_changes"]["session"]["current"]["path"] = "safe/<script>|`#"
    _write_json(paths["diff"], diff)
    report = json.loads(paths["diff_report"].read_text(encoding="utf-8"))
    report["symbol"] = diff["symbol"]
    report["output_sha256"] = sha256_bytes(paths["diff"].read_bytes())
    _write_json(paths["diff_report"], report)

    rendered = _render(paths, "render")

    assert rendered["render_ready"] is True
    markdown = (paths["root"] / "render" / "market_aware_session_package_diff.md").read_text(
        encoding="utf-8"
    )
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "\\|" in markdown
    assert "\\#" in markdown


def test_renderer_cli_and_output_boundary(tmp_path: Path, capsys: Any) -> None:
    _current, paths = _build_render_input(tmp_path)
    output_dir = paths["root"] / "cli-render"
    exit_code = main(
        [
            "render-market-aware-session-package-diff",
            "--diff",
            str(paths["diff"]),
            "--diff-report",
            str(paths["diff_report"]),
            "--input-root",
            str(paths["root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["render_ready"] is True

    outside = paths["root"].parent / "outside-render"
    boundary = render_market_aware_session_package_diff(
        diff_path=paths["diff"],
        diff_report_path=paths["diff_report"],
        input_root=paths["root"],
        output_dir=outside,
    )
    assert boundary["render_ready"] is False
    assert boundary["issues"][0]["code"] == "PATH_OUTSIDE_INPUT_ROOT"
    assert not (outside / "market_aware_session_package_diff_render_report.json").exists()
