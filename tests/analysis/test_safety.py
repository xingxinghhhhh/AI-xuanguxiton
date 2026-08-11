import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.quality import audit_analysis_quality
from a_share_ai.analysis.renderer import render_analysis
from a_share_ai.analysis.safety import audit_analysis_safety
from a_share_ai.cli import main
from a_share_ai.decision.decision_input import build_decision_input
from a_share_ai.market.replay import sha256_bytes
from tests.decision.test_decision_input import _decision_inputs


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare(tmp_path: Path) -> dict[str, Path]:
    inputs = _decision_inputs(tmp_path)
    decision_dir = inputs["artifact_root"] / "decision-input"
    build_decision_input(
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        render_report_path=inputs["render_report"],
        quality_report_path=inputs["quality_report"],
        bundle_path=inputs["bundle"],
        bundle_report_path=inputs["bundle_report"],
        input_root=inputs["input_root"],
        artifact_root=inputs["artifact_root"],
        output_dir=decision_dir,
    )
    inputs["decision_input"] = decision_dir / "decision_input_snapshot.json"
    inputs["decision_input_report"] = decision_dir / "decision_input_report.json"
    inputs["markdown"] = inputs["render_report"].parent / "research_analysis.md"
    return inputs


def _run(inputs: dict[str, Path], output_dir: Path) -> dict[str, Any]:
    return audit_analysis_safety(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        render_report_path=inputs["render_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=output_dir,
    )


def _refresh_chain(inputs: dict[str, Path]) -> None:
    analysis_report = json.loads(inputs["analysis_report"].read_text(encoding="utf-8"))
    analysis_report["output_sha256"] = sha256_bytes(inputs["analysis"].read_bytes())
    _write_json(inputs["analysis_report"], analysis_report)
    render_analysis(
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        input_root=inputs["input_root"],
        output_dir=inputs["render_report"].parent,
    )
    audit_analysis_quality(
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        render_report_path=inputs["render_report"],
        input_root=inputs["input_root"],
        output_dir=inputs["quality_report"].parent,
    )
    build_decision_input(
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        render_report_path=inputs["render_report"],
        quality_report_path=inputs["quality_report"],
        bundle_path=inputs["bundle"],
        bundle_report_path=inputs["bundle_report"],
        input_root=inputs["input_root"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["decision_input"].parent,
    )


def test_safety_passes_real_shape_and_is_deterministic(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path)
    first = _run(inputs, inputs["artifact_root"] / "safety-one")
    second = _run(inputs, inputs["artifact_root"] / "safety-two")

    assert first["status"] == "pass"
    assert first["safety_ready"] is True
    assert first["decision_input_ready"] is True
    assert first["decision_ready"] is False
    assert first["findings"] == []
    assert sha256_bytes(
        (inputs["artifact_root"] / "safety-one" / "analysis_safety_report.json").read_bytes()
    ) == sha256_bytes(
        (inputs["artifact_root"] / "safety-two" / "analysis_safety_report.json").read_bytes()
    )
    assert second["safety_ready"] is True


def test_safety_cli_writes_report(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare(tmp_path)
    output_dir = inputs["artifact_root"] / "safety-cli"
    exit_code = main(
        [
            "audit-analysis-safety",
            "--decision-input",
            str(inputs["decision_input"]),
            "--decision-input-report",
            str(inputs["decision_input_report"]),
            "--analysis",
            str(inputs["analysis"]),
            "--analysis-report",
            str(inputs["analysis_report"]),
            "--render-report",
            str(inputs["render_report"]),
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["safety_ready"] is True


def test_safety_rejects_action_terms_and_unknown_action_fields(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path)
    analysis = json.loads(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["sections"]["market"][0]["text"] = "可买入并设置止损"
    _write_json(inputs["analysis"], analysis)
    _refresh_chain(inputs)

    report = _run(inputs, inputs["artifact_root"] / "unsafe-language")
    assert report["status"] == "unsafe"
    assert report["safety_ready"] is False
    assert {finding["rule_id"] for finding in report["findings"]} >= {
        "ACTION_WORD",
        "EXECUTION_PRICE",
        "RECOMMENDATION",
    }

    inputs = _prepare(tmp_path / "field")
    snapshot = json.loads(inputs["decision_input"].read_text(encoding="utf-8"))
    snapshot["recommendation"] = "BUY"
    _write_json(inputs["decision_input"], snapshot)
    decision_report = json.loads(inputs["decision_input_report"].read_text(encoding="utf-8"))
    decision_report["output_sha256"] = sha256_bytes(inputs["decision_input"].read_bytes())
    _write_json(inputs["decision_input_report"], decision_report)
    report = _run(inputs, inputs["artifact_root"] / "unsafe-field")
    assert report["issues"] == [
        {
            "code": "FORBIDDEN_ANALYSIS_LANGUAGE",
            "message": "forbidden action or execution language was found",
        }
    ]
    assert any(finding["rule_id"] == "FORBIDDEN_FIELD" for finding in report["findings"])


def test_safety_rejects_hidden_markdown_and_tampering(tmp_path: Path) -> None:
    inputs = _prepare(tmp_path)
    inputs["markdown"].write_text(
        inputs["markdown"].read_text(encoding="utf-8")
        + "\n[卖出](https://example.invalid)\n```BUY```\n<span>止损</span>\n",
        encoding="utf-8",
    )
    render_report = json.loads(inputs["render_report"].read_text(encoding="utf-8"))
    render_report["output_sha256"] = sha256_bytes(inputs["markdown"].read_bytes())
    _write_json(inputs["render_report"], render_report)
    quality = json.loads(inputs["quality_report"].read_text(encoding="utf-8"))
    quality["rendered_report_sha256"] = render_report["output_sha256"]
    _write_json(inputs["quality_report"], quality)
    snapshot = json.loads(inputs["decision_input"].read_text(encoding="utf-8"))
    snapshot["render"]["sha256"] = sha256_bytes(inputs["render_report"].read_bytes())
    snapshot["render"]["output_sha256"] = render_report["output_sha256"]
    snapshot["quality"]["sha256"] = sha256_bytes(inputs["quality_report"].read_bytes())
    snapshot["quality"]["rendered_report_sha256"] = render_report["output_sha256"]
    _write_json(inputs["decision_input"], snapshot)
    decision_report = json.loads(inputs["decision_input_report"].read_text(encoding="utf-8"))
    decision_report["output_sha256"] = sha256_bytes(inputs["decision_input"].read_bytes())
    _write_json(inputs["decision_input_report"], decision_report)

    report = _run(inputs, inputs["artifact_root"] / "unsafe-markdown")
    assert report["status"] == "unsafe"
    assert any(finding["rule_id"] == "MARKUP_BYPASS" for finding in report["findings"])

    inputs = _prepare(tmp_path / "tampered")
    inputs["analysis"].write_text(
        inputs["analysis"].read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    report = _run(inputs, inputs["artifact_root"] / "tampered-analysis")
    assert report["status"] == "invalid"
    assert report["issues"][0]["code"] == "HASH_MISMATCH"
