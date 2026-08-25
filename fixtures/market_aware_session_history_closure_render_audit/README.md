# Market-aware session history closure render audit fixtures

Node47 audits the Node45 closure, Node46 Markdown, and Node46 render report
without re-rendering the Markdown.

```bash
python -m a_share_ai.cli audit-market-aware-session-history-closure-render \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --markdown reports/session-history/history-closure-render/market_aware_session_history_closure.md \
  --render-report reports/session-history/history-closure-render/market_aware_session_history_closure_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-render-audit
```
