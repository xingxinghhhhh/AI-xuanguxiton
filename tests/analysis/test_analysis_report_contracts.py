from pathlib import Path

from a_share_ai.analysis.contracts import (
    ANALYSIS_REPORT_VERSION,
    ANALYSIS_SECTIONS,
    EVIDENCE_IDS,
    AnalysisReportConfig,
)


def test_analysis_report_contract_is_versioned_and_resolves_sibling_report(tmp_path: Path) -> None:
    bundle = tmp_path / "analysis_input_bundle.json"
    config = AnalysisReportConfig(
        bundle_path=bundle,
        input_root=tmp_path,
        response_fixture=tmp_path / "provider.json",
        output_dir=tmp_path / "output",
    )

    assert ANALYSIS_REPORT_VERSION == "analysis-report-v1"
    assert ANALYSIS_SECTIONS == (
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
        "risks",
        "unknowns",
    )
    assert EVIDENCE_IDS == (
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
    )
    assert config.resolved_bundle_report() == tmp_path / "analysis_input_report.json"
