# Phase 13: Readable analysis report and evidence navigation MVP

## Scope

This node renders a successful `analysis-report-v1` JSON result as deterministic
Markdown. It exposes the symbol, cutoff, provider/model, eight sections, claims,
citations, evidence paths and SHA-256 values, and an explicit non-trading safety
boundary. It performs no network request and reads no credentials.

## Implementation

- `renderer.py` verifies the analysis output SHA recorded by
  `research_analysis_report.json`, validates readiness flags, rechecks evidence
  paths and hashes under `input-root`, and renders fixed section order.
- Claim text and metadata are escaped so model output cannot inject Markdown
  headings, links, HTML, or code fences.
- `render-analysis` writes `research_analysis.md` and
  `analysis_render_report.json`; invalid inputs produce a non-ready audit report
  and no valid Markdown output.

## Verification

Tests cover deterministic repeated rendering, CLI output, analysis SHA mismatch,
evidence hash mismatch, path escape, readiness/decision gates, and Markdown
injection text. The renderer consumes existing JSON artifacts only and does not
call any AI provider.
