# Phase 42: market-aware session history manifest independent audit MVP

## Goal

Independently verify the Node41 manifest, its report, and all six declared
artifact files without rebuilding or modifying any upstream artifact.

## Contract

- Version: `market-aware-session-history-manifest-audit-v1`.
- CLI: `python -m a_share_ai.cli audit-market-aware-session-history-manifest`.
- Inputs: Node41 manifest, Node41 manifest report, artifact root, and output
  directory.
- Output: `market_aware_session_history_manifest_audit_report.json`.

The audit checks the manifest/report versions, canonical report self-hash,
manifest SHA binding, `decision_ready=false`, exact six artifact roles,
controlled relative paths, actual file SHA-256 values and byte counts, symbol,
package count, time bounds, and readiness fields. The output reports each
artifact's declared and recomputed values and has a deterministic self-hash.

`audit_ready=true` means only that the evidence manifest is internally
consistent. Blocked or stale history state is preserved literally and is not
upgraded to a research or trading decision.

## Safety and scope

This node does not rebuild Node41, rerun Node37–40, re-audit history or
packages, call DeepSeek, read API keys, use network access, or add database,
UI, scheduling, archive, or trading services. All paths must remain under the
controlled artifact root; escaping or symlink-escaping paths fail closed.

## Verification

```text
python -m pytest -q tests/analysis/test_market_aware_session_history_manifest_audit.py
python -m pytest -q
ruff check src tests
python -m compileall -q src tests
python -m pip install -e . --no-deps
git diff --check
```
