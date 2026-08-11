# Phase 23: Market-context analysis-input v2 MVP

This node connects the independent `market-context-v1` snapshot to the
analysis evidence chain without changing the six evidence IDs or rewriting
existing bundles.

## Version and compatibility

- `analysis-input-v1` remains readable and is produced when no market-context
  paths are supplied.
- `analysis-input-v2` is produced only when both the snapshot and report paths
  are supplied.
- A v1 bundle is never upgraded in place.
- The market context is an extension of the existing `market` evidence, not a
  seventh evidence ID.

## v2 validation

The bundle builder and downstream validators require:

- `market_context_ready=true`, `status=ready`, and `decision_ready=false`;
- exact `as_of` equality between the stock bundle and market context;
- matching market-context snapshot SHA-256 and calendar SHA-256;
- matching calendar version and valid `market-context-v1` snapshot version;
- all three fixed index symbols and ready per-index reports;
- valid Decimal records with no unsupported symbols, duplicate dates, or future
  trade dates;
- both market-context files inside `input-root`.

The existing market evidence retains its target-stock report, bars, coverage,
and calendar references, and appends the market-context snapshot and report
references plus their SHA-256 values. Summaries record only source, status,
fixed symbols, date range, and record count.

## Scope and downstream behavior

The analysis-report validator accepts v1 and v2 bundles. Quality, decision-input,
and safety checks accept the same explicit version set and preserve the false
decision gate. The request builder receives the v2 summary through the existing
`market` evidence; no real DeepSeek call is needed for this node.

This node does not add evidence IDs, change the DailyBar contract, recapture
indexes, alter technical or price-plan calculations, make a market judgment,
or produce BUY/SELL/HOLD, prices, position sizes, or trading authorization.

## Verification

Offline tests cover a valid v2 bundle, v1 compatibility, deterministic market
artifact references, paired-argument enforcement, snapshot/report hash
tampering, and consumption by the offline analysis validator. Existing tests
remain required to pass, with all output states retaining `decision_ready=false`.
