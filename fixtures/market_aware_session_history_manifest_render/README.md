# Market-aware session history manifest render fixtures

Node43 renders the Node41 manifest and Node42 audit report as a safe Markdown
evidence table. It does not copy artifact contents or rerun any audit.

Run it with:

```bash
python -m a_share_ai.cli render-market-aware-session-history-manifest \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --audit-report reports/session-history/history-manifest-audit/market_aware_session_history_manifest_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest-render
```
