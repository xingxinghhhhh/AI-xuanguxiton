# Phase 52: market-aware session history final receipt MVP

## Goal

Aggregate the Node48 admission, Node50 readable render, and Node51 independent
render audit into one deterministic final evidence-chain receipt.

## Contract

- Version: `market-aware-session-history-final-receipt-v1`.
- CLI: `python -m a_share_ai.cli build-market-aware-session-history-final-receipt`.
- Inputs: Node48 admission JSON/report, Node50 render report, Node51 render
  audit report, artifact root, and output directory.
- Outputs: `market_aware_session_history_final_receipt.json` and
  `market_aware_session_history_final_receipt_report.json`.
- The receipt records readiness, status, summary metadata, four input SHA-256
  values, issues, and `decision_ready=false`.

## Safety and scope

Node52 does not execute, rebuild, render, or re-audit Nodes45–51. It does not
use the network, DeepSeek, API keys, database, UI, scheduling, or trading
services. It only aggregates already audited bindings; stale and blocked
states remain literal and cannot become `receipt_ready=true`.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_final_receipt.py
python -m pytest -q
python -m ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
