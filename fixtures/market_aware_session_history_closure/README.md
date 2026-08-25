# Market-aware session history closure fixtures

Node45 closes the existing Node41–44 evidence chain into two deterministic
JSON files. It records only paths, SHA-256 values, times, and literal status;
it does not rerun any builder, audit, or renderer.

Run it with:

```bash
python -m a_share_ai.cli build-market-aware-session-history-closure \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --manifest-audit-report reports/session-history/history-manifest-audit/market_aware_session_history_manifest_audit_report.json \
  --render-report reports/session-history/history-manifest-render/market_aware_session_history_manifest_render_report.json \
  --render-audit-report reports/session-history/history-manifest-render-audit/market_aware_session_history_manifest_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure
```
