import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.deepseek_provider import (
    DeepSeekAnalysisProvider,
    DeepSeekTransportError,
    UrllibDeepSeekTransport,
)
from a_share_ai.analysis.offline_provider import ProviderError

EVIDENCE_IDS = (
    "market",
    "technical",
    "price_plan",
    "profitability",
    "growth",
    "announcements",
)


def _bundle_and_entries() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    bundle = {
        "symbol": "600000.SH",
        "as_of": "2026-08-10T12:00:00+00:00",
        "summaries": {name: {"status": "ready"} for name in EVIDENCE_IDS},
    }
    entries = {name: {"source": "fixture", "status": "ready"} for name in EVIDENCE_IDS}
    return bundle, entries


def _provider_payload() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "analysis_version": "analysis-report-v1",
        "symbol": "600000.SH",
        "as_of": "2026-08-10T12:00:00+00:00",
        "sections": {
            section: []
            for section in (
                "market",
                "technical",
                "price_plan",
                "profitability",
                "growth",
                "announcements",
                "risks",
                "unknowns",
            )
        },
    }


class FakeTransport:
    def __init__(self, response: bytes | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, *, url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(content: str, *, finish_reason: str = "stop") -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "index": 0,
                    "message": {"content": content, "role": "assistant"},
                }
            ],
        }
    ).encode("utf-8")


def test_provider_uses_chat_completions_json_mode_and_audits_without_key(
    tmp_path: Path,
) -> None:
    secret = "test-deepseek-key"
    transport = FakeTransport(
        _response(json.dumps(_provider_payload(), ensure_ascii=False))
    )
    provider = DeepSeekAnalysisProvider(
        model="deepseek-v4-flash", api_key=secret, transport=transport
    )

    payload = provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])
    provider.write_audit_files(tmp_path)

    request = json.loads(transport.calls[0]["body"].decode("utf-8"))
    assert payload["analysis_version"] == "analysis-report-v1"
    assert transport.calls[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {secret}"
    assert request["response_format"] == {"type": "json_object"}
    assert request["stream"] is False
    assert "text" not in request
    assert "json_schema" not in json.dumps(request)
    assert secret.encode("utf-8") not in transport.calls[0]["body"]
    assert secret not in (tmp_path / "ai_request.json").read_text(encoding="utf-8")
    assert secret not in (tmp_path / "ai_response.json").read_text(encoding="utf-8")
    assert provider.audit_metadata()["provider"] == "deepseek"


def test_provider_accepts_a_complete_json_code_fence() -> None:
    transport = FakeTransport(_response(f"```json\n{json.dumps(_provider_payload())}\n```"))
    provider = DeepSeekAnalysisProvider(model="test-model", api_key="test-key", transport=transport)

    payload = provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])

    assert payload["symbol"] == "600000.SH"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response("", finish_reason="stop"), "content is empty"),
        (_response("not-json", finish_reason="stop"), "not valid JSON"),
        (_response("{}", finish_reason="length"), "finish_reason is not stop"),
    ],
)
def test_provider_rejects_unusable_or_truncated_responses(
    response: bytes, message: str
) -> None:
    provider = DeepSeekAnalysisProvider(
        model="test-model", api_key="test-key", transport=FakeTransport(response)
    )

    with pytest.raises(ProviderError, match=message):
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])


def test_provider_rejects_missing_choices() -> None:
    provider = DeepSeekAnalysisProvider(
        model="test-model",
        api_key="test-key",
        transport=FakeTransport(b'{"id":"test"}'),
    )

    with pytest.raises(ProviderError, match="did not contain choices"):
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])


def test_missing_key_never_calls_transport(tmp_path: Path) -> None:
    transport = FakeTransport(b"unused")
    provider = DeepSeekAnalysisProvider(
        model="test-model", api_key="", env_file=tmp_path / "missing.env", transport=transport
    )

    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY is not configured"):
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])

    assert transport.calls == []


def test_transport_error_cannot_echo_api_key(tmp_path: Path) -> None:
    secret = "test-deepseek-key"
    transport = FakeTransport(DeepSeekTransportError(f"upstream detail: {secret}"))
    provider = DeepSeekAnalysisProvider(model="test-model", api_key=secret, transport=transport)

    with pytest.raises(ProviderError, match="<redacted>") as error:
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])
    provider.write_audit_files(tmp_path)

    assert secret not in str(error.value)
    assert secret not in json.dumps(provider.audit_metadata(), ensure_ascii=False)


def test_http_error_detail_is_sanitized() -> None:
    error = urllib.error.HTTPError(
        url="https://api.deepseek.com/chat/completions",
        code=402,
        msg="Payment Required",
        hdrs=None,
        fp=io.BytesIO(
            b'{"error":{"code":"insufficient_balance",'
            b'"message":"Bearer sk-secret"}}'
        ),
    )

    detail = UrllibDeepSeekTransport._http_error_detail(error)

    assert "insufficient_balance" in detail
    assert "sk-secret" not in detail
    assert "Bearer <redacted>" in detail
