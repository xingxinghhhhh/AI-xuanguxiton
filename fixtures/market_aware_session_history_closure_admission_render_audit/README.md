# Market-aware session history closure admission render audit fixtures

Node51 independently audits the Node50 admission Markdown and render report.

```bash
python -m a_share_ai.cli audit-market-aware-session-history-closure-admission-render \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --admission-audit-report reports/session-history/history-closure-admission-audit/market_aware_session_history_closure_admission_audit_report.json \
  --markdown reports/session-history/history-closure-admission-render/market_aware_session_history_closure_admission.md \
  --render-report reports/session-history/history-closure-admission-render/market_aware_session_history_closure_admission_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission-render-audit
```
