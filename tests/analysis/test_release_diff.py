import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.release_diff import compare_research_releases
from a_share_ai.analysis.research_release import build_research_release
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_review_record import _apply, _prepare_record, _submission, _write_json


def _prepare_release(tmp_path: Path) -> dict[str, Path]:
    inputs = _prepare_record(tmp_path)
    _apply(inputs, _submission(inputs), "review-result")
    result_dir = inputs["artifact_root"] / "review-result"
    inputs["result"] = result_dir / "analysis_review_result.json"
    inputs["result_report"] = result_dir / "analysis_review_result_report.json"
    inputs["release"] = inputs["artifact_root"] / "release"
    build_research_release(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        safety_report_path=inputs["safety_report"],
        review_packet_path=inputs["packet"],
        review_result_path=inputs["result"],
        review_result_report_path=inputs["result_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["release"],
    )
    inputs["manifest"] = inputs["release"] / "research_release_manifest.json"
    inputs["release_report"] = inputs["release"] / "research_release_report.json"
    return inputs


def _refresh_release(inputs: dict[str, Path], as_of: str, *, claim_text: str | None = None) -> None:
    snapshot = json.loads(inputs["decision_input"].read_text(encoding="utf-8"))
    snapshot["as_of"] = as_of
    _write_json(inputs["decision_input"], snapshot)
    snapshot_sha = sha256_bytes(inputs["decision_input"].read_bytes())

    snapshot_report = json.loads(inputs["decision_input_report"].read_text(encoding="utf-8"))
    snapshot_report["as_of"] = as_of
    snapshot_report["output_sha256"] = snapshot_sha
    _write_json(inputs["decision_input_report"], snapshot_report)

    safety = json.loads(inputs["safety_report"].read_text(encoding="utf-8"))
    safety["as_of"] = as_of
    _write_json(inputs["safety_report"], safety)
    safety_sha = sha256_bytes(inputs["safety_report"].read_bytes())

    packet = json.loads(inputs["packet"].read_text(encoding="utf-8"))
    packet["as_of"] = as_of
    packet["decision_input_sha256"] = snapshot_sha
    packet["safety_report_sha256"] = safety_sha
    if claim_text is not None:
        packet["items"][0]["text"] = claim_text
    _write_json(inputs["packet"], packet)
    packet_sha = sha256_bytes(inputs["packet"].read_bytes())

    result = json.loads(inputs["result"].read_text(encoding="utf-8"))
    result["as_of"] = as_of
    result["review_packet_sha256"] = packet_sha
    _write_json(inputs["result"], result)
    result_sha = sha256_bytes(inputs["result"].read_bytes())

    result_report = json.loads(inputs["result_report"].read_text(encoding="utf-8"))
    result_report["as_of"] = as_of
    result_report["output_sha256"] = result_sha
    result_report["review_packet_sha256"] = packet_sha
    _write_json(inputs["result_report"], result_report)

    build_research_release(
        decision_input_path=inputs["decision_input"],
        decision_input_report_path=inputs["decision_input_report"],
        safety_report_path=inputs["safety_report"],
        review_packet_path=inputs["packet"],
        review_result_path=inputs["result"],
        review_result_report_path=inputs["result_report"],
        artifact_root=inputs["artifact_root"],
        output_dir=inputs["release"],
    )


def _compare(
    previous: dict[str, Path], current: dict[str, Path], output_name: str
) -> dict[str, Any]:
    return compare_research_releases(
        previous_manifest_path=previous["manifest"],
        previous_report_path=previous["release_report"],
        current_manifest_path=current["manifest"],
        current_report_path=current["release_report"],
        previous_artifact_root=previous["artifact_root"],
        current_artifact_root=current["artifact_root"],
        output_dir=current["artifact_root"] / output_name,
    )


def test_release_diff_is_deterministic_and_classifies_claim_changes(tmp_path: Path) -> None:
    previous = _prepare_release(tmp_path / "previous")
    current = _prepare_release(tmp_path / "current")
    _refresh_release(previous, "2026-08-09T12:00:00+00:00")
    _refresh_release(current, "2026-08-10T12:00:00+00:00", claim_text="Updated literal claim text.")

    first = _compare(previous, current, "diff-one")
    second = _compare(previous, current, "diff-two")

    assert first["status"] == "ready"
    assert first["comparison_ready"] is True
    assert first["decision_ready"] is False
    assert first["claim_changed_count"] == 1
    assert first["claim_unchanged_count"] == 7
    assert first["claim_added_count"] == 0
    assert first["claim_removed_count"] == 0
    assert first["output_sha256"] == second["output_sha256"]
    diff = json.loads(
        (current["artifact_root"] / "diff-one" / "research_release_diff.json").read_text(
            encoding="utf-8"
        )
    )
    assert diff["risk_changes"]["current"] == diff["risk_changes"]["previous"]
    assert diff["unknown_changes"]["current"] == diff["unknown_changes"]["previous"]


def test_release_diff_cli_writes_diff_and_report(tmp_path: Path, capsys: Any) -> None:
    previous = _prepare_release(tmp_path / "previous")
    current = _prepare_release(tmp_path / "current")
    _refresh_release(previous, "2026-08-09T12:00:00+00:00")
    _refresh_release(current, "2026-08-10T12:00:00+00:00")
    output_dir = current["artifact_root"] / "cli-diff"

    exit_code = main(
        [
            "compare-research-releases",
            "--previous-manifest",
            str(previous["manifest"]),
            "--previous-report",
            str(previous["release_report"]),
            "--current-manifest",
            str(current["manifest"]),
            "--current-report",
            str(current["release_report"]),
            "--previous-artifact-root",
            str(previous["artifact_root"]),
            "--current-artifact-root",
            str(current["artifact_root"]),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["comparison_ready"] is True
    assert (output_dir / "research_release_diff.json").exists()
    assert (output_dir / "research_release_diff_report.json").exists()


def test_release_diff_fails_closed_for_order_hash_path_and_decision_field(tmp_path: Path) -> None:
    previous = _prepare_release(tmp_path / "previous")
    current = _prepare_release(tmp_path / "current")
    _refresh_release(previous, "2026-08-10T12:00:00+00:00")
    _refresh_release(current, "2026-08-09T12:00:00+00:00")
    report = _compare(previous, current, "order")
    assert report["issues"][0]["code"] == "AS_OF_ORDER_INVALID"

    previous = _prepare_release(tmp_path / "hash")
    current = _prepare_release(tmp_path / "hash-current")
    _refresh_release(previous, "2026-08-09T12:00:00+00:00")
    _refresh_release(current, "2026-08-10T12:00:00+00:00")
    manifest = json.loads(current["manifest"].read_text(encoding="utf-8"))
    manifest["artifacts"]["analysis_review_packet"]["sha256"] = "0" * 64
    _write_json(current["manifest"], manifest)
    report = _compare(previous, current, "hash-mismatch")
    assert report["issues"][0]["code"] == "HASH_MISMATCH"

    previous = _prepare_release(tmp_path / "path")
    current = _prepare_release(tmp_path / "path-current")
    _refresh_release(previous, "2026-08-09T12:00:00+00:00")
    _refresh_release(current, "2026-08-10T12:00:00+00:00")
    nested = current["artifact_root"] / "nested"
    nested.mkdir()
    report = compare_research_releases(
        previous_manifest_path=previous["manifest"],
        previous_report_path=previous["release_report"],
        current_manifest_path=current["manifest"],
        current_report_path=current["release_report"],
        previous_artifact_root=previous["artifact_root"],
        current_artifact_root=nested,
        output_dir=current["artifact_root"] / "path-mismatch",
    )
    assert report["issues"][0]["code"] == "PATH_OUTSIDE_ARTIFACT_ROOT"

    previous = _prepare_release(tmp_path / "field")
    current = _prepare_release(tmp_path / "field-current")
    _refresh_release(previous, "2026-08-09T12:00:00+00:00")
    _refresh_release(current, "2026-08-10T12:00:00+00:00")
    manifest = json.loads(current["manifest"].read_text(encoding="utf-8"))
    manifest["decision"] = "HOLD"
    _write_json(current["manifest"], manifest)
    report = _compare(previous, current, "decision-field")
    assert report["issues"][0]["code"] == "FORBIDDEN_DECISION_FIELD"
