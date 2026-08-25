# Phase 24: Benchmark market numeric summary MVP

This node derives a small, deterministic numeric summary from the existing
`market-context-v1` snapshot while leaving the raw snapshot and the v1 bundle
unchanged.

## Contract

The summary version is `market-context-summary-v1`. For each fixed index it
contains exactly these feature fields:

- `latest_trade_date`
- `latest_close`
- `return_1d`
- `return_5d`
- `return_20d`
- `record_count`

Returns use the fixed formula:

```text
return_n = close_latest / close_n_periods_ago - 1
```

All calculations use `Decimal` and ascending trade dates. If the snapshot has
fewer than `n + 1` rows, `return_n` is `null`; no value is substituted from
another period or index. A non-positive close, duplicate date, future date,
invalid OHLC record, or hash mismatch fails the v2 bundle closed.

## Integration boundary

The summary is added under `summaries.market.market_context` in
`analysis-input-v2`. It is derived only during bundle construction from the
already-validated snapshot. No network request, provider call, AI call, raw
market file, local path, or credential is added to the analysis request. v1
bundles and the original market-context snapshot remain unchanged.

This node does not calculate trends, rankings, moving averages, market
strength, sectors, breadth, flows, news, or trading conclusions. Every
downstream output continues to keep `decision_ready=false`.

## Verification

Tests cover two-day warmup behavior, deterministic repeated builds, zero/invalid
close failure, out-of-order dates, v1 compatibility, offline analysis-validator
consumption, and structured request forwarding without local paths or raw
artifacts. A limited real two-day Baostock snapshot from 2026-08-07 to
2026-08-10 was replayed offline: each index produced `return_1d`, while
`return_5d` and `return_20d` remained `null`.
