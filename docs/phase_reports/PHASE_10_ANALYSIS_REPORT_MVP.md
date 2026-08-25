# Phase 10: evidence-backed research analysis report MVP

## Outcome

Added the versioned `analysis-report-v1` contract. It consumes an already
validated `analysis-input-v1` bundle and an offline JSON response fixture, then
produces a deterministic, evidence-cited research report. The node keeps
`decision_ready=false` and does not call an AI service or make a trade
recommendation.

The existing `EvidenceEntry.name` values (`market`, `technical`,
`price_plan`, `profitability`, `growth`, and `announcements`) are used as the
stable `evidence_id`. The output has eight sections: those six evidence
sections plus `risks` and `unknowns`. Claims use `claim_id`, `kind`, `text`,
`citation_ids`, and `observed_dates`; citations are expanded once in a
top-level citation index. The evidence module from Phase 9 was not changed.

## CLI

```bash
python -m a_share_ai.cli analyze-input \
  --bundle reports/analysis-input-001/analysis_input_bundle.json \
  --input-root reports \
  --response-fixture fixtures/analysis/report/valid_provider.json \
  --output-dir reports/analysis-report-001
```

The command writes:

- `research_analysis.json`: structured claims for market, technical,
  price-plan, profitability, growth, announcement facts, risks, and unknowns;
- `research_analysis_report.json`: status, input bundle SHA-256, output
  SHA-256, issue details, `analysis_ready`, and `decision_ready`.

Every claim must cite one or more known bundle evidence IDs. The output expands
each citation once with the bundle's report path, report SHA-256, artifact
paths, and artifact SHA-256 values. The validator re-hashes the bundle report
and every referenced file under `--input-root` before accepting the result.

## Fail-closed gates

- bundle and input report versions, symbol, cutoff, readiness, and SHA-256
  must agree;
- every evidence report and artifact must exist under `--input-root` and match
  the bundle manifest;
- provider output must have exactly the versioned sections and structured
  observation fields;
- unknown evidence IDs, duplicate observations, missing citations, unknown
  fields, symbol/cutoff mismatches, malformed JSON, and future dates are
  rejected;
- BUY/SELL/HOLD and 买入/卖出/观望 terms are rejected;
- valid and invalid outputs are deterministic JSON, and every output keeps
  `decision_ready=false`.

## Verification

- `python -m pytest`: 90 passed
- `ruff check .`: passed
- `python -m compileall -q src tests`: passed
- analysis-specific tests cover valid deterministic output, citation
  expansion, bundle and evidence hash tampering, missing citations, future
  claims, symbol mismatch, prohibited decisions, malformed provider data,
  and CLI readiness.
- no network, credentials, real AI provider, database, scheduling, or trading
  path was added.
