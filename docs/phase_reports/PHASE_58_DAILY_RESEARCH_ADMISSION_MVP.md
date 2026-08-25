# Phase 58: daily research freshness admission MVP

## Goal

Node58 adds `daily-research-admission-v1`, a read-only freshness gate for
downstream consumption of Node56/57 output. It prevents a structurally valid
but old daily run from being treated as the current research result.

## Command and inputs

```bash
python -m a_share_ai.cli build-daily-research-admission \
  --run-report reports/daily-run-001/daily_research_run_report.json \
  --run-audit-report reports/daily-run-001-audit/daily_research_run_audit_report.json \
  --calendar reports/calendar.json \
  --calendar-report reports/calendar_report.json \
  --evaluation-at 2026-08-17T13:00:00+00:00 \
  --artifact-root reports \
  --output-dir reports/daily-admission
```

The command requires a ready Node56 run, a ready Node57 audit, a complete
`TradingCalendar` JSON, and a calendar report whose SHA-256 and version match
the calendar. All inputs must be normalized relative paths inside
`artifact-root`; the output directory is also bounded inside that root.

## Freshness policy

Evaluation uses `Asia/Shanghai` and the existing 15:00 close boundary. Before
the close, the latest completed trading date is the previous calendar session;
at or after the close it may include the evaluation date. A run is `ready` only
when its `as_of` local date equals that latest completed session. Older runs are
`stale`; an uncovered evaluation date is `calendar_unknown`; a blocked Node57
chain is `blocked`; malformed, future, or hash-inconsistent inputs are
`invalid`.

The output writes both `daily_research_admission.json` and
`daily_research_admission_report.json` with the fixed input paths and SHA-256
values, freshness state, expected latest trading date, `audit_ready`,
`analysis_input_ready`, `admission_ready`, issues, and deterministic
`output_sha256`. Every state keeps `decision_ready=false`.

## Scope and rollback

The implementation adds the runtime admission module, tests, this phase report,
and one CLI/README branch. It does not rerun Node56/57, access external
services, modify existing artifacts, connect a database, schedule work, or
enable trading. Rollback is removing the Node58 module, tests, documentation,
and CLI branch.
