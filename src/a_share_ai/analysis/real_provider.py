"""Single-call OpenAI Responses API provider with redacted audit artifacts."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..market.replay import sha256_bytes, write_atomic
from .offline_provider import ProviderError
from .request_builder import (
    RequestBuildError,
    build_openai_request,
    canonical_json_bytes,
    request_sha256,
)

DEFAULT_RESPONSES_URL = "https://api.openai.com/v1/responses"


class ResponseTransport(Protocol):
    """Minimal transport seam used by the real provider and offline tests."""

    def post(
        self, *, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> bytes:
        """Send one request and return the response body bytes."""


class ProviderTransportError(ProviderError):
    """A sanitized transport failure that never contains response bodies or keys."""


class UrllibResponseTransport:
    """Small stdlib transport so the base installation needs no network SDK."""

    @staticmethod
    def _http_error_detail(exc: urllib.error.HTTPError) -> str:
        """Return a short, secret-free API error detail for local diagnostics."""
        detail = f"OpenAI HTTP status {exc.code}"
        try:
            raw = exc.read()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return detail
        if not isinstance(payload, Mapping):
            return detail
        error = payload.get("error")
        if not isinstance(error, Mapping):
            return detail
        code = error.get("code")
        message = error.get("message")
        parts = [str(value) for value in (code, message) if isinstance(value, str) and value]
        if not parts:
            return detail
        # The API error should not contain credentials, but keep this boundary
        # defensive because the detail is written into an audit report.
        safe = ": ".join(parts)
        safe = re.sub(r"Bearer\s+\S+", "Bearer <redacted>", safe, flags=re.IGNORECASE)
        safe = re.sub(r"\bsk-[A-Za-z0-9_-]+", "<redacted>", safe)
        return f"{detail}: {safe[:500]}"

    def post(
        self, *, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> bytes:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise ProviderTransportError(f"OpenAI HTTP status {status}")
                return response.read()
        except urllib.error.HTTPError as exc:
            raise ProviderTransportError(self._http_error_detail(exc)) from exc
        except urllib.error.URLError as exc:
            raise ProviderTransportError(f"OpenAI network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderTransportError("OpenAI request timed out") from exc


def _read_api_key_from_env_file(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "OPENAI_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or None
    return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class OpenAIAnalysisProvider:
    """Call the OpenAI Responses API once and return the strict provider JSON."""

    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float = 60.0,
        env_file: Path | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: ResponseTransport | None = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.env_file = env_file or Path.cwd() / ".env.local"
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY") or _read_api_key_from_env_file(
            self.env_file
        )
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_RESPONSES_URL
        self._transport = transport or UrllibResponseTransport()
        self._request_payload: dict[str, Any] | None = None
        self._response_payload: dict[str, Any] | None = None
        self._response_raw: bytes | None = None
        self._request_sha256: str | None = None
        self._response_sha256: str | None = None
        self._requested_at: str | None = None
        self._received_at: str | None = None
        self._status = "not_started"
        self._error: str | None = None

    def load(
        self,
        *,
        bundle: Mapping[str, Any] | None = None,
        entries: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if bundle is None or entries is None:
            raise ProviderError("OpenAI provider requires a validated bundle")
        self._requested_at = _utc_now()
        self._status = "not_sent"
        if not self._api_key:
            self._error = "OPENAI_API_KEY is not configured"
            raise ProviderError(self._error)
        try:
            self._request_payload = build_openai_request(bundle, entries, model=self.model)
            request_bytes = canonical_json_bytes(self._request_payload)
            self._request_sha256 = request_sha256(self._request_payload)
            if self._api_key.encode("utf-8") in request_bytes:
                raise ProviderError("request payload unexpectedly contains the API key")
            self._status = "request_ready"
            response_raw = self._transport.post(
                url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                body=request_bytes,
                timeout=self.timeout_seconds,
            )
            self._response_raw = response_raw
            self._response_sha256 = sha256_bytes(response_raw)
            try:
                response_payload = json.loads(response_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._response_payload = {"status": "invalid_json"}
                self._response_raw = None
                raise ProviderError("OpenAI response was not valid JSON") from exc
            if not isinstance(response_payload, dict):
                self._response_payload = {"status": "invalid_root"}
                self._response_raw = None
                raise ProviderError("OpenAI response root must be a JSON object")
            self._response_payload = response_payload
            if self._api_key.encode("utf-8") in response_raw:
                self._response_payload = {"status": "response_redacted"}
                self._response_raw = None
                raise ProviderError("OpenAI response unexpectedly contained the API key")
            output_text = self._extract_output_text(response_payload)
            try:
                provider_payload = json.loads(output_text)
            except json.JSONDecodeError as exc:
                raise ProviderError("OpenAI structured output was not valid JSON") from exc
            if not isinstance(provider_payload, dict):
                raise ProviderError("OpenAI structured output root must be a JSON object")
            self._status = "ready"
            self._received_at = _utc_now()
            return provider_payload
        except (ProviderError, RequestBuildError) as exc:
            self._status = "error"
            self._error = str(exc)
            self._received_at = _utc_now()
            raise ProviderError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - final provider safety gate
            self._status = "error"
            self._error = f"OpenAI provider error: {exc}"
            self._received_at = _utc_now()
            raise ProviderError(self._error) from exc

    @staticmethod
    def _extract_output_text(response: Mapping[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for content_item in content:
                    if not isinstance(content_item, Mapping):
                        continue
                    if content_item.get("type") == "refusal":
                        raise ProviderError("OpenAI model refused the structured analysis request")
                    if content_item.get("type") in {"output_text", "text"}:
                        text = content_item.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
        raise ProviderError("OpenAI response did not contain structured output text")

    def audit_metadata(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.name,
            "provider_error": self._error,
            "provider_status": self._status,
            "request_sha256": self._request_sha256,
            "requested_at": self._requested_at,
            "response_sha256": self._response_sha256,
            "received_at": self._received_at,
        }

    def write_audit_files(self, output_dir: Path) -> None:
        request_payload: Mapping[str, Any] = self._request_payload or {"status": "not_sent"}
        response_payload: Mapping[str, Any] = self._response_payload or {
            "status": "not_received"
        }
        write_atomic(output_dir / "ai_request.json", canonical_json_bytes(request_payload))
        if self._response_raw is not None:
            write_atomic(output_dir / "ai_response.json", self._response_raw)
        else:
            write_atomic(output_dir / "ai_response.json", canonical_json_bytes(response_payload))
