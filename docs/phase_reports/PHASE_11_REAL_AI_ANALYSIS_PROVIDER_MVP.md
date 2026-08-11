# Phase 11: OpenAI single-stock research provider MVP

## Scope

This node adds one explicitly selected, single-request OpenAI Responses API
provider for an already-ready `analysis-input-v1` bundle. It sends only the
bundle's structured summaries and fixed evidence IDs, validates the returned
`analysis-report-v1` provider payload locally, and keeps `decision_ready=false`.

It does not add real-time quotes, web search, PDF extraction, database storage,
multi-stock batching, retries, provider fallback, trade decisions, or orders.

## Implementation

- `request_builder.py` constructs a deterministic, redacted request and strict
  structured-output schema for the eight analysis sections.
- `real_provider.py` uses a small stdlib Responses API transport, reads
  `OPENAI_API_KEY` from the process environment or local `.env.local`, and
  keeps the key only in the request header.
- `validator.py` selects `offline` by default or `openai` only when explicitly
  requested, then reuses the existing fail-closed validator.
- Real-provider runs write `ai_request.json`, `ai_response.json`,
  `research_analysis.json`, and `research_analysis_report.json`, including
  provider/model/timestamps and request/response SHA-256 values without the key.

## Verification

The ordinary test path uses fake transport and never calls OpenAI. Coverage
includes deterministic request construction, no local paths in the request,
key placement only in the authorization header, missing key, invalid JSON,
transport errors, redacted audit artifacts, and an explicit CLI provider branch.

The real smoke test remains a separate, manually authorized single-stock call
and must use an already-ready input bundle.
