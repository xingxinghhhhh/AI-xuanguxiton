# Phase 30: market-aware research session admission MVP

## Goal

Bind the reviewed `analysis-input-v2` release, the market-aware release replay,
and the existing `research-freshness-v1` audit into one read-only admission
report. A ready session means only that the research package is complete,
reviewed, internally chained, and fresh; it is not an investment or trading
decision.

## Contract

- Version: `market-aware-session-v1`.
- CLI: `python -m a_share_ai.cli build-market-aware-session`.
- Outputs: `market_aware_session.json` and
  `market_aware_session_report.json`.
- Required time inputs: timezone-aware `--evaluation-at` and explicit
  `--reference-at`.
- Time policy: `explicit-reference-v1`.

The session layer checks time ordering before invoking the existing freshness
audit:

1. `evaluation_at > reference_at` yields `TIME_IN_FUTURE`.
2. `release_as_of > evaluation_at` yields `RELEASE_AFTER_EVALUATION`.
3. Only a valid time ordering is passed to `research-freshness-v1`, so an
   uncovered calendar range yields `CALENDAR_UNKNOWN` rather than masking a
   time error.

The session is ready only when the release replay is ready, the review is
complete and passes its gate, the release and bundle hashes and paths agree,
the market context and relative-strength versions are present, and freshness
is `fresh`. Every result keeps `decision_ready=false`.

## Scope exclusions

This node does not modify the release, review, replay, or freshness core
contracts. It does not call DeepSeek, read API keys, collect market data,
generate automatic reviews, add trading fields, make investment judgements,
or implement a database, scheduler, reminder, or web UI.

## Verification

The offline tests cover a ready deterministic session, stale and unknown
calendar results, release-before-evaluation, future-reference ordering,
review-gate failure, replay hash tampering, artifact-root boundaries, and the
CLI. The acceptance gates are:

```text
python -m pytest -q tests/analysis/test_market_aware_session.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```

All outputs remain offline, read-only, deterministic for fixed inputs, and
fail closed on invalid upstream evidence.
