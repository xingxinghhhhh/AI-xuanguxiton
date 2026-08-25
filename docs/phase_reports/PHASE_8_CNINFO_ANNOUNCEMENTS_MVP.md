# Phase 8: CNINFO official announcement snapshot MVP

## Outcome

Added a single-symbol, read-only CNINFO announcement adapter. It resolves the
public organization id, queries a bounded date range, converts the official
`announcementTime` epoch-millisecond field to Asia/Shanghai time, filters by the
explicit `as_of` date, and captures official PDF attachments with hashes.

The node only produces auditable facts. It does not summarize, score, classify, or
connect announcements to a trading decision.

## Files

- `src/a_share_ai/events/contracts.py`
- `src/a_share_ai/events/cninfo_announcements.py`
- `src/a_share_ai/events/__init__.py`
- `src/a_share_ai/cli.py` (`capture-announcements`)
- `tests/events/test_cninfo_announcements.py`
- `fixtures/events/*.json`

## Contract and fail-closed behavior

The adapter uses the official public CNINFO endpoints:

- `POST /new/information/topSearch/query` to resolve `code` and `orgId`;
- `POST /new/hisAnnouncement/query` to page announcement metadata;
- `https://static.cninfo.com.cn/...` to fetch the official attachment.

Output files:

- `request.json`
- `raw_response.json`
- `announcements_snapshot.json`
- `announcements_report.json`
- `content/{announcementId}.{type}` when an attachment is available

The snapshot retains title, announcement id, publication time/date, category fields,
source URL, raw item hash, content status, and content hash. Future announcements
are excluded before content retrieval. Duplicate ids, conflicting ids, malformed
dates, invalid source paths, and ascending page order are `invalid`. Missing content
is `content_unavailable`; metadata is retained but `announcement_ready=false`.
All statuses keep `decision_ready=false`.

## Verification

- `python -m pytest`: 72 passed
- `ruff check .`: passed
- `python -m compileall -q src tests`: passed
- `python -m pip install -e . --no-deps`: passed
- Real read-only smoke test: `600000.SH`, 2025-12-29 through 2025-12-31,
  `as_of=2025-12-31`, `received_at=2026-08-10T12:00:00Z`
- Real smoke result: exit code 0, `status=ready`, `record_count=2`,
  `announcement_ready=true`, `decision_ready=false`
- Selected ids: `1224905574`, `1224905571`
- Request SHA-256:
  `2fd176e7215a29f1aa3bbc86e20d957109ff4d23792389d0302795ab9641c82a`
- Raw response SHA-256:
  `e95ad40eb7c13e410bdba4c27fcbdba44874064ad2c61c21eec9a77202240d51`
- Snapshot SHA-256:
  `78a03f7bc055511b3662aa04383eecee2c86b998b968826df2196559f4ef3fd3`
- A repeated request with the same cutoff and `received_at` produced identical
  request, raw, and snapshot hashes.

## Scope and rollback

This node does not add news providers, AI analysis, a database, scheduling, real-time
data, credentials, automated trading, or anti-bot bypasses. Rollback is limited to
the new `events` module, CLI command, fixtures, tests, and this report; existing
market, technical, price-plan, profitability, and growth outputs are independent.
