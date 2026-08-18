# Phase 63: controlled daily research service launch gate MVP

## Goal

Bind the Node61 daily research handoff and Node62 independent audit to the
existing loopback-only read-only service startup. The gate is a versioned,
path-bounded artifact and does not alter the Node55 launch manifest contract.

## Build

```bash
python -m a_share_ai.cli build-daily-research-service-launch-gate \
  --handoff reports/daily-handoff/daily_research_handoff.json \
  --handoff-report reports/daily-handoff/daily_research_handoff_report.json \
  --handoff-audit-report reports/daily-handoff-audit/daily_research_handoff_audit_report.json \
  --launch-manifest reports/service/read_only_receipt_service_launch.json \
  --artifact-root reports \
  --output-dir reports/daily-service-gate
```

The gate version is `daily-research-service-launch-gate-v1`. Its pair of JSON
files records the old launch manifest path/SHA, Node61 handoff/report
path/SHA, Node62 audit path/SHA, daily admission path/SHA, symbol, `as_of`,
`evaluation_at`, receipt and admission readiness, audit/handoff readiness,
status, `gate_ready`, sanitized issues, `decision_ready=false`, and a
deterministic `output_sha256`.

## Startup binding and compatibility

Gate startup is explicit:

```bash
python -m a_share_ai.cli serve-research-receipt \
  --daily-launch-gate reports/daily-service-gate/daily_research_service_launch_gate.json \
  --daily-launch-gate-root reports \
  --artifact-root reports
```

The service reloads all referenced bytes before listening. A stale or blocked
gate exits without binding; `--check-only` reports the gate without binding.
When ready, the server reuses the existing receipt service and daily admission
route, so Node60's four-route probe remains unchanged. Direct startup and the
Node55 `read-only-receipt-service-launch-v1` manifest remain supported.

## State and safety

Ready means the old receipt launch, Node61 handoff, Node62 audit, and daily
admission are mutually consistent and ready; it returns `0`. Valid stale or
blocked inputs return `1` and cannot become `gate_ready=true`. Invalid input
contracts fail closed in the report and return `1`; missing/incompatible CLI
configuration returns `2`. All paths are normalized and artifact-root bound,
all declared bytes are rehashed, and `decision_ready` is always false.

The node does not refresh data, call DeepSeek/Baostock, schedule or deploy
anything, add public endpoints, modify old artifacts, or authorize trading.

## Acceptance evidence

The focused suite covers ready, stale, blocked, tamper/state rewrite, old
manifest compatibility, check-only no-listen behavior, and a ready gate local
service followed by the existing Node60 probe. Full project tests and static
quality gates are required before review.
