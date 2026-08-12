# Market-aware session history closure admission render fixtures

Node50 renders the Node48 admission and Node49 audit as a deterministic
Markdown view.

```bash
python -m a_share_ai.cli render-market-aware-session-history-closure-admission \
  --admission reports/session-history/history-closure-admission/market_aware_session_history_closure_admission.json \
  --admission-report reports/session-history/history-closure-admission/market_aware_session_history_closure_admission_report.json \
  --admission-audit-report reports/session-history/history-closure-admission-audit/market_aware_session_history_closure_admission_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission-render
```
