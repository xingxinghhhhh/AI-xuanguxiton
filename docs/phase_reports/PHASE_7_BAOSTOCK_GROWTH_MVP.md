# Phase 7: Baostock Quarterly Growth Snapshot MVP

## Scope completed

This phase adds a separate read-only quarterly growth snapshot for one stock.
It uses `query_growth_data`, parses fields by name, and keeps growth evidence
independent from the profitability snapshot.

The standard snapshot includes year-over-year equity, assets, net income,
parent net income, and basic EPS when returned. Operating-revenue and gross-
revenue growth fields are represented as null when the current provider response
does not supply them; missing values are never replaced with another metric.

Only records with `pubDate <= as_of` are eligible. Future-only, unknown-date,
provider-error, malformed, and conflicting records fail closed. A valid growth
snapshot sets `growth_ready=true` while `decision_ready` remains false.

## CLI

```bash
python -m a_share_ai.cli capture-growth \
  --symbol 600000.SH \
  --as-of 2026-08-10 \
  --start-year 2025 --start-quarter 1 \
  --end-year 2025 --end-quarter 4 \
  --output-dir reports/growth-001
```

The command writes request metadata, raw responses, `growth_snapshot.json`, and
`growth_report.json` with publication/report dates, statuses, and SHA-256
evidence.

## Verification

- `python -m pytest`: 65 passed
- `ruff check .`: passed
- `python -m compileall -q src tests`: passed
- Editable package installation: passed
- Offline fixtures cover valid, future-only, unknown publication date, provider
  error, and invalid numeric cases
- Controlled read-only Baostock smoke test: `600000.SH`, 2025 Q1–Q4,
  `as_of=2026-08-10`; status `ready`, `growth_ready=true`,
  `decision_ready=false`; selected `pubDate=2026-03-31`,
  `statDate=2025-12-31`
- Repeated smoke captures matched raw, snapshot, and report SHA-256 values

## Explicit non-goals

No merged financial report, balance sheet, cash flow, valuation, news, AI
analysis, trade decision, database, scheduler, or automatic trading is
included.
