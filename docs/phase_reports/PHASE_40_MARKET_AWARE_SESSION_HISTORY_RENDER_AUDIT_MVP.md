# Phase 40: market-aware session history render independent audit MVP

## Goal

Independently verify the Node39 Markdown render and its report against the
Node37 history and Node38 history audit chain without regenerating any
artifact.

## Contract

- Version: `market-aware-session-history-render-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-history-render`.
- Inputs: history JSON, history report, history audit report, Markdown,
  render report, an artifact root, and an output directory.
- Output: `market_aware_session_history_render_audit_report.json`.

The audit records the five input paths, byte sizes, and SHA-256 values, plus
the history symbol, package count, first/last `as_of`, ready flags, render
status, issues, and a deterministic output SHA-256. It verifies the current
Node37/38/39 version and decision-gate fields, canonical Node37 and Node38
report hashes, Node38 history bindings, Node39 history/report/Markdown
bindings, declared relative paths, package literals, time ordering, and
render status relations. Markdown is checked as UTF-8 and must remain next to
the render report.

`audit_ready=true` means the artifact chain is internally consistent. It
does not mean the history is useful, investable, or authorized for trading.
Stale or blocked history states may audit successfully and remain unchanged;
`decision_ready` is always false.

## Safety and scope

This node does not regenerate Markdown, rerun Node37/38, re-audit packages,
interpret trends or returns, call DeepSeek, read API keys, use network access,
or add database, UI, scheduling, archive, or trading services. All inputs and
outputs must remain under the controlled artifact root; escaping or symlinked
paths fail closed.

## Verification

Coverage includes ready and blocked render chains, history/report/audit/
Markdown tampering, SHA and size mismatches, version and decision-gate
injection, summary and package-state mismatches, invalid JSON/UTF-8, CLI
behavior, deterministic output, and output-root boundaries.

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_render_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
