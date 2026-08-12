# Phase 33: market-aware session package independent audit MVP

## Goal

Independently verify a `market-aware-session-package-v1` handoff without
re-running the package builder or trusting its report. The audit closes the
consumer-side integrity loop while preserving the distinction between package
integrity and session decision readiness.

## Contract

- Version: `market-aware-session-package-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-package`.
- Output: `market_aware_session_package_audit_report.json`.
- Inputs: the package manifest, package report, and one artifact root.

The audit re-reads the package and package report, verifies their version and
summary fields, checks the package report's manifest SHA, and requires exactly
five fixed roles: `session`, `session_report`, `freshness_report`,
`session_markdown`, and `session_render_report`. Every declared path must stay
relative to `artifact-root`; the resolved file must exist and match both its
declared byte size and SHA-256.

It then independently validates the session/report/freshness/render SHA and
field relationships, same-directory freshness placement, relative renderer
paths, timezone-aware evaluation/reference times, and
`evaluation_at <= reference_at`. The audit report itself is deterministic:
`output_sha256` covers the canonical report payload with that field set to
`null`, avoiding a circular hash.

## Readiness semantics

`audit_ready=true` means only that the package and its five declared artifacts
are internally consistent. A blocked or stale session may therefore audit
successfully with `package_ready=true` and `session_ready=false`; the audit
never changes a readiness gate and always keeps `decision_ready=false`.

Invalid JSON, versions, role sets, paths, sizes, hashes, time ordering, or
cross-file relations fail closed and produce an audit report when the output
directory is inside `artifact-root`. An output directory outside that root is
rejected without writing there.

## Scope exclusions

This node does not modify the Node30 session, Node31 renderer, or Node32
package builder. It does not rebuild packages, infer release/bundle/calendar
paths, call DeepSeek, read API keys, use network access, or add a database,
archive service, scheduler, UI, trading action, review workflow, or decision.

## Verification

Coverage includes ready and blocked packages, deterministic audit reports,
artifact tampering, package/report mismatch, duplicate or unknown roles,
absolute and escaping paths, future evaluation time, CLI behavior, output-root
boundaries, and symlink escapes where the operating system permits symlink
creation.

```text
python -m pytest -q tests/analysis/test_market_aware_session_package_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
