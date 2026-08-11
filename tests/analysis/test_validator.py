import json
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.contracts import ANALYSIS_SECTIONS, AnalysisReportConfig
from a_share_ai.analysis.validator import AnalysisReportSource
from a_share_ai.cli import main
from a_share_ai.market.replay import sha256_bytes

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "analysis" / "report"
SYMBOL = "600000.SH"
AS_OF = "2026-08-10T12:00:00+00:00"


def _write_json(path: Path, payload: Any) -> bytes:
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _build_config(tmp_path: Path, provider: Path) -> AnalysisReportConfig:
    root = tmp_path / "input-root"
    evidence_entries: list[dict[str, Any]] = []
    for name in ("market", "technical", "price_plan", "profitability", "growth", "announcements"):
        report_relative = f"evidence/{name}/report.json"
        artifact_relative = f"evidence/{name}/artifact.json"
        report_raw = _write_json(
            root / report_relative,
            {"name": name, "symbol": SYMBOL, "as_of": AS_OF, "status": "ready"},
        )
        artifact_raw = _write_json(root / artifact_relative, {"name": name, "kind": "fixture"})
        evidence_entries.append(
            {
                "name": name,
                "report_path": report_relative,
                "report_sha256": sha256_bytes(report_raw),
                "artifact_paths": [artifact_relative],
                "artifact_sha256": [sha256_bytes(artifact_raw)],
                "source": "fixture",
                "symbol": SYMBOL,
                "as_of": AS_OF,
                "status": "ready",
                "ready": True,
            }
        )
    bundle = {
        "schema_version": "1.0",
        "bundle_version": "analysis-input-v1",
        "source": "analysis-input-bundle",
        "symbol": SYMBOL,
        "as_of": AS_OF,
        "as_of_date": "2026-08-10",
        "evidence": evidence_entries,
        "summaries": {
            name: {"status": "ready"} for name in (entry["name"] for entry in evidence_entries)
        },
        "analysis_input_ready": True,
        "decision_ready": False,
    }
    bundle_path = root / "bundle" / "analysis_input_bundle.json"
    bundle_raw = _write_json(bundle_path, bundle)
    _write_json(
        bundle_path.with_name("analysis_input_report.json"),
        {
            "schema_version": "1.0",
            "bundle_version": "analysis-input-v1",
            "source": "analysis-input-bundle",
            "symbol": SYMBOL,
            "as_of": AS_OF,
            "bundle_sha256": sha256_bytes(bundle_raw),
            "status": "ready",
            "analysis_input_ready": True,
            "decision_ready": False,
            "issues": [],
        },
    )
    return AnalysisReportConfig(
        bundle_path=bundle_path,
        input_root=root,
        response_fixture=provider,
        output_dir=tmp_path / "output",
    )


def _run(tmp_path: Path, provider_name: str) -> tuple[AnalysisReportConfig, dict[str, Any]]:
    config = _build_config(tmp_path, FIXTURE_ROOT / provider_name)
    report = AnalysisReportSource(config).capture()
    return config, report


def test_valid_report_expands_citations_and_is_deterministic(tmp_path: Path) -> None:
    config, first = _run(tmp_path / "first", "valid_provider.json")
    second_config = AnalysisReportConfig(
        bundle_path=config.bundle_path,
        input_root=config.input_root,
        response_fixture=config.response_fixture,
        output_dir=tmp_path / "second" / "output",
    )
    second = AnalysisReportSource(second_config).capture()

    assert first["status"] == "ready"
    assert first["analysis_ready"] is True
    assert first["decision_ready"] is False
    assert first["output_sha256"] == second["output_sha256"]
    assert (
        (config.output_dir / "research_analysis.json").read_bytes()
        == (second_config.output_dir / "research_analysis.json").read_bytes()
    )
    analysis = json.loads(
        (config.output_dir / "research_analysis.json").read_text(encoding="utf-8")
    )
    assert set(analysis["sections"]) == set(ANALYSIS_SECTIONS)
    assert analysis["analysis_version"] == "analysis-report-v1"
    assert analysis["input_bundle_path"] == "bundle/analysis_input_bundle.json"
    technical = analysis["sections"]["technical"][0]
    assert technical["citation_ids"] == ["technical"]
    assert {item["evidence_id"] for item in analysis["citations"]} == {
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
    }
    assert analysis["citations"][0]["report_sha256"]
    assert first["output_sha256"] == sha256_bytes(
        (config.output_dir / "research_analysis.json").read_bytes()
    )


