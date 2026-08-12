# Market-aware session history manifest audit fixtures

Node42 independently checks the Node41 manifest, manifest report, and six
declared files. It recomputes paths, byte counts, SHA-256 values, roles, and
readiness fields without rebuilding the manifest.

Run it with:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-manifest \
  --manifest reports/session-history/history-manifest/market_aware_session_history_manifest.json \
  --manifest-report reports/session-history/history-manifest/market_aware_session_history_manifest_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-manifest-audit
```

Tests create isolated manifest inputs and exercise the fail-closed contract.
