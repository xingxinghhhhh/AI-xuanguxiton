# Phase 50: market-aware session history closure admission render MVP

## Goal

Render the Node48 admission and Node49 independent audit into a deterministic,
read-only Markdown view without adding research or trading meaning.

## Contract

- Version: `market-aware-session-history-closure-admission-render-v1`.
- CLI: `python -m a_share_ai.cli render-market-aware-session-history-closure-admission`.
- Inputs: Node48 admission JSON/report, Node49 admission audit report,
  artifact root, and output directory.
- Outputs: `market_aware_session_history_closure_admission.md` and
  `market_aware_session_history_closure_admission_render_report.json`.
- The render report records the three input paths and SHA-256 values, summary
  fields, `markdown_sha256`, `output_sha256`, and `decision_ready=false`.

## Safety and scope

The renderer does not modify or rebuild Node48/49, does not re-audit upstream
evidence, does not use the network, DeepSeek, API keys, database, UI,
scheduling, or trading services. Ready, stale, and blocked states remain
literal; only ready admission plus ready audit may set `render_ready=true`.
All Markdown values are escaped before rendering.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_closure_admission_renderer.py
python -m pytest -q
python -m ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
