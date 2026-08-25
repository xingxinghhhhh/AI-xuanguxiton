# Phase 44: market-aware session history manifest render audit MVP

## Goal

Independently verify the Node43 Markdown and render report against the Node41
manifest, Node41 report, and Node42 audit report without re-rendering or
re-auditing the six upstream artifacts.

## Contract

- Version: `market-aware-session-history-manifest-render-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-history-manifest-render`.
- Inputs: Node41 manifest, Node41 manifest report, Node42 manifest audit
  report, Node43 Markdown, Node43 render report, artifact root, and output
  directory.
- Output: `market_aware_session_history_manifest_render_audit_report.json`.

The audit checks the five input versions and decision gates, canonical
Node41/42 report hashes, Node41/42/43 SHA bindings, controlled relative paths,
UTF-8 Markdown, six artifact roles, literal summary fields, ready states, and
the Markdown SHA. The output records actual input paths and hashes and uses a
deterministic self-hash.

`audit_ready=true` means only that the render chain is internally consistent;
all results keep `decision_ready=false` and do not interpret the history.

## Safety and scope

This node does not regenerate Node43, rebuild Node41, rerun Node42, re-audit
the six artifacts, call DeepSeek, read API keys, use network access, or add
database, UI, scheduling, archive, or trading services. Paths outside the
controlled artifact root fail closed.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_manifest_render_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
