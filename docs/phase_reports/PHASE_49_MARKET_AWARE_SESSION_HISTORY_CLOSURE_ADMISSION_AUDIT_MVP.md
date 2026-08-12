# Phase 49: market-aware session history closure admission audit MVP

## Goal

Independently audit the Node48 admission JSON/report and all five evidence
inputs declared by the admission report.

## Contract

- Version: `market-aware-session-history-closure-admission-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-history-closure-admission`.
- Inputs: Node48 admission JSON/report, Node45 closure JSON/report, Node46
  Markdown/render report, Node47 render audit report, artifact root, and output
  directory.
- Output: `market_aware_session_history_closure_admission_audit_report.json`.

The audit validates seven controlled files, canonical self-hashes, Node48
input path/byte/SHA declarations, Node45–47 version and binding fields, state
and readiness consistency, UTF-8 Markdown, and all
`decision_ready=false` gates. Ready and stale/blocked chains remain literal;
the audit never grants trading or research meaning.

## Safety and scope

This node does not rerun, repair, render, or modify Node45–48, call DeepSeek,
read API keys, use network access, or add database, UI, scheduling, archive,
or trading services. It only audits existing files.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_closure_admission_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
