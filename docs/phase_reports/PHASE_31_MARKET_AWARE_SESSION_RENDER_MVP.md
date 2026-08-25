# Phase 31: market-aware session metadata report render MVP

## Goal

Turn the existing `market-aware-session-v1` admission snapshot and report into
a deterministic, read-only Markdown artifact that a person can read directly.
This node does not extend the session contract and does not infer upstream
paths that the session report does not contain.

## Contract

- Version: `market-aware-session-render-v1`.
- CLI: `python -m a_share_ai.cli render-market-aware-session`.
- Outputs: `market_aware_session.md` and
  `market_aware_session_render_report.json`.
- The render report is self-contained for the session time and freshness
  semantics and always includes `evaluation_at`, `freshness_status`, and
  `freshness_ready` copied exactly from the session on valid inputs.
- Every output keeps `decision_ready=false`.

The renderer proves only what the current session contract exposes:

- session/report JSON structure and matching core fields;
- actual SHA-256 values for the session and session report;
- the same-directory `research_freshness_report.json` and its declared SHA;
- `market-aware-session-v1`, `explicit-reference-v1`, time fields, status gates,
  and artifact-root boundaries;
- market-context and relative-strength version fields.

`release_manifest_sha256`, `bundle_sha256`, and `calendar_sha256` are rendered
as `declared_summary_only`. No release, replay, analysis, or market-summary
file path is invented or claimed to be validated.

## Blocked sessions and safety

A valid stale, calendar-unknown, or invalid-time session can be rendered as a
blocked Markdown report for auditability. It is never marked
`session_render_ready=true`. Malformed inputs, hash mismatches, inconsistent
fields, unsupported versions, and path escapes fail closed and produce only a
blocked render audit report when the output location itself is inside the
artifact root.

All external strings are escaped before insertion into Markdown. The output is
metadata-only: it does not copy analysis body text, call a provider, create a review,
make a market judgement, or authorize a transaction.

## Verification

The offline tests cover ready deterministic rendering, blocked stale rendering,
freshness hash tampering, output-root boundaries, metadata injection escaping,
and the CLI. The node is accepted only when these checks pass:

```text
python -m pytest -q tests/analysis/test_market_aware_session_renderer.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```

No DeepSeek request, credential read, network call, upstream mutation, trading
field, database, scheduler, reminder, or web UI is added.
