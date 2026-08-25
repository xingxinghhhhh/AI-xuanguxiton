import json
from pathlib import Path

from a_share_ai.analysis.market_aware_release_replay import replay_market_aware_release
from a_share_ai.analysis.market_aware_replay import replay_market_aware_analysis
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes
from tests.analysis.test_market_aware_replay import _build_v2_replay_inputs

VALID_FIXTURE = Path("fixtures/analysis/market_aware_valid_observation.json")


def _build_pending_review(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    bundle_path, bundle_report_path, artifact_root = _build_v2_replay_inputs(tmp_path)
    replay_dir = artifact_root / "replay"
    replay_report = replay_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=artifact_root,
        response_fixture=VALID_FIXTURE,
        output_dir=replay_dir,
    )
    assert replay_report["review_packet_ready"] is True
    return (
        replay_dir / "review" / "analysis_review_packet.json",
        replay_dir / "review" / "analysis_review_report.json",
        artifact_root,
        replay_dir,
    )


def _write_submission(packet_path: Path, *, status: str = "confirmed") -> Path:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    items = []
    for packet_item in packet["items"]:
        items.append(
            {
                "review_id": packet_item["review_id"],
                "status": status,
                "notes": None if status == "confirmed" else "needs follow-up",
                "reviewed_at": "2026-08-11T12:00:00+00:00",
            }
        )
    submission = {
        "review_packet_sha256": sha256_bytes(packet_path.read_bytes()),
        "items": items,
    }
    path = packet_path.parent / "review_submission.json"
    path.write_text(json.dumps(submission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_market_aware_release_replay_builds_release_for_all_confirmed(
    tmp_path: Path,
) -> None:
    packet_path, packet_report_path, artifact_root, _replay_dir = _build_pending_review(tmp_path)
    submission_path = _write_submission(packet_path)
    output_dir = artifact_root / "release-replay"

    report = replay_market_aware_release(
        packet_path=packet_path,
        packet_report_path=packet_report_path,
        submission_path=submission_path,
        artifact_root=artifact_root,
        output_dir=output_dir,
    )

    assert report["status"] == "ready"
    assert report["review_complete"] is True
    assert report["review_gate_pass"] is True
    assert report["research_release_ready"] is True
    assert report["decision_ready"] is False
    assert report["stages"]["review_record"]["status"] == "ready"
    assert report["stages"]["research_release"]["status"] == "ready"
    manifest_path = output_dir / "release" / "research_release_manifest.json"
    assert manifest_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["decision_ready"] is False


def test_market_aware_release_replay_stops_when_review_gate_fails(tmp_path: Path) -> None:
    packet_path, packet_report_path, artifact_root, _replay_dir = _build_pending_review(tmp_path)
    submission_path = _write_submission(packet_path, status="challenged")

    report = replay_market_aware_release(
        packet_path=packet_path,
        packet_report_path=packet_report_path,
        submission_path=submission_path,
        artifact_root=artifact_root,
        output_dir=artifact_root / "release-replay",
    )

    assert report["status"] == "review_gate_failed"
    assert report["review_complete"] is True
    assert report["review_gate_pass"] is False
    assert report["research_release_ready"] is False
    assert report["stages"]["research_release"]["status"] == "skipped"
    manifest_path = (
        artifact_root / "release-replay" / "release" / "research_release_manifest.json"
    )
    assert not manifest_path.exists()


def test_market_aware_release_replay_rejects_submission_packet_hash(tmp_path: Path) -> None:
    packet_path, packet_report_path, artifact_root, _replay_dir = _build_pending_review(tmp_path)
    submission_path = _write_submission(packet_path)
    submission = json.loads(submission_path.read_text(encoding="utf-8"))
    submission["review_packet_sha256"] = "0" * 64
    submission_path.write_text(json.dumps(submission) + "\n", encoding="utf-8")

    report = replay_market_aware_release(
        packet_path=packet_path,
        packet_report_path=packet_report_path,
        submission_path=submission_path,
        artifact_root=artifact_root,
        output_dir=artifact_root / "release-replay",
    )

    assert report["status"] == "invalid"
    assert report["stages"]["review_record"]["status"] == "invalid"
    assert report["stages"]["research_release"]["status"] == "skipped"
    assert report["issues"][0]["code"] == "PACKET_SHA_MISMATCH"


def test_cli_market_aware_release_replay_returns_ready(tmp_path: Path, capsys) -> None:
    packet_path, packet_report_path, artifact_root, _replay_dir = _build_pending_review(tmp_path)
    submission_path = _write_submission(packet_path)
    output_dir = artifact_root / "release-replay"

    assert (
        main(
            [
                "replay-market-aware-release",
                "--packet",
                str(packet_path),
                "--packet-report",
                str(packet_report_path),
                "--submission",
                str(submission_path),
                "--artifact-root",
                str(artifact_root),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ready"
    assert report["research_release_ready"] is True
