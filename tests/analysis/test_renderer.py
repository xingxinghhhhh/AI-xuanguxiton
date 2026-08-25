import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.renderer import render_analysis
from a_share_ai.analysis.validator import AnalysisReportSource
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_validator import FIXTURE_ROOT, _build_config


def _render_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    config = _build_config(tmp_path, FIXTURE_ROOT / "valid_provider.json")
    AnalysisReportSource(config).capture()
    return (
        config.output_dir / "research_analysis.json",
        config.output_dir / "research_analysis_report.json",
        config.input_root,
    )


def _rewrite_json(path: Path, payload: dict[str, Any]) -> bytes:
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    path.write_bytes(raw)
    return raw


def _refresh_analysis_hash(report_path: Path, analysis_path: Path) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["output_sha256"] = sha256_bytes(analysis_path.read_bytes())
    _rewrite_json(report_path, report)


def test_render_is_deterministic_and_contains_evidence_navigation(tmp_path: Path) -> None:
    analysis_path, report_path, input_root = _render_inputs(tmp_path / "inputs")

    first = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=report_path,
        input_root=input_root,
        output_dir=tmp_path / "output-one",
    )
    second = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=report_path,
        input_root=input_root,
        output_dir=tmp_path / "output-two",
    )

    assert first["status"] == "ready"
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    markdown = (tmp_path / "output-one" / "research_analysis.md").read_text(encoding="utf-8")
    sections = (
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
        "risks",
        "unknowns",
    )
    positions = [
        markdown.index(f"## {index}. {section}")
        for index, section in enumerate(sections, start=2)
    ]
    assert positions == sorted(positions)
    assert "Evidence citations" in markdown
    assert "report_sha256" in markdown
    assert "not a trading decision" in markdown


def test_cli_render_analysis_writes_report_and_returns_success(
    tmp_path: Path, capsys: Any
) -> None:
    analysis_path, report_path, input_root = _render_inputs(tmp_path / "inputs")

    exit_code = main(
        [
            "render-analysis",
            "--analysis",
            str(analysis_path),
            "--analysis-report",
            str(report_path),
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "cli-output"),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_render_fails_closed_when_analysis_sha_does_not_match(tmp_path: Path) -> None:
    analysis_path, report_path, input_root = _render_inputs(tmp_path / "inputs")
    analysis_path.write_text(analysis_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    report = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=report_path,
        input_root=input_root,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "ANALYSIS_HASH_MISMATCH"
    assert not (tmp_path / "output" / "research_analysis.md").exists()


def test_render_fails_closed_on_citation_hash_mismatch(tmp_path: Path) -> None:
    analysis_path, report_path, input_root = _render_inputs(tmp_path / "inputs")
    citation = json.loads(analysis_path.read_text(encoding="utf-8"))["citations"][0]
    artifact = input_root / citation["artifact_paths"][0]
    artifact.write_text("tampered", encoding="utf-8")

    report = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=report_path,
        input_root=input_root,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "CITATION_HASH_MISMATCH"


def test_render_rejects_citation_path_escape(tmp_path: Path) -> None:
    analysis_path, report_path, input_root = _render_inputs(tmp_path / "inputs")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["citations"][0]["report_path"] = "../outside.json"
    _rewrite_json(analysis_path, analysis)
    _refresh_analysis_hash(report_path, analysis_path)

    report = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=report_path,
        input_root=input_root,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_INPUT_ROOT"


def test_render_escapes_markdown_in_claim_text(tmp_path: Path) -> None:
    analysis_path, report_path, input_root = _render_inputs(tmp_path / "inputs")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["sections"]["market"][0]["text"] = (
        "# injected\n[bad](https://example.invalid)\n<script>alert(1)</script>\n```code```"
    )
    _rewrite_json(analysis_path, analysis)
    _refresh_analysis_hash(report_path, analysis_path)

    report = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=report_path,
        input_root=input_root,
        output_dir=tmp_path / "output",
    )
    markdown = (tmp_path / "output" / "research_analysis.md").read_text(encoding="utf-8")

    assert report["status"] == "ready"
    assert "\n# injected" not in markdown
    assert "](https://example.invalid)" not in markdown
    assert "<script>" not in markdown
    assert "```code```" not in markdown


def test_render_rejects_not_ready_or_decision_ready_input(tmp_path: Path) -> None:
    analysis_path, report_path, input_root = _render_inputs(tmp_path / "inputs")
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["decision_ready"] = True
    _rewrite_json(analysis_path, analysis)
    _refresh_analysis_hash(report_path, analysis_path)
    report = render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=report_path,
        input_root=input_root,
        output_dir=tmp_path / "output",
    )

    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "DECISION_GATE_INVALID"
