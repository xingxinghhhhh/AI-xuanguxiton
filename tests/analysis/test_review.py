import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.review import build_analysis_review
from a_share_ai.analysis.safety import audit_analysis_safety
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_safety import _prepare


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_review(tmp_path: Path) -> dict[str, Path]:
    inputs = _prepare(tmp_path)
    safety_dir = inputs["artifact_root"] / "safety"
    audit_analysis_safety(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        render_report_path=inputs["render_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=safety_dir,
    )
    inputs["safety_report"] = safety_dir / "analysis_safety_report.json"
    return inputs


def _run(inputs: dict[str, Path], output_dir: Path) -> dict[str, Any]:
    return build_analysis_review(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        safety_report_path=inputs["safety_report"],
        analysis_path=inputs["analysis"],
        analysis_report_path=inputs["analysis_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=output_dir,
    )


def test_review_packet_is_pending_complete_and_deterministic(tmp_path: Path) -> None:
    inputs = _prepare_review(tmp_path)
    first = _run(inputs, inputs["artifact_root"] / "review-one")
    second = _run(inputs, inputs["artifact_root"] / "review-two")

    assert first["status"] == "pending"
    assert first["review_packet_ready"] is True
    assert first["review_status"] == "pending"
    assert first["review_complete"] is False
    assert first["decision_ready"] is False
    assert first["claim_count"] == 8
    assert first["output_sha256"] == second["output_sha256"]
    packet = json.loads(
        (inputs["artifact_root"] / "review-one" / "analysis_review_packet.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(packet["items"]) == 8
    assert all(item["review_status"] == "pending" for item in packet["items"])
    assert all(item["review_notes"] is None for item in packet["items"])
    assert all(item["reviewed_at"] is None for item in packet["items"])
    assert {item["section"] for item in packet["items"]} == {
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
        "risks",
        "unknowns",
    }
    assert all(item["citations"] for item in packet["items"])


def test_review_cli_writes_packet_and_report(tmp_path: Path, capsys: Any) -> None:
    inputs = _prepare_review(tmp_path)
    output_dir = inputs["artifact_root"] / "review-cli"
    exit_code = main(
        [
            "build-analysis-review",
            "--decision-input",
            str(inputs["decision_input"]),
            "--decision-input-report",
            str(inputs["decision_input_report"]),
            "--safety-report",
            str(inputs["safety_report"]),
            "--analysis",
            str(inputs["analysis"]),
            "--analysis-report",
            str(inputs["analysis_report"]),
            "--artifact-root",
            str(inputs["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["review_packet_ready"] is True
    assert (output_dir / "analysis_review_packet.json").exists()
    assert (output_dir / "analysis_review_report.json").exists()


def test_review_fails_closed_when_safety_or_citation_is_invalid(tmp_path: Path) -> None:
    inputs = _prepare_review(tmp_path)
    safety = json.loads(inputs["safety_report"].read_text(encoding="utf-8"))
    safety["safety_ready"] = False
    _write_json(inputs["safety_report"], safety)
    report = _run(inputs, inputs["artifact_root"] / "invalid-safety")
    assert report["review_packet_ready"] is False
    assert report["issues"][0]["code"] == "FIELD_MISMATCH"

    inputs = _prepare_review(tmp_path / "citation")
    analysis = json.loads(inputs["analysis"].read_text(encoding="utf-8"))
    artifact = inputs["input_root"] / analysis["citations"][0]["artifact_paths"][0]
    artifact.write_text("tampered", encoding="utf-8")
    report = _run(inputs, inputs["artifact_root"] / "invalid-citation")
    assert report["review_packet_ready"] is False
    assert report["issues"][0]["code"] == "HASH_MISMATCH"


def test_review_rejects_future_snapshot_and_preserves_decision_gate(tmp_path: Path) -> None:
    inputs = _prepare_review(tmp_path)
    snapshot = json.loads(inputs["decision_input"].read_text(encoding="utf-8"))
    snapshot["as_of"] = "2099-01-01T00:00:00+00:00"
    inputs["decision_input"].write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    decision_report = json.loads(inputs["decision_input_report"].read_text(encoding="utf-8"))
    decision_report["output_sha256"] = sha256_bytes(inputs["decision_input"].read_bytes())
    _write_json(inputs["decision_input_report"], decision_report)
    report = _run(inputs, inputs["artifact_root"] / "invalid-future")
    assert report["review_packet_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] == "AS_OF_IN_FUTURE"
