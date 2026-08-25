# Phase 45: market-aware session history closure MVP

## Goal

Produce a deterministic closure report for the Node41 manifest, Node42
manifest audit, Node43 render report, and Node44 render audit chain.

## Contract

- Version: `market-aware-session-history-closure-v1`.
- CLI: `python -m a_share_ai.cli build-market-aware-session-history-closure`.
- Inputs: five existing JSON reports, artifact root, and output directory.
- Outputs: `market_aware_session_history_closure.json` and
  `market_aware_session_history_closure_report.json`.

The closure validates versions, decision gates, canonical report hashes, all
declared path and SHA bindings, symbol/package/time summary, and the required
Node42/Node44 audit-ready gates. The closure JSON records literal readiness
and input SHA values; the report records every input's controlled path, byte
count, SHA-256, status, closure state, and a deterministic self-hash.

Closure is an evidence-chain status only. It never upgrades stale or blocked
history and always keeps `decision_ready=false`.

## Safety and scope

This node does not rerun Node37–44, copy or repair artifacts, call DeepSeek,
read API keys, use network access, or add database, UI, scheduling, archive,
or trading services. All paths remain under the controlled artifact root.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_closure.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
