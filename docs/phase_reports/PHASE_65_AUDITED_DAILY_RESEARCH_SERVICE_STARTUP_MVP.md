# Phase 65: audited daily research service startup MVP

## Goal

Bind the independent Node64 launch-gate audit to the Node63 gate-mode service
startup. A gate can be inspected with `--check-only`, but actual listening is
allowed only when the gate and its independent audit report are both ready.

## Startup command

```bash
python -m a_share_ai.cli serve-research-receipt \
  --daily-launch-gate reports/daily-service-gate/daily_research_service_launch_gate.json \
  --daily-launch-gate-root reports \
  --daily-launch-gate-audit reports/daily-service-gate-audit/daily_research_service_launch_gate_audit_report.json \
  --artifact-root reports
```

The startup validator rechecks the gate pair and the Node64 audit report's
self-hash, version, gate/report paths and actual SHAs, symbol, timestamps,
status, readiness, and `decision_ready=false`. It does not rebuild the gate or
audit, write artifacts, call Node60, access the network, or read API keys.

## Failure semantics and compatibility

Ready gate plus ready audit starts the existing read-only receipt service and
keeps the existing Node60 probe and four routes unchanged. Stale, blocked,
invalid, tampered, missing, or out-of-root evidence returns `1` and opens no
socket. Missing audit parameters or illegal option combinations return `2`.
The old direct and Node55 launch-manifest modes remain compatible. Gate-only
`--check-only` remains a read-only Node63 compatibility check; it is never
accepted for actual service startup.

All paths remain under the controlled artifact root and all decision paths keep
`decision_ready=false`.
