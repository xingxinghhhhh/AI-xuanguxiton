# Market-aware session history manifest render audit fixtures

Node44 independently checks the Node43 Markdown/render report chain against
Node41 and Node42. It recomputes input SHA-256 values and does not re-render
or re-audit any upstream artifact.

Run it with:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-manifest-render \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --manifest-audit-report reports/session-history/history-manifest-audit/market_aware_session_history_manifest_audit_report.json \
  --markdown reports/session-history/history-manifest-render/market_aware_session_history_manifest.md \
  --render-report reports/session-history/history-manifest-render/market_aware_session_history_manifest_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest-render-audit
```
