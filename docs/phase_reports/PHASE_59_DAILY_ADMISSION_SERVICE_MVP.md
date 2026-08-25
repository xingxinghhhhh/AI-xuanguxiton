# Phase 59: read-only daily admission service MVP

## Goal

Node59 optionally exposes Node58's freshness admission through the existing
loopback-only Node53 receipt service. This makes the current daily research
consumption gate observable without adding a scheduler, database, credentials,
network provider, trading path, or UI.

## Optional service inputs

The `serve-research-receipt` command accepts these as a complete set:

```text
--daily-admission
--daily-admission-report
--daily-admission-root
```

At startup the service validates both Node58 JSON files, their self-hashes,
version, status, safety gates, normalized paths, referenced artifact hashes,
and safe issue messages. The old receipt inputs remain independently validated.

## Routes and readiness

`GET /v1/research/daily-admission` returns only the service version, admission
version, symbol, timestamps, status, freshness status, admission gate, issues,
and `decision_ready`. It does not expose artifact paths or filesystem details.

`/healthz` remains HTTP 200 when the receipt and optional admission inputs are
valid. When daily admission is configured, the three legacy summary routes
(`/healthz`, `/readyz`, and `/v1/research/receipt`) expose one combined,
Node54-probe-compatible readiness summary: a non-ready daily admission makes
the exposed `receipt_ready` false and `/readyz` return HTTP 503. The detailed
daily status remains available only from the dedicated route. `/readyz` returns
HTTP 200 only when the original receipt is ready and, when configured,
`admission_ready=true`. Stale, blocked,
calendar-unknown, or invalid daily input therefore keeps the service live but
not ready. Without daily options, Node53 behavior is unchanged.

## Scope and rollback

The implementation only changes the receipt server, CLI wiring, service
exports, service tests, this phase report, and README. Node52–58 modules and
artifacts remain untouched. Rollback is removing the optional daily arguments,
the new route, and the corresponding validation/tests/documentation.
