# Phase 38: market-aware session history independent audit MVP

## Goal

Independently verify a Node37 history manifest, its report, and every
referenced session package. This closes the consumer-side integrity loop for
the file-based history without creating a second package-audit implementation.

## Contract

- Version: `market-aware-session-history-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-history`.
- Inputs: history JSON, history report, history root, and an output directory.
- Output: `market_aware_session_history_audit_report.json`.

The audit validates history/report JSON and versions, `decision_ready=false`,
history/report issues and summary fields, relative package references, unique
package tuples, timezone-aware strictly increasing `as_of` values, and the
history's package count and boundary times. Each package is independently
rechecked through `audit_market_aware_session_package`; its actual manifest
SHA, symbol, status, readiness, freshness fields, time fields, and decision
gate must match the history entry. The session artifact is read only to
confirm the package's actual `freshness_ready` field.

The current Node37 report writes a deterministic report hash using the
canonical report payload with its own hash field set to `null`; the audit
accepts and verifies that existing semantic while also accepting the intended
history-manifest SHA binding when present. The audit report has its own
deterministic `output_sha256` using the same non-circular convention. Stale or
blocked packages may audit successfully and remain visibly blocked; audit
readiness never upgrades `session_ready` or `decision_ready`.

## Safety and scope

This node does not modify or rebuild Node37 history, auto-discover files, run
Node34–36, re-render diff output, use a database, add UI/scheduling/archive
services, call DeepSeek, read API keys, use network access, or make trend,
value, or trading judgements. Absolute, escaping, missing, duplicate, and
symlink-escaping references fail closed. Output outside `history-root` is
rejected without writing there. Every result keeps `decision_ready=false`.

## Verification

Coverage includes ready and stale histories, history/report/package tampering,
SHA and field mismatch, version and decision-gate injection, invalid JSON,
time and path failures, duplicate references, deterministic audit reports,
CLI behavior, and output-root boundaries.

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
