# Phase 18: Analysis review record MVP

## Scope

This node accepts a complete, user-supplied human review record for the
pending `analysis-review-v1` packet. It records whether each existing claim
and its evidence mapping was confirmed, challenged, or marked for follow-up.
It does not edit claims, citations, source paths, hashes, or any upstream
analysis artifact.

## Contract

The record version is `analysis-review-record-v1`. A submission contains only
the packet SHA-256 and one item for every packet `review_id`:

```json
{
  "review_packet_sha256": "<sha256>",
  "items": [
    {
      "review_id": "market:claim-001",
      "status": "confirmed",
      "notes": null,
      "reviewed_at": "2026-08-11T12:00:00+00:00"
    }
  ]
}
```

Only `confirmed`, `challenged`, and `follow_up` are accepted. The latter two
require non-empty notes. Every timestamp must be an explicit ISO-8601 value
with a timezone. Missing, duplicate, unknown, or extra fields fail closed.

## CLI

```bash
python -m a_share_ai.cli apply-analysis-review \
  --packet reports/analysis-deepseek-001/review/analysis_review_packet.json \
  --packet-report reports/analysis-deepseek-001/review/analysis_review_report.json \
  --submission reports/analysis-deepseek-001/review/review_submission.json \
  --output-dir reports/analysis-deepseek-001/review-result
```

The command writes `analysis_review_result.json` and
`analysis_review_result_report.json`. A complete all-confirmed submission sets
`review_complete=true` and `review_gate_pass=true`; challenged or follow-up
items keep the result valid but set the review gate to false. Every result
keeps `decision_ready=false`. The review gate only confirms the human check of
the existing analysis/evidence mapping; it is not authorization to trade.

## Verification

The implementation is deterministic and offline. It performs no API calls,
does not read credentials, does not modify the review packet, and rejects
transaction fields such as BUY, SELL, or HOLD.
