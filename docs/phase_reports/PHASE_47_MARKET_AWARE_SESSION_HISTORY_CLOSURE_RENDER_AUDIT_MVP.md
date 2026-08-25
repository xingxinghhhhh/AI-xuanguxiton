# Phase 47: market-aware session history closure render audit MVP

## Goal

Independently audit the Node46 closure Markdown and render report against the
Node45 closure and closure report, including the declared Markdown SHA.

## Contract

- Version: `market-aware-session-history-closure-render-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-history-closure-render`.
- Inputs: Node45 closure JSON/report, Node46 Markdown/render report, artifact
  root, and output directory.
- Output: `market_aware_session_history_closure_render_audit_report.json`.

The audit validates versions, canonical self-hashes, controlled paths, actual
SHA-256 values, UTF-8 Markdown, Node45/46 state and metadata bindings, and the
Node46 `markdown_sha256` binding. It does not re-render Markdown. Ready,
blocked, and stale states remain literal and always keep
`decision_ready=false`.

## Safety and scope

This node does not modify Node45 or Node46, rerun upstream builders, call
DeepSeek, read API keys, use network access, or add database, UI, scheduling,
archive, or trading services. The audit is a read-only evidence check.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_closure_render_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
