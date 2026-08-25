# Phase 61: controlled daily research startup handoff MVP

## Goal

Aggregate the existing Node56 run report, Node57 run audit, and Node58 daily
admission pair into one deterministic, read-only handoff package for a human
orchestrator. The package prevents mixing symbols, timestamps, or artifact
batches before a service is started.

## Command and outputs

```bash
python -m a_share_ai.cli build-daily-research-handoff \
  --run-report reports/daily-run-001/daily_research_run_report.json \
  --run-audit-report reports/daily-run-001-audit/daily_research_run_audit_report.json \
  --admission reports/daily-admission/daily_research_admission.json \
  --admission-report reports/daily-admission/daily_research_admission_report.json \
  --artifact-root reports \
  --output-dir reports/daily-handoff
```

The command writes `daily_research_handoff.json` and
`daily_research_handoff_report.json`. Both contain controlled relative paths,
actual SHA-256 values, symbol/time metadata, upstream status and readiness,
`handoff_ready`, sanitized issues, and `decision_ready=false`.

Exit `0` means the run, audit, and daily admission are all ready and mutually
consistent. Exit `1` means the inputs are valid but stale/blocked/not admitted.
Exit `2` means configuration, path, or input-contract validation failed.

## Safety and scope

The handoff validates versions, exact fields, self-hashes, path boundaries,
artifact hashes, symbol and timestamp consistency, and the full admission pair.
It does not rerun research, call a network provider, start a service, write a
launch manifest, invoke Node60, or add scheduling, credentials, UI, or trading
behavior. Stale and blocked upstream results remain visible but cannot produce
`handoff_ready=true`.
