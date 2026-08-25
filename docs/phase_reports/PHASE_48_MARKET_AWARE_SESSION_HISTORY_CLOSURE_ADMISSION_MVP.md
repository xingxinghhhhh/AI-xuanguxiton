# Phase 48: market-aware session history closure admission MVP

## Goal

Aggregate the Node45 closure, Node46 render, and Node47 render audit into one
deterministic read-only admission summary for offline consumers.

## Contract

- Version: `market-aware-session-history-closure-admission-v1`.
- CLI: `python -m a_share_ai.cli build-market-aware-session-history-closure-admission`.
- Inputs: Node45 closure JSON/report, Node46 Markdown/render report, Node47
  render audit report, artifact root, and output directory.
- Outputs: `market_aware_session_history_closure_admission.json` and
  `market_aware_session_history_closure_admission_report.json`.

The admission validates all five input files, canonical report self-hashes,
actual paths, byte counts and SHA-256 values, Node45/46/47 versions and
bindings, UTF-8 Markdown, status/readiness consistency, and the literal
`decision_ready=false` gate. Only a ready closure with ready rendering and a
ready Node47 audit yields `admission_ready=true`; stale or blocked chains are
reported literally and never admitted.

## Safety and scope

This node does not rerun or modify Node45–47, call DeepSeek, read API keys,
use network access, or add database, UI, scheduling, archive, or trading
services. `admission_ready` only means that the existing offline evidence
chain passed its declared gates; it is not a research or trading conclusion.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_closure_admission.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