@pytest.mark.parametrize(
    ("provider_name", "issue_code"),
    [
        ("missing_citation/provider.json", "CITATION_MISSING"),
        ("unknown_evidence_id/provider.json", "CITATION_UNKNOWN"),
        ("future_claim/provider.json", "FUTURE_CLAIM"),
        ("symbol_mismatch/provider.json", "SYMBOL_MISMATCH"),
        ("prohibited_decision/provider.json", "PROHIBITED_DECISION"),
        ("malformed_provider_output.json", "SECTIONS_INVALID"),
    ],
)
def test_provider_contract_fail_closed(
    tmp_path: Path, provider_name: str, issue_code: str
) -> None:
    config, report = _run(tmp_path, provider_name)

    assert report["status"] == "invalid"
    assert report["analysis_ready"] is False
    assert report["decision_ready"] is False
    assert report["issues"][0]["code"] == issue_code
    invalid_analysis = json.loads(
        (config.output_dir / "research_analysis.json").read_text(encoding="utf-8")
    )
    assert invalid_analysis["analysis_ready"] is False


def test_bundle_hash_and_evidence_hash_fail_closed(tmp_path: Path) -> None:
    config = _build_config(tmp_path, FIXTURE_ROOT / "valid_provider.json")
    bundle_report_path = config.resolved_bundle_report()
    bundle_report = json.loads(bundle_report_path.read_text(encoding="utf-8"))
    bundle_report["bundle_sha256"] = "0" * 64
    _write_json(bundle_report_path, bundle_report)
    report = AnalysisReportSource(config).capture()
    assert report["issues"][0]["code"] == "BUNDLE_HASH_MISMATCH"

    config = _build_config(tmp_path / "artifact", FIXTURE_ROOT / "valid_provider.json")
    artifact_path = config.input_root / "evidence" / "market" / "artifact.json"
    artifact_path.write_text("tampered", encoding="utf-8")
    report = AnalysisReportSource(config).capture()
    assert report["issues"][0]["code"] == "EVIDENCE_HASH_MISMATCH"


def test_cli_builds_analysis_report_and_returns_ready_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _build_config(tmp_path, FIXTURE_ROOT / "valid_provider.json")
    exit_code = main(
        [
            "analyze-input",
            "--bundle",
            str(config.bundle_path),
            "--input-root",
            str(config.input_root),
            "--response-fixture",
                str(config.response_fixture),
            "--output-dir",
            str(config.output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["analysis_ready"] is True


def test_cli_openai_provider_is_explicit_and_uses_fake_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _build_config(tmp_path, FIXTURE_ROOT / "valid_provider.json")

    class FakeOpenAIProvider:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["model"] == "test-model"

        def load(self, *, bundle: dict[str, Any], entries: dict[str, Any]) -> dict[str, Any]:
            del bundle, entries
            return json.loads((FIXTURE_ROOT / "valid_provider.json").read_text(encoding="utf-8"))

        def audit_metadata(self) -> dict[str, Any]:
            return {
                "provider": "openai",
                "model": "test-model",
                "provider_status": "ready",
            }

        def write_audit_files(self, output_dir: Path) -> None:
            del output_dir

    monkeypatch.setattr(
        "a_share_ai.analysis.validator.OpenAIAnalysisProvider", FakeOpenAIProvider
    )
    exit_code = main(
        [
            "analyze-input",
            "--provider",
            "openai",
            "--model",
            "test-model",
            "--bundle",
            str(config.bundle_path),
            "--input-root",
            str(config.input_root),
            "--output-dir",
            str(config.output_dir),
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provider"] == "openai"
    assert report["model"] == "test-model"
