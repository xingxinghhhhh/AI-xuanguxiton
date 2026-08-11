import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.quality import audit_analysis_quality
from a_share_ai.analysis.renderer import render_analysis
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_renderer import _render_inputs


def _quality_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    analysis_path, analysis_report_path, input_root = _render_inputs(tmp_path / "inputs")
    render_dir = tmp_path / "rendered"
    render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        input_root=input_root,
        output_dir=render_dir,
    )
    return (
        analysis_path,
        analysis_report_path,
        render_dir / "analysis_render_report.json",
        input_root,
    )


def _rewrite_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _refresh_analysis_and_render(
    analysis_path: Path,
    analysis_report_path: Path,
    input_root: Path,
    render_report_path: Path,
) -> None:
    report = json.loads(analysis_report_path.read_text(encoding="utf-8"))
    report["output_sha256"] = sha256_bytes(analysis_path.read_bytes())
    _rewrite_json(analysis_report_path, report)
    render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        input_root=input_root,
        output_dir=render_report_path.parent,
    )


def test_quality_audit_passes_for_valid_rendered_analysis(tmp_path: Path) -> None:
    analysis_path, analysis_report_path, render_report_path, input_root = _quality_inputs(tmp_path)

    report = audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=tmp_path / "quality",
    )

    assert report["quality_status"] == "pass"
    assert report["quality_ready"] is True
    assert report["decision_ready"] is False
    assert report["citation_coverage_percent"] == 100.0
    assert all(report["evidence_usage"].values())
    assert set(report["section_claim_counts"]) == {
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
        "risks",
        "unknowns",
    }


def test_quality_cli_writes_audit_report(tmp_path: Path, capsys: Any) -> None:
    analysis_path, analysis_report_path, render_report_path, input_root = _quality_inputs(tmp_path)

    exit_code = main(
        [
            "audit-analysis-quality",
            "--analysis",
            str(analysis_path),
            "--analysis-report",
            str(analysis_report_path),
            "--render-report",
            str(render_report_path),
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "quality"),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["quality_ready"] is True


def test_quality_rejects_wrong_section_evidence_mapping(tmp_path: Path) -> None:
    analysis_path, analysis_report_path, render_report_path, input_root = _quality_inputs(tmp_path)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["sections"]["market"][0]["citation_ids"] = ["technical"]
    _rewrite_json(analysis_path, analysis)
    _refresh_analysis_and_render(
        analysis_path, analysis_report_path, input_root, render_report_path
    )

    report = audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=tmp_path / "quality",
    )

    assert report["quality_status"] == "invalid"
    assert report["issues"][0]["code"] == "SECTION_EVIDENCE_MISMATCH"


def test_quality_rejects_unused_evidence(tmp_path: Path) -> None:
    analysis_path, analysis_report_path, render_report_path, input_root = _quality_inputs(tmp_path)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["sections"]["technical"][0]["citation_ids"] = ["market"]
    analysis["sections"]["price_plan"][0]["citation_ids"] = ["technical"]
    _rewrite_json(analysis_path, analysis)
    _refresh_analysis_and_render(
        analysis_path, analysis_report_path, input_root, render_report_path
    )

    report = audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=tmp_path / "quality",
    )

    assert report["quality_status"] == "invalid"
    assert report["issues"][0]["code"] == "EVIDENCE_COVERAGE"


def test_quality_rejects_hash_mismatch_and_decision_gate(tmp_path: Path) -> None:
    analysis_path, analysis_report_path, render_report_path, input_root = _quality_inputs(tmp_path)
    render_report = json.loads(render_report_path.read_text(encoding="utf-8"))
    render_report["analysis_sha256"] = "0" * 64
    _rewrite_json(render_report_path, render_report)

    report = audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=tmp_path / "quality-one",
    )
    assert report["issues"][0]["code"] == "RENDER_ANALYSIS_HASH_MISMATCH"

    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["decision_ready"] = True
    _rewrite_json(analysis_path, analysis)
    analysis_report = json.loads(analysis_report_path.read_text(encoding="utf-8"))
    analysis_report["output_sha256"] = sha256_bytes(analysis_path.read_bytes())
    _rewrite_json(analysis_report_path, analysis_report)
    render_report = json.loads(render_report_path.read_text(encoding="utf-8"))
    render_report["analysis_sha256"] = sha256_bytes(analysis_path.read_bytes())
    render_report["analysis_report_sha256"] = sha256_bytes(analysis_report_path.read_bytes())
    render_report["decision_ready"] = True
    _rewrite_json(render_report_path, render_report)
    report = audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=tmp_path / "quality-two",
    )
    assert report["issues"][0]["code"] == "DECISION_GATE_INVALID"


def test_quality_rejects_missing_risk_or_unknown_section(tmp_path: Path) -> None:
    analysis_path, analysis_report_path, render_report_path, input_root = _quality_inputs(tmp_path)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["sections"]["risks"] = []
    _rewrite_json(analysis_path, analysis)
    _refresh_analysis_and_render(
        analysis_path, analysis_report_path, input_root, render_report_path
    )

    report = audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=tmp_path / "quality",
    )

    assert report["issues"][0]["code"] == "SECTION_COVERAGE"
