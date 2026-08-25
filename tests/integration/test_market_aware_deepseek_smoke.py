import json
from pathlib import Path
from typing import Any

from a_share_ai.analysis.deepseek_provider import DeepSeekAnalysisProvider
from a_share_ai.analysis.market_aware_smoke import smoke_market_aware_analysis
from a_share_ai.analysis.validator import AnalysisReportSource
from a_share_ai.evidence.analysis_input_bundle import AnalysisInputSource
from tests.evidence.test_analysis_input_bundle import add_market_context, build_artifacts

FIXTURE_ROOT = Path("fixtures/analysis/report")


class FakeTransport:
    def __init__(self, response: bytes | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, *, url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-smoke",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "index": 0,
                    "message": {"content": json.dumps(payload), "role": "assistant"},
                }
            ],
        }
    ).encode("utf-8")


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    config = add_market_context(build_artifacts(tmp_path / "input"))
    bundle_dir = config.input_root / "analysis_input"
    assert AnalysisInputSource(config).capture(bundle_dir)["status"] == "ready"
    return (
        bundle_dir / "analysis_input_bundle.json",
        bundle_dir / "analysis_input_report.json",
        config.input_root,
    )


def test_market_aware_smoke_sends_one_structured_request_and_audits_chain(
    tmp_path: Path, monkeypatch
) -> None:
    bundle_path, bundle_report_path, input_root = _inputs(tmp_path)
    payload = json.loads((FIXTURE_ROOT / "valid_provider.json").read_text(encoding="utf-8"))
    transport = FakeTransport(_response(payload))

    def fake_provider(self: AnalysisReportSource) -> DeepSeekAnalysisProvider:
        return DeepSeekAnalysisProvider(
            model=self.config.model,
            api_key="test-smoke-secret",
            transport=transport,
        )

    monkeypatch.setattr(AnalysisReportSource, "_provider", fake_provider)
    report = smoke_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        env_file=tmp_path / ".env.local",
        output_dir=input_root / "smoke",
    )

    request = json.loads(transport.calls[0]["body"].decode("utf-8"))
    serialized = json.dumps(request, ensure_ascii=False, sort_keys=True)
    assert len(transport.calls) == 1
    assert report["status"] == "ready"
    assert report["provider_status"] == "ready"
    assert report["review_packet_ready"] is True
    assert report["review_complete"] is False
    assert report["decision_ready"] is False
    assert "market-context-summary-v1" in serialized
    assert "relative-strength-v1" in serialized
    assert "report_path" not in serialized
    assert "raw_response" not in serialized
    assert "test-smoke-secret" not in serialized


def test_market_aware_smoke_fails_once_and_redacts_provider_error(
    tmp_path: Path, monkeypatch
) -> None:
    bundle_path, bundle_report_path, input_root = _inputs(tmp_path)
    transport = FakeTransport(RuntimeError("Bearer test-smoke-secret upstream timeout"))

    def fake_provider(self: AnalysisReportSource) -> DeepSeekAnalysisProvider:
        return DeepSeekAnalysisProvider(
            model=self.config.model,
            api_key="test-smoke-secret",
            transport=transport,
        )

    monkeypatch.setattr(AnalysisReportSource, "_provider", fake_provider)
    report = smoke_market_aware_analysis(
        bundle_path=bundle_path,
        bundle_report_path=bundle_report_path,
        input_root=input_root,
        env_file=tmp_path / ".env.local",
        output_dir=input_root / "smoke",
    )

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert len(transport.calls) == 1
    assert report["status"] == "invalid"
    assert report["provider_status"] == "error"
    assert report["review_packet_ready"] is False
    assert report["decision_ready"] is False
    assert "test-smoke-secret" not in serialized
    assert report["stages"]["render"]["status"] == "skipped"
