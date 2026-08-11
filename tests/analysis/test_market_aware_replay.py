import json
from pathlib import Path

from a_share_ai.analysis.market_aware_replay import replay_market_aware_analysis
from a_share_ai.cli import main
from a_share_ai.evidence.analysis_input_bundle import AnalysisInputSource
from a_share_ai.market.replay import sha256_bytes
from tests.evidence.test_analysis_input_bundle import (
    add_market_context,
    build_artifacts,
    write_json,
)

FIXTURE_ROOT = Path("fixtures/analysis/report")


def _build_v2_replay_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    config = add_market_context(build_artifacts(tmp_path / "input"))
    bundle_dir = config.input_root / "analysis_input"
    bundle_report = AnalysisInputSource(config).capture(bundle_dir)
    assert bundle_report["status"] == "ready"
    return (
        bundle_dir / "analysis_input_bundle.json",
        bundle_dir / "analysis_input_report.json",
        config.input_root,
    )


def test_market_aware_replay_reaches_pending_review_packet(tmp_path: Path) -> None:
    bundle_path, bundle_report_path, input_root = _build_v2_replay_inputs(tmp_path)
    output_dir = input_root / "replay"

    report = replay_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        response_fixture=Path("fixtures/analysis/market_aware_valid_observation.json"),
        output_dir=output_dir,
    )

    assert report["status"] == "ready"
    assert report["analysis_ready"] is True
    assert report["quality_ready"] is True
    assert report["safety_ready"] is True
    assert report["review_packet_ready"] is True
    assert report["review_complete"] is False
    assert report["decision_ready"] is False
    assert report["market_context_summary_version"] == "market-context-summary-v1"
    assert report["relative_strength_version"] == "relative-strength-v1"
    assert set(report["stages"]) == {
        "analysis",
        "render",
        "quality",
        "decision_input",
        "safety",
        "review",
    }
    assert all(
        stage["status"] in {"ready", "pass", "pending"}
        for stage in report["stages"].values()
    )
    assert all(stage["report_sha256"] for stage in report["stages"].values())
    assert all(stage["output_sha256"] for stage in report["stages"].values())
    assert (output_dir / "review" / "analysis_review_packet.json").exists()


def test_market_aware_replay_rejects_v1_without_running_stages(tmp_path: Path) -> None:
    config = build_artifacts(tmp_path / "input")
    bundle_dir = config.input_root / "analysis_input"
    bundle_report = AnalysisInputSource(config).capture(bundle_dir)
    assert bundle_report["status"] == "ready"

    report = replay_market_aware_analysis(
        bundle_path=bundle_dir / "analysis_input_bundle.json",
        bundle_report_path=bundle_dir / "analysis_input_report.json",
        input_root=config.input_root,
        response_fixture=FIXTURE_ROOT / "valid_provider.json",
        output_dir=config.input_root / "replay",
    )

    assert report["status"] == "invalid"
    assert report["analysis_ready"] is False
    assert report["issues"][0]["code"] == "INPUT_INVALID"
    assert all(stage["status"] == "skipped" for stage in report["stages"].values())


def test_market_aware_replay_requires_relative_strength_summary(tmp_path: Path) -> None:
    bundle_path, bundle_report_path, input_root = _build_v2_replay_inputs(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["summaries"]["technical"].pop("relative_strength")
    bundle_raw = write_json(bundle_path, bundle)
    bundle_report = json.loads(bundle_report_path.read_text(encoding="utf-8"))
    bundle_report["bundle_sha256"] = sha256_bytes(bundle_raw)
    write_json(bundle_report_path, bundle_report)

    report = replay_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        response_fixture=FIXTURE_ROOT / "valid_provider.json",
        output_dir=input_root / "replay",
    )

    assert report["status"] == "invalid"
    assert "relative-strength summary is missing" in report["issues"][0]["message"]
    assert all(stage["status"] == "skipped" for stage in report["stages"].values())


def test_market_aware_replay_stops_after_analysis_failure(tmp_path: Path) -> None:
    bundle_path, bundle_report_path, input_root = _build_v2_replay_inputs(tmp_path)

    report = replay_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        response_fixture=Path("fixtures/analysis/market_aware_invalid_fact.json"),
        output_dir=input_root / "replay",
    )

    assert report["status"] == "invalid"
    assert report["analysis_ready"] is False
    assert report["stages"]["analysis"]["status"] == "invalid"
    assert report["stages"]["render"]["status"] == "skipped"
    assert report["issues"][0]["code"].startswith("ANALYSIS_")


def test_market_aware_replay_stops_on_bundle_report_hash_tampering(tmp_path: Path) -> None:
    bundle_path, bundle_report_path, input_root = _build_v2_replay_inputs(tmp_path)
    bundle_report = json.loads(bundle_report_path.read_text(encoding="utf-8"))
    bundle_report["bundle_sha256"] = "0" * 64
    write_json(bundle_report_path, bundle_report)

    report = replay_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        response_fixture=FIXTURE_ROOT / "valid_provider.json",
        output_dir=input_root / "replay",
    )

    assert report["status"] == "invalid"
    assert report["stages"]["analysis"]["status"] == "invalid"
    assert report["issues"][0]["code"].startswith("ANALYSIS_")


def test_market_aware_replay_fails_closed_when_output_root_is_outside_input_root(
    tmp_path: Path,
) -> None:
    bundle_path, bundle_report_path, input_root = _build_v2_replay_inputs(tmp_path)

    report = replay_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        response_fixture=FIXTURE_ROOT / "valid_provider.json",
        output_dir=tmp_path.parent.parent / "outside" / "replay",
    )

    assert report["status"] == "invalid"
    assert report["stages"]["analysis"]["ready"] is True
    assert report["stages"]["decision_input"]["status"] == "invalid"
    assert report["stages"]["safety"]["status"] == "skipped"


def test_cli_market_aware_replay_writes_deterministic_report(
    tmp_path: Path, capsys
) -> None:
    bundle_path, bundle_report_path, input_root = _build_v2_replay_inputs(tmp_path)
    output_dir = input_root / "cli-replay"
    argv = [
        "replay-market-aware-analysis",
        "--bundle",
        str(bundle_path),
        "--bundle-report",
        str(bundle_report_path),
        "--input-root",
        str(input_root),
        "--response-fixture",
        str(FIXTURE_ROOT / "valid_provider.json"),
        "--output-dir",
        str(output_dir),
    ]

    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    first_sha = sha256_bytes(
        (output_dir / "market_aware_replay_report.json").read_bytes()
    )
    assert main(argv) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["status"] == "ready"
    assert second["status"] == "ready"
    assert first_sha == sha256_bytes(
        (output_dir / "market_aware_replay_report.json").read_bytes()
    )
