# Phase 12: DeepSeek single-stock research provider MVP

## Scope

This node adds an explicitly selected DeepSeek Chat Completions provider for an
already-ready `analysis-input-v1` bundle. It uses JSON mode, then sends the
returned object through the existing `analysis-report-v1` validator. OpenAI,
offline fixtures, and `decision_ready=false` semantics remain unchanged.

The node does not add automatic provider switching, retries, offline fallback,
multi-stock batching, web search, PDF extraction, trade decisions, or orders.

## Implementation

- `request_builder.py` builds a deterministic DeepSeek `messages` request with
  `response_format: {"type": "json_object"}` and no Responses API `json_schema`
  fields.
- `deepseek_provider.py` calls `POST https://api.deepseek.com/chat/completions`,
  reads `DEEPSEEK_API_KEY` only into memory, parses `choices[0].message.content`,
  and rejects empty, truncated, non-stop, or invalid responses.
- `validator.py` and `cli.py` add the explicit `--provider deepseek` branch;
  the default model is `deepseek-v4-flash`.
- Request/response audit hashes and provider errors are recorded without the
  API key. The local validator remains authoritative for citations, dates,
  prohibited decisions, and the final readiness flags.

## Verification

Fake transport tests cover JSON mode request shape, no local paths or key in the
request, code-fenced JSON, empty/invalid/truncated responses, missing choices,
missing key, transport error redaction, and HTTP error redaction. A separate
single-stock real smoke test is required before marking the live path ready.
