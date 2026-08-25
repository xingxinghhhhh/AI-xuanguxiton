# Phase 26: Market-aware offline end-to-end replay MVP

This node adds a thin offline orchestrator proving that the market-aware v2
summaries survive the existing read-only research chain without changing any
downstream contract.

## Contract

- Version: `market-aware-replay-v1`.
- CLI: `python -m a_share_ai.cli replay-market-aware-analysis`.
- Input: an `analysis-input-v2` bundle and report, the declared `input-root`,
  and an offline provider response fixture.
- Output: the existing stage artifacts plus
  `market_aware_replay_report.json`.

The replay calls existing modules in this fixed order:

1. offline `analysis-report-v1`;
2. Markdown rendering;
3. quality audit;
4. decision-input snapshot;
5. safety audit;
6. pending-only human review packet.

The report records each stage's status, relative output/report paths, and
SHA-256 values, along with the bundle hash, market summary version,
relative-strength version, and final readiness gates. It always records
`review_complete=false` and `decision_ready=false`.

## Safety and failure behavior

The entry point requires `analysis-input-v2`,
`market-context-summary-v1`, and `relative-strength-v1`. It stops at the first
invalid stage and marks later stages as skipped. It does not duplicate the
downstream evidence, citation, quality, decision-input, safety, or review
validation rules; it delegates those checks to the existing modules.

This node does not call DeepSeek or any other provider, read API keys, capture
new data, create a review result, create a research release, or add trading
decisions. A valid replay reaches a pending human review packet only.
