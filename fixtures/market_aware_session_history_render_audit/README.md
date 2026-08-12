# Market-aware session history render audit fixtures

The Node40 audit consumes the Node37 history, Node38 history audit, and Node39
Markdown/render report artifacts. It verifies their literal paths, hashes,
sizes, statuses, and decision gate without recreating the render.

Run it against a controlled artifact root with:

```bash
python -m a_share_ai.cli audit-market-aware-session-history-render \
  --history reports/session-history/history/market_aware_session_history.json \
  --history-report reports/session-history/history/market_aware_session_history_report.json \
  --history-audit-report reports/session-history/history-audit/market_aware_session_history_audit_report.json \
  --markdown reports/session-history/history-render/market_aware_session_history.md \
  --render-report reports/session-history/history-render/market_aware_session_history_render_report.json \
  --artifact-root reports/session-history \
  --output-dir reports/session-history/history-render-audit
```

Tests create isolated inputs from the existing history and render helpers.
