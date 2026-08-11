"""Build redacted, deterministic requests for supported analysis providers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..market.replay import sha256_bytes
from .contracts import (
    ALLOWED_CLAIM_KINDS,
    ANALYSIS_REPORT_SCHEMA_VERSION,
    ANALYSIS_REPORT_VERSION,
    ANALYSIS_SECTIONS,
    EVIDENCE_IDS,
)

REQUEST_SCHEMA_NAME = "a_share_analysis_report"

SYSTEM_PROMPT = """You are a read-only A-share research analyst.
Use only the structured evidence supplied in the user message. Do not add external
facts, search the web, infer missing announcement content, or invent dates.
Return only the requested JSON object. Every claim must cite one or more supplied
evidence IDs. Use kind=unknown when the supplied evidence is insufficient.
Do not produce BUY, SELL, HOLD, 买入, 卖出, 观望, entry prices, stop loss, take
profit, position sizing, orders, or any other executable trading instruction.
The local program will expand citation paths and hashes after validation.
"""

DEEPSEEK_SYSTEM_PROMPT = """You are a read-only A-share research analyst.
Use only the structured evidence supplied in the user message. Do not add external
facts, search the web, infer missing announcement content, or invent dates.
Return one valid JSON object and no Markdown. Every claim must cite one or more
supplied evidence IDs. Use kind=unknown when the supplied evidence is insufficient.
Do not produce BUY, SELL, HOLD, 涔板叆, 鍗栧嚭, 瑙傛湜, entry prices, stop loss, take
profit, position sizing, orders, or any other executable trading instruction.
The required JSON shape is shown in the system message example; keep all keys and
all eight section names exactly as shown. The local program will validate every
field and expand citation paths and hashes after validation. Every claim object
must use exactly these keys: claim_id, kind, text, citation_ids, observed_dates.
Do not use citations, description, summary, or any other claim keys.
"""


class RequestBuildError(ValueError):
    """Raised when a bundle cannot be reduced to a safe AI request."""


def _claim_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claim_id": {"type": "string"},
            "kind": {"type": "string", "enum": list(ALLOWED_CLAIM_KINDS)},
            "text": {"type": "string"},
            "citation_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(EVIDENCE_IDS)},
                "minItems": 1,
            },
            "observed_dates": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "claim_id",
            "kind",
            "text",
            "citation_ids",
            "observed_dates",
        ],
    }


def provider_output_schema() -> dict[str, Any]:
    """Return the strict schema accepted by the Responses API."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "enum": [ANALYSIS_REPORT_SCHEMA_VERSION]},
            "analysis_version": {"type": "string", "enum": [ANALYSIS_REPORT_VERSION]},
            "symbol": {"type": "string"},
            "as_of": {"type": "string"},
            "sections": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    section: {"type": "array", "items": _claim_schema()}
                    for section in ANALYSIS_SECTIONS
                },
                "required": list(ANALYSIS_SECTIONS),
            },
        },
        "required": ["schema_version", "analysis_version", "symbol", "as_of", "sections"],
    }


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize a request or audit payload deterministically without secrets."""

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def build_analysis_context(
    bundle: Mapping[str, Any], entries: Mapping[str, Any]
) -> dict[str, Any]:
    """Reduce a validated bundle to summaries and non-sensitive evidence metadata."""

    symbol = bundle.get("symbol")
    as_of = bundle.get("as_of")
    summaries = bundle.get("summaries")
    if not isinstance(symbol, str) or not symbol:
        raise RequestBuildError("bundle symbol is required")
    if not isinstance(as_of, str) or not as_of:
        raise RequestBuildError("bundle as_of is required")
    if not isinstance(summaries, dict) or set(summaries) != set(EVIDENCE_IDS):
        raise RequestBuildError("bundle summaries must contain the fixed evidence IDs")

    evidence: list[dict[str, Any]] = []
    for evidence_id in EVIDENCE_IDS:
        entry = entries.get(evidence_id)
        if not isinstance(entry, Mapping):
            raise RequestBuildError(f"bundle evidence entry is missing: {evidence_id}")
        summary = summaries[evidence_id]
        if not isinstance(summary, Mapping):
            raise RequestBuildError(f"bundle summary is not an object: {evidence_id}")
        evidence.append(
            {
                "evidence_id": evidence_id,
                "source": entry.get("source"),
                "status": entry.get("status"),
                "summary": dict(summary),
            }
        )
    return {"symbol": symbol, "as_of": as_of, "evidence": evidence}


def build_openai_request(
    bundle: Mapping[str, Any], entries: Mapping[str, Any], *, model: str
) -> dict[str, Any]:
    """Build a Responses API request that contains no paths, hashes, or credentials."""

    context = build_analysis_context(bundle, entries)
    return {
        "model": model,
        "store": False,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(context, ensure_ascii=False, sort_keys=True),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": REQUEST_SCHEMA_NAME,
                "strict": True,
                "schema": provider_output_schema(),
            }
        },
    }


def build_deepseek_request(
    bundle: Mapping[str, Any], entries: Mapping[str, Any], *, model: str
) -> dict[str, Any]:
    """Build a DeepSeek Chat Completions JSON-mode request without local metadata."""

    context = build_analysis_context(bundle, entries)
    example = {
        "schema_version": ANALYSIS_REPORT_SCHEMA_VERSION,
        "analysis_version": ANALYSIS_REPORT_VERSION,
        "symbol": context["symbol"],
        "as_of": context["as_of"],
        "sections": {section: [] for section in ANALYSIS_SECTIONS},
    }
    example["sections"]["unknowns"] = [
        {
            "claim_id": "unknown_evidence",
            "kind": "unknown",
            "text": "The supplied evidence is insufficient for this claim.",
            "citation_ids": ["market"],
            "observed_dates": [],
        }
    ]
    system_prompt = (
        f"{DEEPSEEK_SYSTEM_PROMPT}\nJSON example:\n"
        f"{json.dumps(example, ensure_ascii=False, sort_keys=True)}"
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False, sort_keys=True),
            },
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }


def request_sha256(request: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(request))
