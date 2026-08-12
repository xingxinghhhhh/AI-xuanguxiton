# Market-aware session history closure admission audit fixtures

Node49 audits the Node48 admission summary and its five declared evidence
inputs without rebuilding or rendering them.

```bash
python -m a_share_ai.cli audit-market-aware-session-history-closure-admission \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --markdown reports/session-history/history-closure-render/market_aware_session_history_closure.md \
  --render-report reports/session-history/history-closure-render/market_aware_session_history_closure_render_report.json \
  --render-audit-report reports/session-history/history-closure-render-audit/market_aware_session_history_closure_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission-audit
```
