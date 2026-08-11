# Phase 20: Research release diff MVP

## Scope

This node compares two ready, single-stock `research-release-v1` packages and
records literal structural differences. It is intended for daily inspection
of what changed between two published research packages. It does not judge
whether a change is favorable or unfavorable, predict the future, or generate
an investment signal.

## Contract

The output version is `research-release-diff-v1`. A comparison is ready only
when both releases are valid, their manifests and reports match their actual
SHA-256 values, all referenced files are under their artifact roots, the
symbol matches, and the current `as_of` is later than the previous one.

The diff classifies claim IDs as `unchanged`, `added`, `removed`, or `changed`.
For changed claims it reports only literal changes to `kind`, `text`,
`citation_ids`, or `observed_dates`. It separately reports evidence and
artifact mapping changes, plus current/previous risk and unknown claim IDs.

## CLI

```bash
python -m a_share_ai.cli compare-research-releases \
  --previous-manifest reports/previous/release/research_release_manifest.json \
  --previous-report reports/previous/release/research_release_report.json \
  --current-manifest reports/current/release/research_release_manifest.json \
  --current-report reports/current/release/research_release_report.json \
  --previous-artifact-root reports/previous \
  --current-artifact-root reports/current \
  --output-dir reports/current/release-diff
```

The command writes `research_release_diff.json` and
`research_release_diff_report.json`. Invalid SHA-256 values, path escapes,
symbol mismatches, time reversal, incomplete review records, unsupported
decision fields, or unverified evidence references fail closed. Both success
and failure outputs keep `decision_ready=false`.

## Verification

The comparison is deterministic for fixed inputs and offline. It performs no
provider calls, reads no credentials, never edits either release, and only
describes literal structure. It does not produce trend, score, recommendation,
or transaction output.
