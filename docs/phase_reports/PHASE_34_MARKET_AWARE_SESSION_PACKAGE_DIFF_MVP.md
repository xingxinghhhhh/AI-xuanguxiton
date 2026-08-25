# Phase 34: market-aware session package structural diff MVP

## Goal

Compare two independently audited market-aware session packages and make
literal changes in the session admission chain traceable. The diff reports
structure only: it does not judge whether a change is good, predict prices, or
produce a trading conclusion.

## Contract

- Version: `market-aware-session-package-diff-v1`.
- CLI: `python -m a_share_ai.cli compare-market-aware-session-packages`.
- Inputs: previous/current package manifest, package report, and artifact root.
- Outputs: `market_aware_session_package_diff.json` and
  `market_aware_session_package_diff_report.json`.

Both sides must pass the Node33 independent package audit. The symbols must
match and `current.as_of` must be strictly later than `previous.as_of`. The
comparison then reports literal `unchanged` or `changed` values for the
actual session/package fields, including session status/readiness, freshness,
review and research-release gates, market-context and relative-strength
versions, time fields, package/session versions, and the decision gate.

Each of the five fixed artifact roles is compared by its declared relative
path, byte size, and SHA-256. A changed artifact means only that one of those
literal descriptors changed; the diff does not infer content semantics.
The diff keeps `decision_ready=false` and uses a deterministic self-hash
convention: its `output_sha256` covers the canonical diff payload with that
field set to `null`; the companion report's `output_sha256` covers the final
diff JSON.

## Failure and safety behavior

Invalid or tampered packages, audit failures, symbol mismatch, time-order
failure, path escape, or decision-gate injection fail closed and produce a
non-ready report. The comparison never modifies Node30–33 artifacts or any
upstream release, review, replay, freshness, provider, or trading module.
Node33 audit reports are written only to temporary directories under their
respective artifact roots and removed after each side is validated.

## Scope exclusions

This node does not rebuild packages, repair artifacts, infer missing upstream
paths, call DeepSeek, read API keys, use network access, add a database,
archive service, scheduler, web UI, review workflow, trading logic, or
semantic good/bad analysis.

## Verification

Coverage includes two valid packages, deterministic repeated comparisons,
session status and version changes, artifact descriptor changes, symbol and
`as_of` order failures, package audit failure, tampering, decision-gate
injection, CLI success and required-argument validation.

```text
python -m pytest -q tests/analysis/test_market_aware_session_package_diff.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
