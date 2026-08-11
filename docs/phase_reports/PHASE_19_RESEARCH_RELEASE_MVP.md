# Phase 19: Research release manifest MVP

## Scope

This node creates a final, single-stock research release manifest from the
decision-input snapshot, safety audit, pending review packet, and completed
human review result. It confirms that one reviewed research chain is internally
consistent and ready for delivery to a reader. It does not copy source data or
report bodies and never creates an investment conclusion.

## Contract

The manifest version is `research-release-v1`. A ready release requires:

- `decision_input_ready=true` and a ready decision-input snapshot/report;
- `safety_ready=true` with a passing safety report;
- a pending `analysis-review-v1` packet matched by its SHA-256;
- a complete `analysis-review-record-v1` result with every item confirmed;
- matching symbol, cutoff time, versions, hashes, and artifact-root boundaries;
- `decision_ready=false` in every upstream artifact and the release itself.

The manifest contains only controlled relative artifact paths, versions,
statuses, and SHA-256 values. It does not embed claims, citations, evidence
contents, credentials, or absolute local paths.

## CLI

```bash
python -m a_share_ai.cli build-research-release \
  --decision-input reports/analysis-deepseek-001/decision-input/decision_input_snapshot.json \
  --decision-input-report reports/analysis-deepseek-001/decision-input/decision_input_report.json \
  --safety-report reports/analysis-deepseek-001/safety/analysis_safety_report.json \
  --analysis-review-packet reports/analysis-deepseek-001/review/analysis_review_packet.json \
  --analysis-review-result reports/analysis-deepseek-001/review-result/analysis_review_result.json \
  --analysis-review-result-report reports/analysis-deepseek-001/review-result/analysis_review_result_report.json \
  --artifact-root reports \
  --output-dir reports/analysis-deepseek-001/release
```

The command writes `research_release_manifest.json` and
`research_release_report.json`. Any challenged or follow-up review, hash
mismatch, path escape, future cutoff, or upstream readiness failure blocks the
release. A ready release is a research-delivery gate only and is not trade
authorization.

## Verification

The implementation is deterministic for fixed inputs and offline. It performs
no provider calls, reads no credentials, does not alter earlier artifacts, and
keeps `decision_ready=false` in both success and failure results.
