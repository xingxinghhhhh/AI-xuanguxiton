# Phase 53 — Read-only research receipt service MVP

## Goal

Expose the validated Node52 final receipt through a small local HTTP service so
an operator can probe liveness, readiness, and the current evidence-chain
summary before a later deployment node adds production infrastructure.

## Contract

The service reads exactly these two files:

- `market_aware_session_history_final_receipt.json`
- `market_aware_session_history_final_receipt_report.json`

Both must be inside `--artifact-root`. Startup validates UTF-8 JSON, the
Node52 version, canonical `output_sha256`, the report's `receipt_sha256`,
relative input paths, cross-file status/readiness metadata, and
`decision_ready=false`. It does not read or rebuild any Node37–52 upstream
artifact.

The command is:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --receipt <artifact-root>/final-receipt/market_aware_session_history_final_receipt.json \
  --receipt-report <artifact-root>/final-receipt/market_aware_session_history_final_receipt_report.json \
  --artifact-root <artifact-root> \
  --host 127.0.0.1 \
  --port 8765
```

Only loopback hosts are accepted. The service has no write path, external
network call, API-key access, authentication, database, scheduler, UI, or
trade operation.

## HTTP behavior

- `GET /healthz` returns HTTP 200 for a validated loaded receipt.
- `GET /readyz` returns HTTP 200 only when `receipt_ready=true`; valid
  `stale`/`blocked` receipts return HTTP 503.
- `GET /v1/research/receipt` returns a fixed summary containing service
  version, status, symbol, package count, time bounds, receipt readiness,
  issues, and `decision_ready=false`.
- Unknown paths return 404; non-GET methods return 405.
- Startup validation failures return a sanitized non-zero CLI result and do
  not bind a port.

## Verification

The service tests cover ready and stale behavior, SHA/self-hash tampering,
artifact-root escapes, public-host rejection, path redaction, HTTP 404/405,
and launching through the CLI followed by a local HTTP probe. The existing
offline evidence chain remains unchanged and all outputs preserve
`decision_ready=false`.

## Rollback

Rollback is limited to removing the Node53 service module, service tests,
phase report, README section, and CLI branch. No Node52 artifact, database
schema, or deployment state is changed.
