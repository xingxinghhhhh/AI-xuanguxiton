# Phase 25: Relative strength summary MVP

This node adds a deterministic, numeric comparison between the target stock's
existing `technical-v1` returns and the three fixed benchmark indexes already
captured by `market-context-v1`.

## Contract

- Version: `relative-strength-v1`.
- Location: `summaries.technical.relative_strength` in `analysis-input-v2`.
- Formula: `relative_return_n = stock_return_n - benchmark_return_n` for
  `n ∈ {1, 5, 20}`.
- Every output value is serialized from Decimal arithmetic; insufficient stock
  or benchmark history remains `null` without substituting another period.
- Each benchmark entry carries its symbol, three benchmark returns, and three
  relative returns.

The builder validates `technical-v1`, `market-context-v1`, and
`market-context-summary-v1`, checks the fixed benchmark set, and requires the
latest stock and index trade dates to match. Malformed or missing return fields,
invalid Decimal values, version mismatches, and date mismatches fail closed.

## Scope and safety

The node only extends the v2 derived summary. It leaves v1 bundles,
technical-indicator calculations, raw evidence, request transport, and
`analysis-report-v1` unchanged. It produces no strong/weak labels, rankings,
buy/sell/hold decisions, prices, position sizes, orders, or network requests.

The structured request builder forwards the summary values while excluding local
paths, raw responses, hashes, and credentials. A two-day market snapshot yields
the one-day comparison and `null` five-day and twenty-day values; the decision
gate remains `decision_ready=false`.
