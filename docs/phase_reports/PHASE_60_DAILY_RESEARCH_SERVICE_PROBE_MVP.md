# Phase 60: daily research service deployment probe MVP

## Goal

Provide a dedicated loopback-only deployment probe for the Node59 service. It
must verify the legacy health, readiness, and receipt routes together with the
daily admission route, so deployment checks cannot silently omit the current
research admission gate.

## Command and exit codes

```bash
python -m a_share_ai.cli probe-daily-research-service \
  --base-url http://127.0.0.1:8765 \
  --timeout-seconds 3
```

- Exit `0`: all four routes are valid, the service is ready, daily admission is
  ready, and `decision_ready=false`.
- Exit `1`: the service is unreachable, invalid, or valid but stale/blocked or
  not ready.
- Exit `2`: the URL or timeout argument is invalid.

The probe only accepts `127.0.0.1` or `::1`, HTTP(S) URLs without credentials,
paths, queries, or fragments. It disables environment proxies, never follows
redirects, never retries, and never writes files.

## Output contract

The command writes deterministic JSON with exactly these fields:

- `probe_version`, `status` (`ready`, `blocked`, or `invalid`);
- `health_status`, `ready_status`, `receipt_status`, and
  `daily_admission_status`;
- `daily_admission_ready`, `decision_ready`, and ordered `issues`.

The probe requires exact legacy summary fields and the fixed daily admission
whitelist, rejects unknown fields, path-like sensitive output, invalid JSON,
redirects, version mismatches, readiness mismatches, and any
`decision_ready=true` response.

## Scope and rollback

Node60 adds only the dedicated probe module, service tests, this operational
documentation, README guidance, service exports, and the CLI branch. It does
not modify Node53, Node54, Node55, Node58, or Node59 contracts, artifacts, or
routes. Rollback is deletion of these Node60 files and the dedicated CLI
branch.
