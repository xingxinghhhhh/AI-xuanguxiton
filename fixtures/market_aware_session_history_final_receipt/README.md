# Market-aware session history final receipt fixtures

Node52 aggregates the audited admission and render chain into one final JSON
receipt.

```bash
python -m a_share_ai.cli build-market-aware-session-history-final-receipt \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --render-report reports/session-history/history-closure-admission-render/market_aware_session_history_closure_admission_render_report.json \
  --render-audit-report reports/session-history/history-closure-admission-render-audit/market_aware_session_history_closure_admission_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/final-receipt
```
