import json

from a_share_ai.analysis.request_builder import (
    build_analysis_context,
    build_deepseek_request,
    build_openai_request,
    provider_output_schema,
    request_sha256,
)

EVIDENCE_IDS = (
    "market",
    "technical",
    "price_plan",
    "profitability",
    "growth",
    "announcements",
)


def _bundle_and_entries() -> tuple[dict, dict]:
    bundle = {
        "symbol": "600000.SH",
        "as_of": "2026-08-10T12:00:00+00:00",
        "summaries": {name: {"status": "ready", "value": name} for name in EVIDENCE_IDS},
    }
    entries = {
        name: {
            "source": "fixture",
            "status": "ready",
            "report_path": f"C:/private/{name}/report.json",
            "artifact_paths": [f"C:/private/{name}/artifact.json"],
        }
        for name in EVIDENCE_IDS
    }
    return bundle, entries


def test_context_contains_summaries_but_no_local_paths() -> None:
    bundle, entries = _bundle_and_entries()

    context = build_analysis_context(bundle, entries)
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)

    assert set(item["evidence_id"] for item in context["evidence"]) == set(EVIDENCE_IDS)
    assert "report_path" not in serialized
    assert "C:/private" not in serialized


def test_openai_request_is_structured_and_hash_is_stable() -> None:
    bundle, entries = _bundle_and_entries()

    first = build_openai_request(bundle, entries, model="test-model")
    second = build_openai_request(bundle, entries, model="test-model")

    assert first == second
    assert request_sha256(first) == request_sha256(second)
    assert first["model"] == "test-model"
    assert first["text"]["format"]["type"] == "json_schema"
    assert first["text"]["format"]["strict"] is True
    assert set(provider_output_schema()["properties"]["sections"]["properties"]) == {
        "market",
        "technical",
        "price_plan",
        "profitability",
        "growth",
        "announcements",
        "risks",
        "unknowns",
    }


def test_deepseek_request_uses_json_mode_without_responses_schema() -> None:
    bundle, entries = _bundle_and_entries()

    request = build_deepseek_request(bundle, entries, model="deepseek-v4-flash")
    serialized = json.dumps(request, ensure_ascii=False)

    assert request["model"] == "deepseek-v4-flash"
    assert request["response_format"] == {"type": "json_object"}
    assert request["stream"] is False
    assert "json_schema" not in serialized
    assert "report_path" not in serialized
    assert "C:/private" not in serialized
    assert "JSON example" in request["messages"][0]["content"]
    assert "claim_id" in request["messages"][0]["content"]
    assert "citation_ids" in request["messages"][0]["content"]
    assert '"description":' not in request["messages"][0]["content"]
