# Market-aware session history closure admission fixtures

Node48 aggregates the Node45 closure, Node46 render, and Node47 render audit
into a single offline admission summary.

```bash
python -m a_share_ai.cli build-market-aware-session-history-closure-admission \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --markdown reports/session-history/history-closure-render/market_aware_session_history_closure.md \
  --render-report reports/session-history/history-closure-render/market_aware_session_history_closure_render_report.json \
  --render-audit-report reports/session-history/history-closure-render-audit/market_aware_session_history_closure_render_audit_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-admission
```
