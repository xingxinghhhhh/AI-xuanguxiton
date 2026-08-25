# Phase 9: point-in-time analysis input bundle MVP

## Outcome

Added `analysis-input-v1`, a read-only evidence assembler for one A-share symbol.
It references existing artifacts without copying or modifying them, normalizes
date-only and timezone-aware `as_of` values to an Asia/Shanghai cutoff date, and
keeps the final `decision_ready` flag permanently false.

## CLI

```bash
python -m a_share_ai.cli build-analysis-input \
  --symbol 600000.SH \
  --as-of 2026-08-10T12:00:00Z \
  --input-root reports \
  --market-bars baostock-001/replay-health/replay_output.jsonl \
  --market-health-report baostock-001/replay-health/health_report.json \
  --coverage-report baostock-001/coverage/coverage_report.json \
  --calendar ../fixtures/market/calendar/baostock-smoke-2025-06.json \
  --technical-input ../fixtures/analysis/technical/valid_80_bars.jsonl \
  --technical-features technical-valid-001/technical_features.jsonl \
  --technical-report technical-valid-001/technical_report.json \
  --price-plan-input ../fixtures/decision/technical_price_plan/valid_features.jsonl \
  --price-plan price-plan-valid-final/technical_price_plan.json \
  --price-plan-report price-plan-valid-final/price_plan_report.json \
  --profitability-snapshot profitability-001/profitability_snapshot.json \
  --profitability-report profitability-001/profitability_report.json \
  --growth-snapshot growth-001/growth_snapshot.json \
  --growth-report growth-001/growth_report.json \
  --announcements-snapshot announcements-001/announcements_snapshot.json \
  --announcements-report announcements-001/announcements_report.json \
  --output-dir reports/analysis-input-001
```

The command writes only `analysis_input_bundle.json` and
`analysis_input_report.json`. The bundle contains relative references, report and
artifact SHA-256 values, upstream statuses, and concise summaries.

## Hard gates

- all six evidence groups must resolve to one symbol;
- all report cutoffs must equal the CLI cutoff after Asia/Shanghai normalization;
- all referenced hashes must match the bytes on disk;
- market health and coverage must be connected/complete;
- technical features, price plan, profitability, and growth must be ready;
- announcements must be `ready` or a successful `empty` query;
- future trade dates, publication dates, or received timestamps fail closed;
- missing artifacts, malformed JSON, path escape, and upstream not-ready states fail closed.

## Verification

- `python -m pytest`: 78 passed
- `ruff check .`: passed
- `python -m compileall -q src tests`: passed
- A valid deterministic fixture produces `analysis_input_ready=true` and identical
  bundle SHA-256 values across repeated runs.
- Mixed dates from the existing real captures fail closed before any AI or trading
  path is reached.
- No network request, credential, database, AI call, or existing artifact mutation
  was added.
