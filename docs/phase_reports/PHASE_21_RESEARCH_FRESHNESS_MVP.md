# Phase 21: Research freshness audit MVP

## Scope

This node audits whether a ready single-stock research release still
corresponds to the latest completed A-share trading day at an explicitly
provided evaluation time. It uses the existing versioned local TradingCalendar
and a fixed Asia/Shanghai 15:00 close boundary. It never reads the system
clock internally, calls a provider, or makes an investment judgement.

## Contract

The report version is `research-freshness-v1`. The status is one of
`fresh`, `stale`, `calendar_unknown`, or `invalid`. A report is ready only
when the release's Asia/Shanghai calendar date equals the latest completed
trading date. Before the close, the current date is not considered complete;
at or after 15:00, a trading date may be considered complete. Weekends and
holidays use the preceding completed trading date.

The audit rejects a release after `evaluation_at`, calendar/report hash
mismatches, invalid timestamps, and unsupported or uncovered calendar ranges.
Every result keeps `decision_ready=false`.

## CLI

```bash
python -m a_share_ai.cli audit-research-freshness \
  --release-manifest reports/analysis-deepseek-001/release/research_release_manifest.json \
  --release-report reports/analysis-deepseek-001/release/research_release_report.json \
  --calendar fixtures/market/calendar/sample.json \
  --calendar-report reports/calendar_report.json \
  --evaluation-at 2026-08-11T08:00:00+00:00 \
  --output-dir reports/analysis-deepseek-001/freshness
```

The calendar report must identify the calendar SHA-256, calendar version,
`status=complete`, and `decision_ready=true`. The evaluation time is always a
CLI input, so fixed inputs produce deterministic results.

## Verification

The implementation is offline and read-only. It reports data freshness only;
it does not assess price movement, news coverage, provider recency, stock
quality, or any transaction action.
