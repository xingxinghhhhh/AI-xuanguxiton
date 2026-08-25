# Phase 32: market-aware session immutable package manifest MVP

## Goal

Create a deterministic, portable audit manifest for the current session and
its rendered metadata. The package makes handoff and later offline verification
explicit without changing the session or renderer contracts.

## Contract

- Version: `market-aware-session-package-v1`.
- CLI: `python -m a_share_ai.cli build-market-aware-session-package`.
- Outputs: `market_aware_session_package.json` and
  `market_aware_session_package_report.json`.

The package has five fixed artifact roles:

1. `session`
2. `session_report`
3. `freshness_report`
4. `session_markdown`
5. `session_render_report`

Each entry contains only a relative path under `artifact-root`, the actual
SHA-256, the byte size, and its fixed role. The package also carries the
symbol, cutoff times, status, readiness, and `decision_ready=false` summary.

## Validation and safety

The builder reads each input once, validates JSON and the session/render chains,
checks the freshness declaration against the actual same-directory freshness
report, and rejects missing files, hash changes, field mismatches, invalid
versions, path escapes, and symlink escapes. It never treats the session's
release, bundle, or calendar hash summaries as locatable upstream artifacts.

A valid blocked session can produce `package_ready=true` while retaining
`session_ready=false`; this is an auditable package, not a readiness override.
Invalid input produces `package_ready=false` and no ready package manifest.

## Scope exclusions

This node does not modify the session, freshness, renderer, release, review, or
replay modules. It does not copy analysis body text or raw market data, call
DeepSeek, read credentials, add an archive service, add a database, scheduler,
reminder, web UI, or transaction field.

## Verification

Coverage includes ready and blocked packages, field mismatch, freshness and
Markdown/render-report tampering, missing/invalid inputs, output-root bounds,
relative-path output, CLI behavior, deterministic package hashes, and symlink
escape handling where the operating system permits symlink creation.

```text
python -m pytest -q tests/analysis/test_market_aware_session_package.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
