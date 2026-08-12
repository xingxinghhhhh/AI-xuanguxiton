# Phase 39: market-aware session history readable render MVP

## Goal

Provide a deterministic, offline Markdown view of the Node37 history and
Node38 independent audit without changing the history, package, or decision
logic.

## Contract

- Version: `market-aware-session-history-render-v1`.
- CLI: `python -m a_share_ai.cli render-market-aware-session-history`.
- Inputs: history JSON, history report, history audit report, history root,
  and an output directory.
- Outputs: `market_aware_session_history.md` and
  `market_aware_session_history_render_report.json`.

The renderer verifies the three input versions, the `decision_ready=false`
gate, history/report summary agreement, audit-to-input SHA-256 bindings,
relative paths, package references, and timezone-aware timestamps. The
Markdown presents the chronological package status, literal times, readiness
flags, package hashes, and referenced paths. Values are escaped for Markdown
and HTML-sensitive text is rendered as text rather than markup.

`render_ready` is true only when both `history_ready` and the independent
history `audit_ready` flag are true. Stale or blocked histories can still be
rendered for inspection, but their blocked state is preserved. The render
report records actual input paths, byte sizes, SHA-256 values, summary
fields, issues, and the Markdown SHA-256. All outputs keep
`decision_ready=false`.

## Safety and scope

This node is a read-only chronological status view. It does not infer market
trends, research quality, returns, investment value, or trading authorization;
it does not call DeepSeek, use a database, add UI or scheduling services, or
read API keys. Input and output paths must remain under `history-root`, and
invalid or escaping references fail closed without writing outside that root.

## Verification

Coverage includes deterministic ready rendering, stale/blocked audit display,
input tampering and SHA mismatch, invalid JSON, version and decision-gate
injection, path-chain validation, Markdown escaping, CLI behavior, and output
boundaries.

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_renderer.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
