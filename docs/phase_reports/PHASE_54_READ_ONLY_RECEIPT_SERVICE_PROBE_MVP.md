# Phase 54 — Read-only receipt service deployment probe MVP

## Goal

Provide a platform-independent deployment and process-manager probe for the
Node53 local receipt service. The probe checks liveness, business readiness,
and the read-only safety contract using three GET requests.

## Command and exit codes

```bash
python -m a_share_ai.cli probe-research-receipt-service \
  --base-url http://127.0.0.1:8765 \
  --timeout-seconds 3
```

- Exit `0`: all three endpoints are valid, `/readyz` is HTTP 200,
  `receipt_ready=true`, and `decision_ready=false`.
- Exit `1`: the service is unreachable, returns an invalid response, or is
  valid but stale/blocked/not ready.
- Exit `2`: the URL or timeout argument is invalid.

The probe only accepts `127.0.0.1` or `::1`, HTTP(S) URLs without credentials,
paths, queries, or fragments. It disables environment proxies, never follows
redirects, never retries or switches addresses, and never writes files.

## Output contract

The command writes deterministic JSON containing:

- `probe_version`, `base_url`, `status` (`ready`, `blocked`, or `invalid`);
- HTTP statuses for `healthz`, `readyz`, and `v1/research/receipt`;
- `receipt_ready`, `decision_ready`, ordered endpoint `checks`, and `issues`.

The probe requires the exact Node53 response field set and service version,
rejects unknown fields, path-like sensitive output, redirects, invalid JSON,
and any `decision_ready=true` response. A stale or blocked service remains
visible in the issue message but cannot produce exit `0`.

## Scope and rollback

Node54 adds only the probe module, service tests, operational documentation,
and the CLI branch. It does not change Node52/53 code, artifacts, response
fields, runtime dependencies, network access, credentials, databases,
schedulers, UI, or trading behavior. Rollback is deletion of these Node54
files and the probe CLI branch.
