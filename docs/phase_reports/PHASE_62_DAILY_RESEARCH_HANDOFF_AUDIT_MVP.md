# Phase 62: independent daily research handoff audit MVP

## Goal

Independently verify a Node61 `daily-research-handoff-v1` package and its four
referenced upstream artifacts. This creates a fail-closed, deterministic review
artifact for a human orchestrator without rebuilding or mutating the handoff.

## Command and output

```bash
python -m a_share_ai.cli audit-daily-research-handoff \
  --handoff reports/daily-handoff/daily_research_handoff.json \
  --handoff-report reports/daily-handoff/daily_research_handoff_report.json \
  --artifact-root reports \
  --output-dir reports/daily-handoff-audit
```

The command writes `daily_research_handoff_audit_report.json`. The report
contains the audit version, actual SHA-256 values for the handoff, its report,
the run report, run audit, admission, and admission report, plus symbol,
`as_of`, `evaluation_at`, status, readiness, sanitized issues, and
`decision_ready=false`. Its own `output_sha256` is deterministic.

## Independent checks

The audit revalidates exact field sets and versions, canonical self-hashes,
relative path normalization, artifact-root containment after resolution,
symlink escape protection, upstream byte hashes, symbol/timestamp chains,
ready/stale/blocked semantics, and the Node56/57/58 version contracts. It
also checks that the handoff readiness fields agree with the independently
loaded upstream states.

Valid stale or blocked handoffs can produce `audit_ready=true` so their state
is reviewable, but they can never be promoted to `handoff_ready=true`.
Invalid inputs produce `audit_ready=false`; no network, API key, database,
scheduler, service, UI, trading, or handoff rebuild is involved. The audit
does not modify any input artifact.

## Acceptance evidence

The focused suite covers ready, stale, blocked, hash tampering, missing input,
unknown fields, path escape, symlink escape when the host permits symlinks,
deterministic output, unchanged inputs, and CLI exit behavior. On the Windows
host used for development, the symlink case is skipped when the OS denies
symlink creation.
