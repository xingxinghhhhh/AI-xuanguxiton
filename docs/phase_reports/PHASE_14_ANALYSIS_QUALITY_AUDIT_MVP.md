# Phase 14: Offline analysis quality and evidence coverage audit MVP

## Scope

This node adds the deterministic `analysis-quality-v1` audit layer after
`analysis-report-v1` rendering. It derives `quality_ready` from existing JSON
artifacts and never calls an AI provider, reads credentials, uses the network,
or changes the trading decision boundary.

## Implementation

- `quality.py` verifies analysis, analysis-report, rendered-report, input-bundle,
  evidence-report, and artifact SHA-256 values.
- Every one of the eight analysis sections must contain claims with citations;
  citation coverage must be 100% and all six fixed evidence IDs must be used.
- Section evidence mappings are fixed and fail closed; risk and unknown claims
  remain explicit coverage requirements.
- `audit-analysis-quality` writes `analysis_quality_report.json` with stable
  status, issue codes, section counts, evidence usage, hashes, and readiness
  flags. `decision_ready` is always false.

## Verification

The quality test suite covers a valid pass, CLI output, wrong section mapping,
unused evidence, hash/decision-gate failures, and missing risk/unknown coverage.
The full repository gates remain the acceptance criteria for this node.
