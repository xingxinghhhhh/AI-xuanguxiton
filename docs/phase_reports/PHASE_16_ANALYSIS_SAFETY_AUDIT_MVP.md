# Phase 16: Analysis output action-language safety audit MVP

## Scope

This node adds the deterministic `analysis-safety-v1` audit after the
decision-input snapshot. It checks the final structured claims and rendered
Markdown for language that could be interpreted as a trade instruction, while
leaving all existing readiness states unchanged.

## Implementation

- `safety.py` revalidates the decision-input snapshot/report, analysis,
  analysis-report, render report, quality report, bundle and rendered Markdown
  hashes, paths, versions, symbol, cutoff, and readiness gates.
- Fixed, explainable rules cover action terms, execution prices, positions,
  recommendations, unexpected decision fields, and raw Markdown/HTML/link/code
  markup that could hide the same expressions.
- `audit-analysis-safety` writes `analysis_safety_report.json` with stable rule
  IDs, source fields, sections, matched terms, chain hashes, and
  `safety_ready`; `decision_ready` remains false in every result.
- The audit is offline and does not call a provider, read credentials, or
  evaluate factual correctness or investment merit.

## Verification

Tests cover a valid deterministic pass, CLI output, action and execution terms,
unknown decision fields, hidden Markdown markup, and tampered analysis input.
The real DeepSeek analysis -> render -> quality -> decision-input chain was
replayed offline and passed the safety audit.
