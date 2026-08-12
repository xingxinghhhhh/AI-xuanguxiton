# Market-aware session history manifest fixtures

Node41 records the six Node37–40 artifacts as fixed evidence roles. It stores
only controlled relative paths, byte counts, SHA-256 values, and literal
readiness fields.

Build it with:

```bash
python -m a_share_ai.cli build-market-aware-session-history-manifest \
  --history reports/session-history/history/market_aware_session_history.json \
  --history-report reports/session-history/history/market_aware_session_history_report.json \
  --history-audit-report reports/session-history/history-audit/market_aware_session_history_audit_report.json \
  --markdown reports/session-history/history-render/market_aware_session_history.md \
  --render-report reports/session-history/history-render/market_aware_session_history_render_report.json \
  --render-audit-report reports/session-history/history-render-audit/market_aware_session_history_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest
```

Tests create isolated upstream artifacts and verify the manifest chain.
