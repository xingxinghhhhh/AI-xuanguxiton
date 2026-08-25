# Market-aware session history closure render fixtures

Node46 renders the Node45 closure and closure report as a deterministic
Markdown evidence view.

```bash
python -m a_share_ai.cli render-market-aware-session-history-closure \
  --closure reports/session-history/history-closure/market_aware_session_history_closure.json \
  --closure-report reports/session-history/history-closure/market_aware_session_history_closure_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-closure-render
```
