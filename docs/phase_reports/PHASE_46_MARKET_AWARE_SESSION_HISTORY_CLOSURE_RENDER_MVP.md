# Phase 46: market-aware session history closure render MVP

## Goal

Render the Node45 closure and closure report as a deterministic, read-only
Markdown view without changing the closure contract or rereading the five
upstream evidence files.

## Contract

- Version: `market-aware-session-history-closure-render-v1`.
- CLI: `python -m a_share_ai.cli render-market-aware-session-history-closure`.
- Inputs: Node45 closure JSON, closure report JSON, artifact root, and output
  directory.
- Outputs: `market_aware_session_history_closure.md` and
  `market_aware_session_history_closure_render_report.json`.

The renderer validates Node45 JSON versions, canonical self-hashes, the
closure SHA binding, controlled input metadata, state and issue consistency,
and `decision_ready=false`. It renders only literal closure fields, evidence
hashes, and issue messages with Markdown/HTML/link/code-fence escaping.
`ready` closures produce `render_ready=true`; valid `blocked` or `stale`
closures remain visibly blocked and never become ready.

## Safety and scope

This node does not modify Node45 or any upstream artifact, call DeepSeek,
read API keys, use network access, or add database, UI, scheduling, archive,
or trading services. The Markdown is an evidence-chain view only and never
represents a research conclusion or trading authorization.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_closure_renderer.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
