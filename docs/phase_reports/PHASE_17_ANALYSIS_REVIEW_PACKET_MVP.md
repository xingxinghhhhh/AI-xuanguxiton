# Phase 17: Single-stock analysis manual review packet MVP

## Scope

This node turns a safety-passed, evidence-backed analysis into a deterministic
claim-by-claim manual review packet. It creates review material only; it does
not approve, reject, or modify any claim and never creates a trading decision.

## Implementation

- `review.py` validates the decision-input snapshot/report, safety report,
  analysis and analysis-report paths, hashes, versions, readiness states,
  symbol, and point-in-time cutoff.
- It rechecks every fixed evidence citation path and artifact SHA, then expands
  each claim into exactly one review item with a stable `section:claim_id`
  review ID.
- Every item starts as `review_status=pending` with null notes and timestamp;
  the packet records claim, risk, unknown, section, and upstream hash counts.
- `build-analysis-review` writes `analysis_review_packet.json` and
  `analysis_review_report.json`; no review mutation interface is provided.

## Verification

Tests cover a valid deterministic packet, CLI output, safety-gate failure,
tampered citation artifacts, future cutoff rejection, pending item fields, and
section coverage. The real DeepSeek analysis chain was replayed offline and
produced an eight-item pending packet with risks and unknowns represented.
