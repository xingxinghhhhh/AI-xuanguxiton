from pathlib import Path

import pytest

from a_share_ai.analysis.offline_provider import OfflineAnalysisProvider, ProviderError

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "analysis" / "report"


def test_offline_provider_loads_structured_fixture() -> None:
    payload = OfflineAnalysisProvider(FIXTURE_ROOT / "valid_provider.json").load()

    assert payload["analysis_version"] == "analysis-report-v1"
    assert set(payload["sections"]) == {
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
        "risks",
        "unknowns",
    }


def test_offline_provider_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "provider.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ProviderError):
        OfflineAnalysisProvider(path).load()
