# Phase 41: market-aware session history immutable manifest MVP

## Goal

Create a deterministic, portable evidence manifest for the six Node37–40
history artifacts without copying their contents or rerunning any upstream
builder or audit.

## Contract

- Version: `market-aware-session-history-manifest-v1`.
- CLI: `python -m a_share_ai.cli build-market-aware-session-history-manifest`.
- Inputs: history JSON, history report, history audit report, Markdown, render
  report, render audit report, an artifact root, and an output directory.
- Outputs: `market_aware_session_history_manifest.json` and
  `market_aware_session_history_manifest_report.json`.

The manifest records the six fixed artifact roles, controlled relative paths,
byte counts, and actual SHA-256 values. It also preserves symbol, package
count, time bounds, history/render readiness, `render_audit_ready`, issues,
and `decision_ready=false`. The report records the manifest path and SHA,
controlled artifact root, status, readiness, issues, and a deterministic
self-hash.

`manifest_ready=true` requires the Node40 render audit to be
`audit_ready=true`. Stale or blocked upstream state is recorded literally and
is never upgraded into a research or trading decision.

## Safety and scope

This node does not rebuild or repair history, rerun Node37–40, re-audit
packages, copy source artifacts, call DeepSeek, read API keys, use network
access, or add database, UI, scheduling, archive, or trading services. Every
input and output path must remain under the controlled artifact root; invalid,
escaping, or symlink-escaping paths fail closed.

## Verification

Coverage includes ready manifests, upstream audit blocking, input tampering,
decision-gate injection, summary mismatches, deterministic repeated output,
CLI behavior, and output-root boundaries.

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_manifest.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
