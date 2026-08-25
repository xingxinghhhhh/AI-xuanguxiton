# Phase 67 — Daily research service run audit MVP

Node67 independently audits an existing Node66
`daily_research_service_run_report.json`. The audit revalidates the report's
fixed schema and canonical self-hash, checks its gate and launch-audit paths
and SHA-256 values inside the artifact root, then reuses the Node65 startup
loader to validate the audited gate chain.

```bash
python -m a_share_ai.cli audit-daily-research-service-run \
  --run-report reports/daily-service-run/daily_research_service_run_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-run-audit
```

This command does not start a service, call the Node60 probe, access HTTP,
refresh data, call an AI provider, or read API keys. A structurally valid
ready, failed, or blocked run can be independently audited, but only the
ready state retains `run_ready=true`; every output keeps
`decision_ready=false`. Invalid, missing, tampered, unknown-field, and
out-of-root inputs fail closed. Repeated audits of unchanged inputs produce
the same self-hashed `daily_research_service_run_audit_report.json`.
