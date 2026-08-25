# Phase 35: market-aware session package diff render MVP

## Goal

Render the Node34 structural package diff as deterministic, safe Markdown for
human inspection. This adds a readable handoff without changing the Node34
JSON contract or re-auditing package artifacts.

## Contract

- Version: `market-aware-session-package-diff-render-v1`.
- CLI: `python -m a_share_ai.cli render-market-aware-session-package-diff`.
- Inputs: `market_aware_session_package_diff.json`, its report, and an
  `input-root`.
- Outputs: `market_aware_session_package_diff.md` and
  `market_aware_session_package_diff_render_report.json`.

The renderer verifies both input files are JSON objects under `input-root`,
checks the Node34 diff version, checks the report's SHA against the actual diff
bytes, requires `decision_ready=false`, and validates the current diff's
literal field and five-role artifact structures. It records relative input
paths, actual SHA-256 values, symbol and as-of values, comparison status,
render status, issues, and Markdown SHA-256.

Ready diffs render field changes and artifact path/size/SHA changes in stable
tables. A blocked diff can render its literal issue while remaining
`render_ready=false`; rendering never upgrades comparison readiness. All
Markdown values are escaped for table pipes, code fences, headings, links,
HTML, and line breaks. The document explicitly states that it is not a value
judgement, prediction, investment recommendation, or trading instruction.

## Safety and scope

The renderer does not rebuild or re-audit Node34 packages, read complete
analysis text, repair JSON, infer upstream paths, call DeepSeek, read API keys,
use network access, or modify review/release artifacts. An output directory
outside `input-root` is rejected without writing there. Every result keeps
`decision_ready=false`.

## Verification

Coverage includes changed and partially unchanged diffs, blocked diffs, input
SHA/version/decision-gate/JSON failures, Markdown and HTML/link/code-fence
escaping, deterministic repeated rendering, CLI behavior, and output-root
boundaries.

```text
python -m pytest -q tests/analysis/test_market_aware_session_package_diff_renderer.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
