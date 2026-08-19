# Phase 68 — Daily research service release admission MVP

Node68 binds the Node66 service run report to the Node67 independent audit and
produces a deterministic, read-only release admission for human deployment or
rollback review.

```bash
python -m a_share_ai.cli build-daily-research-service-release \
  --run-report reports/daily-service-run/daily_research_service_run_report.json \
  --run-audit-report reports/daily-service-run-audit/daily_research_service_run_audit_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release
```

The command writes `daily_research_service_release_manifest.json` and
`daily_research_service_release_report.json`. It verifies both upstream
schemas, self-hashes, actual SHAs, relative paths, artifact-root boundaries,
symbol/timestamp consistency, and the Node66/67 readiness chain. Only a ready
audited run can set `release_ready=true`; failed or blocked runs remain
unpublishable. Every artifact keeps `decision_ready=false`.

This is an admission artifact only. It does not deploy, start, stop, probe, or
monitor a service; access HTTP or API keys; refresh data; call AI providers;
or authorize trading.
