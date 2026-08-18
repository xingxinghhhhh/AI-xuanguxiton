# Phase 64: daily research service launch gate audit MVP

## Goal

Independently audit a Node63 daily read-only service launch gate and its
referenced evidence before startup. This audit is read-only, fail-closed, and
does not rebuild the gate or start the service.

## Audit command

```bash
python -m a_share_ai.cli audit-daily-research-service-launch-gate \
  --gate reports/daily-service-gate/daily_research_service_launch_gate.json \
  --gate-report reports/daily-service-gate/daily_research_service_launch_gate_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-gate-audit
```

The audit version is `daily-research-service-launch-gate-audit-v1`. It checks
the gate/report pair, self-hashes, actual bytes, normalized artifact-root
paths, and the Node55 launch manifest, Node61 handoff/report, Node62 handoff
audit, and daily admission/report chain. It independently re-derives status
and readiness relationships while preserving `decision_ready=false`.

The output is
`daily_research_service_launch_gate_audit_report.json` with relative input
paths and SHAs, status, `gate_ready`, `audit_ready`, sanitized issues, and a
deterministic `output_sha256`. Valid ready, stale, and blocked gates are
auditable; tampered, missing, out-of-root, symlink-escaping, version-invalid,
or state-inconsistent inputs are not audit-ready.

## Exit and scope

The CLI returns `0` for a successful audit, `1` for a failed audit, and `2`
for invalid artifact-root or output configuration. It does not call the
Node63 builder, start the service, invoke Node60, access API keys, refresh
data, use the network, or set any decision gate true.
