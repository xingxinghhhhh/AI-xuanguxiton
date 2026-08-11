import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from a_share_ai.analysis.offline_provider import ProviderError
from a_share_ai.analysis.real_provider import (
    OpenAIAnalysisProvider,
    ProviderTransportError,
    UrllibResponseTransport,
)

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
    entries = {
        name: {"source": "fixture", "status": "ready"} for name in EVIDENCE_IDS
    }
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


def test_provider_uses_header_for_key_and_writes_redacted_audit(tmp_path: Path) -> None:
    secret = "test-secret-key"
    response = {"output_text": json.dumps(_provider_payload(), ensure_ascii=False)}
    transport = FakeTransport(json.dumps(response).encode("utf-8"))
    provider = OpenAIAnalysisProvider(
        model="test-model",
        api_key=secret,
        transport=transport,
    )

    payload = provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])
    provider.write_audit_files(tmp_path)

    assert payload["analysis_version"] == "analysis-report-v1"
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {secret}"
    assert secret.encode("utf-8") not in transport.calls[0]["body"]
    assert secret not in (tmp_path / "ai_request.json").read_text(encoding="utf-8")
    assert secret not in (tmp_path / "ai_response.json").read_text(encoding="utf-8")
    assert provider.audit_metadata()["provider_status"] == "ready"
    assert provider.audit_metadata()["request_sha256"]
    assert provider.audit_metadata()["response_sha256"]


def test_invalid_response_does_not_write_raw_body_or_key(tmp_path: Path) -> None:
    secret = "test-secret-key"
    transport = FakeTransport(f"not-json {secret}".encode())
    provider = OpenAIAnalysisProvider(model="test-model", api_key=secret, transport=transport)

    with pytest.raises(ProviderError, match="not valid JSON"):
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])
    provider.write_audit_files(tmp_path)

    response_audit = json.loads((tmp_path / "ai_response.json").read_text(encoding="utf-8"))
    assert response_audit == {"status": "invalid_json"}
    assert secret not in (tmp_path / "ai_response.json").read_text(encoding="utf-8")


def test_transport_error_is_fail_closed_and_sanitized(tmp_path: Path) -> None:
    transport = FakeTransport(ProviderTransportError("HTTP 429"))
    provider = OpenAIAnalysisProvider(
        model="test-model", api_key="test-secret", transport=transport
    )

    with pytest.raises(ProviderError, match="HTTP 429"):
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])
    provider.write_audit_files(tmp_path)

    metadata = provider.audit_metadata()
    assert metadata["provider_status"] == "error"
    assert metadata["provider_error"] == "HTTP 429"
    assert json.loads((tmp_path / "ai_response.json").read_text(encoding="utf-8")) == {
        "status": "not_received"
    }


def test_transport_error_cannot_echo_api_key(tmp_path: Path) -> None:
    secret = "test-secret-key"
    transport = FakeTransport(ProviderTransportError(f"upstream detail: {secret}"))
    provider = OpenAIAnalysisProvider(model="test-model", api_key=secret, transport=transport)

    with pytest.raises(ProviderError, match="<redacted>") as error:
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])
    provider.write_audit_files(tmp_path)

    assert secret not in str(error.value)
    assert secret not in json.dumps(provider.audit_metadata(), ensure_ascii=False)
    assert secret not in (tmp_path / "ai_response.json").read_text(encoding="utf-8")


def test_missing_key_never_calls_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    transport = FakeTransport(b"unused")
    provider = OpenAIAnalysisProvider(
        model="test-model",
        api_key="",
        env_file=tmp_path / "missing.env",
        transport=transport,
    )

    with pytest.raises(ProviderError, match="OPENAI_API_KEY is not configured"):
        provider.load(bundle=_bundle_and_entries()[0], entries=_bundle_and_entries()[1])

    assert transport.calls == []


def test_http_error_detail_keeps_only_sanitized_api_diagnostics() -> None:
    error = urllib.error.HTTPError(
        url="https://api.openai.com/v1/responses",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=io.BytesIO(
            b'{"error":{"code":"credit_balance_exhausted",'
            b'"message":"no credits; Bearer sk-secret"}}'
        ),
    )

    detail = UrllibResponseTransport._http_error_detail(error)

    assert "credit_balance_exhausted" in detail
    assert "sk-secret" not in detail
    assert "Bearer <redacted>" in detail
