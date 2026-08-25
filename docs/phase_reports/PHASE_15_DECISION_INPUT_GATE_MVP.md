# Phase 15: Decision input readiness and non-trading snapshot MVP

## Scope

This node packages the existing evidence bundle, analysis report, rendered
report, and quality audit into `decision-input-v1`. It creates a final
cross-checked input boundary for a future decision module without generating a
decision or changing any upstream contract.

## Implementation

- `decision_input.py` verifies the bundle and bundle-report readiness, analysis
  and quality gates, rendered output, SHA-256 links, versions, symbol, and
  point-in-time `as_of` value.
- All artifact references are controlled paths relative to an explicit
  `artifact-root`; bundle paths remain checked relative to `input-root`.
- `build-decision-input` writes `decision_input_snapshot.json` and
  `decision_input_report.json`. Invalid or tampered inputs produce an invalid
  audit report, while `decision_ready` is always false.
- No provider, network request, credential, new indicator, trade signal,
  position, or order is introduced.

## Verification

Tests cover a valid deterministic snapshot, CLI output, readiness and future
date gates, hash mismatch, and path escape. The actual DeepSeek analysis ->
render -> quality artifact chain was replayed offline into a ready snapshot.
