# Phase 57: daily research run independent audit MVP

## Goal

Node57 adds `daily-research-run-audit-v1`, a bounded, read-only audit of a
Node56 run. It confirms that a previously generated result is complete and
safe for downstream consumption without re-running data providers, calling
DeepSeek/OpenAI, reading API keys, or changing any artifact.

## Command

```bash
python -m a_share_ai.cli audit-daily-research-run \
  --run-report reports/daily-run-001/daily_research_run_report.json \
  --artifact-root reports/daily-run-001 \
  --output-dir reports/daily-run-001-audit
```

The output is `daily_research_run_audit_report.json` with fixed fields for the
run path/hash, status, stage count, failed stage, analysis-input references,
version summaries, issues, and `decision_ready=false`. Its
`output_sha256` is a deterministic self-hash over the canonical report with
that field set to `null`.

## Checks

The audit fail-closes on malformed or unknown run fields, version mismatch,
invalid timestamps, absolute or escaping paths, missing or duplicate artifacts,
declared SHA-256 mismatches, incomplete ready-stage artifacts, invalid stage
ordering, or an inconsistent blocked/failed/skipped flow. A ready run must
contain all nine stages, a valid `analysis-input-v2` bundle, the
`market-context-summary-v1` and `relative-strength-v1` summaries, and false
decision gates. A blocked run must contain exactly one failed stage followed
only by skipped stages and must not expose an analysis input.

## Scope and rollback

The implementation adds the runtime audit module, tests, this phase report,
and one CLI/README branch. It does not modify the Node52–56 runtime logic or
artifacts, access external services, schedule work, connect a database, or
enable trading. Rollback is removing the Node57 module, tests, documentation,
and CLI branch.
