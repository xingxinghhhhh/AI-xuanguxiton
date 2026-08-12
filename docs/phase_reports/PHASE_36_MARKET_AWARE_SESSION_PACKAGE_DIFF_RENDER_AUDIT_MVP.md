# Phase 36: market-aware session package diff render audit MVP

## Goal

Independently verify the four artifacts emitted by Node35 without re-rendering
or re-comparing them. This closes the consumer-side integrity loop for the
human-readable package diff.

## Contract

- Version: `market-aware-session-package-diff-render-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-package-diff-render`.
- Inputs: Node34 diff JSON, diff report, Node35 Markdown, render report, and
  one artifact root.
- Output: `market_aware_session_package_diff_render_audit_report.json`.

The audit reads the four declared files, verifies JSON/UTF-8 readability,
versions, `decision_ready=false`, and actual SHA-256/byte sizes. It checks the
Node34 diff report's SHA against the diff, Node35 render report SHA fields
against the diff/report/Markdown bytes, and binds symbol, previous/current
`as_of`, comparison readiness, render readiness, issues, relative paths, and
fixed output filenames. The Markdown path is supplied explicitly and must be
the fixed filename next to the render report; no upstream path is inferred.

`audit_ready=true` means only that the Node35 render artifacts are internally
consistent. A valid blocked diff render may therefore audit successfully with
`comparison_ready=false` and `render_ready=false`. The audit report itself is
deterministic: `output_sha256` covers the canonical report payload with that
field set to `null`.

## Safety and scope

The audit does not re-run Node34, re-audit Node33, re-render Markdown, modify
any Node35 input/output, read analysis body text, call DeepSeek, read API keys,
use network access, or add review/release/trading behavior. Absolute,
escaping, missing, duplicate, or symlink-escaping inputs fail closed. An
output directory outside `artifact-root` is rejected without writing there.
Every result keeps `decision_ready=false`.

## Verification

Coverage includes ready and blocked render audits, all four input tamper
cases, SHA/size and field binding failures, version/status/decision-gate
failures, invalid JSON, path and time/symbol mismatch, deterministic audit
hashes, CLI behavior, and output-root boundaries.

```text
python -m pytest -q tests/analysis/test_market_aware_session_package_diff_render_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
