# Phase 69 — Daily research service release audit MVP

Node69 independently audits the Node68 release manifest and report, then
recomputes the release admission from the Node66/67 artifacts they reference.

```bash
python -m a_share_ai.cli audit-daily-research-service-release \
  --manifest reports/daily-service-release/daily_research_service_release_manifest.json \
  --report reports/daily-service-release/daily_research_service_release_report.json \
  --artifact-root reports \
  --output-dir reports/daily-service-release-audit
```

The audit writes a self-hashed
`daily_research_service_release_audit_report.json`. It verifies the Node68
schemas, self-hashes and manifest binding, then checks the actual Node66/67
files, gate chain, paths, hashes, timestamps, status and decision gate. A
valid blocked or failed release is auditable but never promoted to ready.

This command does not rebuild Node68, start or probe a service, access HTTP or
API keys, modify upstream artifacts, deploy, or authorize trading.
