# Phase 27: Market-aware DeepSeek smoke MVP

This node adds a controlled one-request DeepSeek smoke entry point around the
existing market-aware replay chain.

## Contract and safety

- Version: `market-aware-smoke-v1`.
- CLI: `python -m a_share_ai.cli market-aware-smoke`.
- Provider: explicitly selected `deepseek`, default model
  `deepseek-v4-flash`.
- Execution: one stock, one cutoff, one request, no retry, fallback, or
  provider switching.
- Input: an existing `analysis-input-v2` bundle containing
  `market-context-summary-v1` and `relative-strength-v1`.

The command records provider status, request/response SHA-256 values, the
redacted provider error, and the downstream stage statuses in
`market_aware_smoke_report.json`. It never records the API key or `.env.local`
contents. After a successful provider response it runs only the offline
analysis, render, quality, decision-input, safety, and pending review stages.
It never applies a review, creates a research release, or changes
`decision_ready=false`.

## Validation

Fake-transport tests verify that the request contains the structured market and
relative-strength summaries but no local paths, raw responses, hashes, or
credentials. Failure tests verify one request only, error redaction, and
fail-closed stopping before later stages.

The controlled real smoke on 2026-08-11 reached DeepSeek successfully
(`provider_status=ready`) and recorded request/response hashes, but the returned
structured output used `kind="fact"`, which is outside the current
`analysis-report-v1` allowed claim kinds. Local validation therefore returned
`analysis_ready=false` and stopped all downstream stages. No retry or fallback
was performed, and no review result or release was created.
