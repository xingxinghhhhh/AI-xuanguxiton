# Phase 6: Baostock Quarterly Profitability Snapshot MVP

## Scope completed

This phase adds one read-only fundamental evidence path for one stock:
Baostock quarterly profitability data. It records `roeAvg`, `npMargin`,
`netProfit`, and `epsTTM`; `gpMargin` is preserved when present and remains
null when the provider does not return it.

The snapshot is selected only from reports whose `pubDate <= as_of`. Future
reports, unknown publication dates, provider errors, malformed values, and
conflicting report dates fail closed. The resulting snapshot has
`fundamental_ready=true` when valid, but `decision_ready` is always false.

## CLI

```bash
python -m a_share_ai.cli capture-profitability \
  --symbol 600000.SH \
  --as-of 2026-08-10 \
  --start-year 2025 --start-quarter 1 \
  --end-year 2025 --end-quarter 4 \
  --output-dir reports/profitability-001
```

The command writes request metadata, the raw provider response, the selected
standardized snapshot, and `profitability_report.json` with raw/snapshot
SHA-256 values, report/publication dates, status, and gating details.

## Verification

- `python -m pytest`: 60 passed
- `ruff check .`: passed
- `python -m compileall -q src tests`: passed
- Editable package installation: passed
- Offline fixtures cover valid, future-only, missing publication date, provider
  error, and invalid numeric cases
- Controlled read-only Baostock smoke test: `600000.SH`, 2025 Q1–Q4,
  `as_of=2026-08-10`; selected `statDate=2025-12-31`, `pubDate=2026-03-31`,
  `fundamental_ready=true`, `decision_ready=false`
- Repeated smoke captures matched raw, snapshot, and report SHA-256 values

## Explicit non-goals

No revenue growth, balance sheet, cash flow, valuation, news, industry or
macro data, AI analysis, trade decision, position sizing, database, scheduler,
or automatic trading is included.
