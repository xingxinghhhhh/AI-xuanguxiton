# Phase 29: market-aware review-to-release replay MVP

## Goal

Close the offline path from a market-aware pending review packet to a guarded
research release when, and only when, a human supplies a complete review
submission.

## Delivered

- Added `market-aware-release-replay-v1`.
- Added `replay-market-aware-release` to the CLI.
- Reused the existing review-record validator and research-release builder so
  packet hashes, review IDs, timestamps, chain hashes, and safety gates keep
  one implementation.
- Validated `analysis-input-v2`, `market-context-summary-v1`, and
  `relative-strength-v1` before applying the submission.
- Recorded the review-result and research-release stage paths, SHA-256 values,
  statuses, and readiness flags in
  `market_aware_release_replay_report.json`.
- Stopped before release for invalid submissions and for challenged or
  follow-up review statuses.
- Kept `decision_ready=false` in every successful artifact.

## Verification

The test suite covers all-confirmed release, review-gate failure, packet SHA
mismatch, and CLI success. No real DeepSeek request was made in this phase.
