import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.quality import audit_analysis_quality
from a_share_ai.analysis.renderer import render_analysis
from a_share_ai.cli import main
from a_share_ai.decision.decision_input import build_decision_input
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_renderer import _render_inputs


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _decision_inputs(tmp_path: Path) -> dict[str, Path]:
    analysis_path, analysis_report_path, input_root = _render_inputs(tmp_path / "workspace")
    render_dir = analysis_path.parent / "rendered"
    render_analysis(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        input_root=input_root,
        output_dir=render_dir,
    )
    render_report_path = render_dir / "analysis_render_report.json"
    quality_dir = analysis_path.parent / "quality"
    audit_analysis_quality(
        analysis_path=analysis_path,
        analysis_report_path=analysis_report_path,
        render_report_path=render_report_path,
        input_root=input_root,
        output_dir=quality_dir,
    )
    analysis_report = json.loads(analysis_report_path.read_text(encoding="utf-8"))
    bundle_path = input_root / analysis_report["input_bundle_path"]
    return {
        "analysis": analysis_path,
        "analysis_report": analysis_report_path,
        "render_report": render_report_path,
        "quality_report": quality_dir / "analysis_quality_report.json",
        "bundle": bundle_path,
        "bundle_report": bundle_path.with_name("analysis_input_report.json"),
        "input_root": input_root,
        "artifact_root": tmp_path / "workspace",
    }


def _run(inputs: dict[str, Path], output_dir: Path) -> dict[str, Any]:
    return build_decision_input(
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        render_report_path=inputs["render_report"],
        quality_report_path=inputs["quality_report"],
        bundle_path=inputs["bundle"],
        bundle_report_path=inputs["bundle_report"],
        input_root=inputs["input_root"],
        artifact_root=inputs["artifact_root"],
        output_dir=output_dir,
    )


def test_decision_input_snapshot_passes_and_is_deterministic(tmp_path: Path) -> None:
    inputs = _decision_inputs(tmp_path)
    first = _run(inputs, tmp_path / "workspace" / "decision-one")
    second = _run(inputs, tmp_path / "workspace" / "decision-two")

    assert first["status"] == "ready"
    assert first["decision_input_ready"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    snapshot = json.loads(
        (tmp_path / "workspace" / "decision-one" / "decision_input_snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    assert snapshot["decision_input_version"] == "decision-input-v1"
    assert snapshot["decision_ready"] is False
    assert snapshot["analysis"]["sha256"] == sha256_bytes(inputs["analysis"].read_bytes())
    assert ".." not in snapshot["analysis"]["path"].split("/")


def test_decision_input_cli_writes_snapshot_and_report(tmp_path: Path, capsys: Any) -> None:
    inputs = _decision_inputs(tmp_path)
    output_dir = tmp_path / "workspace" / "cli-decision"
    exit_code = main(
        [
            "build-decision-input",
            "--analysis",
            str(inputs["analysis"]),
            "--analysis-report",
            str(inputs["analysis_report"]),
            "--render-report",
            str(inputs["render_report"]),
            "--quality-report",
            str(inputs["quality_report"]),
            "--bundle",
            str(inputs["bundle"]),
            "--bundle-report",
            str(inputs["bundle_report"]),
            "--input-root",
            str(inputs["input_root"]),
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["decision_input_ready"] is True
    assert (output_dir / "decision_input_snapshot.json").exists()
    assert (output_dir / "decision_input_report.json").exists()


def test_decision_input_rejects_quality_gate_and_future_as_of(tmp_path: Path) -> None:
    inputs = _decision_inputs(tmp_path)
    quality = json.loads(inputs["quality_report"].read_text(encoding="utf-8"))
    quality["quality_ready"] = False
    _write_json(inputs["quality_report"], quality)
    report = _run(inputs, tmp_path / "workspace" / "invalid-quality")
    assert report["decision_input_ready"] is False
    assert report["issues"][0]["code"] == "READY_GATE_INVALID"

    inputs = _decision_inputs(tmp_path / "future")
    analysis = json.loads(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["as_of"] = "2099-01-01T00:00:00+00:00"
    _write_json(inputs["analysis"], analysis)
    report = _run(inputs, tmp_path / "future" / "workspace" / "invalid-future")
    assert report["decision_input_ready"] is False
    assert report["issues"][0]["code"] == "AS_OF_IN_FUTURE"


def test_decision_input_rejects_hash_and_path_mismatch(tmp_path: Path) -> None:
    inputs = _decision_inputs(tmp_path)
    render_report = json.loads(inputs["render_report"].read_text(encoding="utf-8"))
    render_report["analysis_sha256"] = "0" * 64
    _write_json(inputs["render_report"], render_report)
    report = _run(inputs, tmp_path / "workspace" / "invalid-hash")
    assert report["issues"][0]["code"] == "HASH_MISMATCH"

    inputs = _decision_inputs(tmp_path / "path")
    analysis_report = json.loads(inputs["analysis_report"].read_text(encoding="utf-8"))
    analysis_report["input_bundle_path"] = "../outside.json"
    _write_json(inputs["analysis_report"], analysis_report)
    render_report = json.loads(inputs["render_report"].read_text(encoding="utf-8"))
    render_report["analysis_report_sha256"] = sha256_bytes(
        inputs["analysis_report"].read_bytes()
    )
    _write_json(inputs["render_report"], render_report)
    report = _run(inputs, tmp_path / "path" / "workspace" / "invalid-path")
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ROOT"
