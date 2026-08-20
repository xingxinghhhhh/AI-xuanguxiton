# Node78 — Daily research release-run admission startup smoke admission MVP

## Purpose

Node78 creates a final, read-only admission summary from the Node76 startup
smoke receipt and the independent Node77 smoke-audit receipt. It gives an
operator or a future startup flow one deterministic boundary for deciding
whether the recorded smoke evidence is ready, while keeping the decision gate
closed.

## Scope

- Read and validate only the two supplied Node76/Node77 JSON receipts.
- Recheck exact schemas, versions, self-hashes, artifact-root-relative paths,
  actual input SHA-256 values, identities, and cross-node state.
- Preserve consistent `ready`, `blocked`, and `failed` evidence as bounded
  outcomes; classify malformed, tampered, or contradictory evidence as
  `invalid`.
- Write a compact self-hashed manifest and report pair deterministically.
- Keep the implementation offline, read-only, and free of subprocess, socket,
  HTTP, network, API-key, AI, and trading operations.

The node does not rerun Node76 or Node77, start a service, refresh data,
modify upstream receipts, inspect current ports, or set `decision_ready=true`.

## Output contract

The command is:

```bash
python -m a_share_ai.cli \
  build-daily-research-service-release-run-admission-startup-smoke-admission \
  --smoke-report PATH \
  --smoke-audit-report PATH \
  --artifact-root PATH \
  --output-dir PATH
```

It writes:

- `daily_research_service_release_run_admission_startup_smoke_admission.json`
- `daily_research_service_release_run_admission_startup_smoke_admission_report.json`

Both outputs use relative paths and SHA-256 references, contain sanitized
issues, and keep `decision_ready=false`. `admission_ready=true` and exit `0`
require both input statuses to be `ready`, both readiness chains to be true,
complete runtime fields, matching identities/hashes/state, and no issues.
Consistent blocked or failed evidence returns exit `1`, preserves its issues,
and remains non-ready. Invalid or contradictory evidence returns exit `1` with
`status=invalid` and `audit_ready=false`. Artifact-root/output configuration
errors return exit `2`.

## Verification

The focused suite covers ready, blocked, failed, invalid audit evidence,
self-hash preservation, deterministic output shape, offline operation, and
CLI output-root handling. Existing Node70–77 CLI/API regressions, static
checks, compilation, and the full repository suite remain required before
publishing the node.
