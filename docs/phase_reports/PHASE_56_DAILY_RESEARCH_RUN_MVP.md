# Phase 56: single-stock daily research run MVP

## Goal

Node56 adds one manually triggered, single-stock, single-pass public-data run.
It composes the existing Baostock daily and benchmark adapters, local calendar
audits, technical features, technical price plan, Baostock fundamentals,
CNINFO announcements, and the existing `analysis-input-v2` builder.

## Runtime specification

The CLI accepts a strict JSON spec with exactly these fields:

```json
{
  "run_version": "daily-research-run-v1",
  "symbol": "600000.SH",
  "start_date": "2026-08-01",
  "end_date": "2026-08-10",
  "as_of": "2026-08-10T12:00:00+00:00",
  "received_at": "2026-08-10T12:00:00+00:00",
  "calendar_path": "market/calendar.json",
  "fundamentals_start": {"year": 2025, "quarter": 1},
  "fundamentals_end": {"year": 2026, "quarter": 2},
  "announcement_start": "2026-01-01",
  "announcement_end": "2026-08-10"
}
```

The spec and referenced calendar must be inside `input-root`. `as_of` and
`received_at` must represent one consistent point-in-time instant because the
existing market-context and health contracts use the same cutoff. The output
directory must also be inside `input-root` and must not equal the root.

## Command and output

```bash
python -m a_share_ai.cli run-daily-research \
  --spec runtime_spec.json \
  --input-root reports \
  --output-dir reports/daily-run-001 \
  --source-mode public-read-only
```

The run writes stage artifacts below the output directory and one
`daily_research_run_report.json`. The report records each stage's status,
relative artifact paths, SHA-256 values, error code, and issues. A failed stage
stops later stages, which are recorded as `skipped`; the runner never retries,
falls back to another provider, or continues into analysis after invalid data.
Successful runs record the v2 bundle path and hash plus the existing
market-context-summary and relative-strength versions.

All outputs keep `decision_ready=false`. The command does not call DeepSeek or
any other AI provider, does not schedule itself, does not use a database, does
not scan multiple stocks, and does not execute trades or emit BUY/SELL/HOLD,
price, position, or authorization decisions.

## Scope and rollback

The implementation only adds the runtime package, runtime tests, this phase
report, and a CLI/README entry. It reuses the existing adapters and validators
without modifying Node52–55 contracts or artifacts. Rollback is removing the
Node56 runtime module, tests, documentation, and CLI branch.
