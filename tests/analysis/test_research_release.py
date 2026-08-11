import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.research_release import build_research_release
from a_share_ai.cli import main
from tests.analysis.test_review_record import _apply, _prepare_record, _submission, _write_json


def _prepare_release(tmp_path: Path) -> dict[str, Path]:
    inputs = _prepare_record(tmp_path)
    _apply(inputs, _submission(inputs), "review-result")
    result_dir = inputs["artifact_root"] / "review-result"
    inputs["result"] = result_dir / "analysis_review_result.json"
    inputs["result_report"] = result_dir / "analysis_review_result_report.json"
    return inputs


def _build(inputs: dict[str, Path], output_name: str) -> dict[str, Any]:
    return build_research_release(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        safety_report_path=inputs["safety_report"],
        review_packet_path=inputs["packet"],
        review_result_path=inputs["result"],
        review_result_report_path=inputs["result_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["artifact_root"] / output_name,
    )


def test_research_release_is_ready_and_deterministic(tmp_path: Path) -> None:
    inputs = _prepare_release(tmp_path)
    first = _build(inputs, "release-one")
    second = _build(inputs, "release-two")

    assert first["status"] == "ready"
    assert first["research_release_ready"] is True
    assert first["decision_input_ready"] is True
    assert first["safety_ready"] is True
    assert first["review_complete"] is True
    assert first["review_gate_pass"] is True
    assert first["decision_ready"] is False
    assert first["artifact_count"] == 6
    assert first["output_sha256"] == second["output_sha256"]

    manifest = json.loads(
        (inputs["artifact_root"] / "release-one" / "research_release_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(not Path(item["path"]).is_absolute() for item in manifest["artifacts"].values())
    assert all(
        item["path"] == item["path"].replace("\\", "/")
        for item in manifest["artifacts"].values()
    )
    assert manifest["decision_ready"] is False


def test_research_release_cli_writes_manifest_and_report(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare_release(tmp_path)
    output_dir = inputs["artifact_root"] / "release-cli"
    exit_code = main(
        [
            "build-research-release",
            "--decision-input",
            str(inputs["decision_input"]),
            "--decision-input-report",
            str(inputs["decision_input_report"]),
            "--safety-report",
            str(inputs["safety_report"]),
            "--analysis-review-packet",
            str(inputs["packet"]),
            "--analysis-review-result",
            str(inputs["result"]),
            "--analysis-review-result-report",
            str(inputs["result_report"]),
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["research_release_ready"] is True
    assert (output_dir / "research_release_manifest.json").exists()
    assert (output_dir / "research_release_report.json").exists()


def test_release_blocks_unconfirmed_review_and_hash_mismatch(tmp_path: Path) -> None:
    inputs = _prepare_release(tmp_path)
    result = json.loads(inputs["result"].read_text(encoding="utf-8"))
    result["review_gate_pass"] = False
    _write_json(inputs["result"], result)
    report = _build(inputs, "unconfirmed")
    assert report["research_release_ready"] is False
    assert report["issues"][0]["code"] == "FIELD_MISMATCH"

    inputs = _prepare_release(tmp_path / "hash")
    result_report = json.loads(inputs["result_report"].read_text(encoding="utf-8"))
    result_report["output_sha256"] = "0" * 64
    _write_json(inputs["result_report"], result_report)
    report = _build(inputs, "hash-mismatch")
    assert report["research_release_ready"] is False
    assert report["issues"][0]["code"] == "HASH_MISMATCH"


def test_release_blocks_path_boundary_and_unready_upstream(tmp_path: Path) -> None:
    inputs = _prepare_release(tmp_path)
    nested_root = inputs["artifact_root"] / "nested-root"
    nested_root.mkdir()
    report = build_research_release(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        safety_report_path=inputs["safety_report"],
        review_packet_path=inputs["packet"],
        review_result_path=inputs["result"],
        review_result_report_path=inputs["result_report"],
        artifact_root=nested_root,
        output_dir=inputs["artifact_root"] / "path-boundary",
    )
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"

    inputs = _prepare_release(tmp_path / "unready")
    safety = json.loads(inputs["safety_report"].read_text(encoding="utf-8"))
    safety["safety_ready"] = False
    _write_json(inputs["safety_report"], safety)
    report = _build(inputs, "unready-upstream")
    assert report["research_release_ready"] is False
    assert report["issues"][0]["code"] == "FIELD_MISMATCH"
