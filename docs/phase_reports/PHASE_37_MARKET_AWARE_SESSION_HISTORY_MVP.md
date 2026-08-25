# Phase 37: market-aware session history manifest MVP

## Goal

Compose multiple explicitly supplied, independently audited market-aware
session packages into one deterministic, file-based history manifest. This
provides chronological traceability without introducing a database or file
auto-discovery.

## Contract

- Version: `market-aware-session-history-v1`.
- CLI: `python -m a_share_ai.cli build-market-aware-session-history`.
- Input: a `session_history_spec.json`, a `history-root`, and an output
  directory.
- Outputs: `market_aware_session_history.json` and
  `market_aware_session_history_report.json`.

The spec has exactly `history_version` and `packages`. It contains at least
two package references, each with exactly `manifest_path`, `report_path`, and
`artifact_root`, all normalized relative paths under `history-root`. The spec
file itself may be supplied as an absolute CLI path only when it resolves
inside `history-root`.

For every package the builder reuses
`audit_market_aware_session_package`; it does not copy Node33 validation
logic. A history becomes ready only when every package audits successfully,
all symbols match, and the input package order has strictly increasing,
timezone-aware `as_of` values. The output preserves each package's relative
references, package SHA, audit state, session status/readiness, freshness
state, time fields, symbol, and `decision_ready=false`.

Blocked or stale sessions may be included in a ready history, but their
`session_ready=false` and freshness status remain unchanged. Invalid specs,
duplicates, path escapes, audit failures, symbol mismatch, or time reversal
fail closed. The history report's `output_sha256` covers the history JSON;
its own deterministic `output_sha256` is computed from a canonical payload
with that field set to `null`.

## Safety and scope

This node does not modify or rebuild packages, run Node34 diffs, render or
audit diff outputs, auto-discover files, use a database, add scheduling,
alerts, UI, trading interfaces, network access, DeepSeek calls, or API-key
reads. An output directory outside `history-root` is rejected without writing
there. All results keep `decision_ready=false`.

## Verification

Coverage includes ready deterministic histories, stale-session preservation,
symbol mismatch, duplicate/reversed `as_of`, package audit failure, too few
packages, unknown spec fields, duplicate/absolute references, CLI behavior,
output-root boundaries, and required-argument validation.

```text
python -m pytest -q tests/analysis/test_market_aware_session_history.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
