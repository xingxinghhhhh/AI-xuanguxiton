# Phase 43: market-aware session history manifest readable render MVP

## Goal

Render the Node41 evidence manifest and Node42 independent audit into a
deterministic Markdown handoff without changing or re-auditing the inputs.

## Contract

- Version: `market-aware-session-history-manifest-render-v1`.
- CLI: `python -m a_share_ai.cli render-market-aware-session-history-manifest`.
- Inputs: Node41 manifest, Node41 manifest report, Node42 audit report,
  artifact root, and output directory.
- Outputs: `market_aware_session_history_manifest.md` and
  `market_aware_session_history_manifest_render_report.json`.

The renderer verifies the input versions, canonical report hashes, SHA/path
bindings, exact six roles, summary fields, ready state, and
`decision_ready=false`. Markdown shows only the literal manifest version,
symbol, package count, time bounds, six artifact roles, paths, byte counts,
SHA-256 values, issues, and audit state. Values are escaped for Markdown and
HTML-sensitive text is displayed as text.

## Safety and scope

This node does not rebuild Node41, rerun Node42, re-audit any artifact,
interpret market trends or returns, call DeepSeek, read API keys, use network
access, or add database, UI, scheduling, archive, or trading services. All
inputs and outputs stay under the controlled artifact root and every result
keeps `decision_ready=false`.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_manifest_renderer.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
