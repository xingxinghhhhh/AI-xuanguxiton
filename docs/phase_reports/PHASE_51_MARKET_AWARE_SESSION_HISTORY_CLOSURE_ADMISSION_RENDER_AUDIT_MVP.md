# Phase 51: market-aware session history closure admission render audit MVP

## Goal

Independently audit the Node50 admission Markdown and render report, including
their Node48 admission and Node49 audit bindings, without rebuilding the view.

## Contract

- Version: `market-aware-session-history-closure-admission-render-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-history-closure-admission-render`.
- Inputs: Node48 admission JSON/report, Node49 admission audit report, Node50
  Markdown/render report, artifact root, and output directory.
- Output: `market_aware_session_history_closure_admission_render_audit_report.json`.
- The report records all five input paths, actual byte/SHA values, summary
  readiness fields, `markdown_sha256`, and its own `output_sha256`.

## Safety and scope

Node51 does not rerender Node50, rerun Node48/49, or modify existing artifacts.
It does not use the network, DeepSeek, API keys, database, UI, scheduling, or
trading services. Ready, stale, and blocked states remain literal; every
result keeps `decision_ready=false`.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_closure_admission_render_audit.py
python -m pytest -q
python -m ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
