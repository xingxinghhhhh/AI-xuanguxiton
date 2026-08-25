# Market-aware session history render fixtures

The Node39 renderer consumes the Node37 history JSON, its report, and the
Node38 independent audit report. It writes a deterministic Markdown timeline
and a render report while preserving stale, blocked, and
`decision_ready=false` states.

Generate a render from a history-root containing those inputs with:

```bash
python -m a_share_ai.cli render-market-aware-session-history \
  --history reports/session-history/history/market_aware_session_history.json \
  --history-report reports/session-history/history/market_aware_session_history_report.json \
  --history-audit-report reports/session-history/history-audit/market_aware_session_history_audit_report.json \
  --history-root reports/session-history \
  --output-dir reports/session-history/history-render
```

This fixture directory documents the contract; tests create isolated inputs
from the existing history and audit fixtures.
