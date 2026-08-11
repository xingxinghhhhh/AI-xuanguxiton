# Phase 22: Benchmark market-context snapshot MVP

This node adds an independent, read-only benchmark-index snapshot for later
research context. It is deliberately separate from the stock daily-bar
contract and from the AI input bundle.

## Contract

The fixed version is `market-context-v1`. Each requested trading date must have
one valid record for each of these Baostock indexes:

- `000001.SH` / `sh.000001` — 上证综合指数
- `399001.SZ` / `sz.399001` — 深证成指
- `399006.SZ` / `sz.399006` — 创业板指

Records contain `symbol`, `instrument_type=index`, `trade_date`, Decimal
`open/high/low/close/volume/amount`, `source`, `market_time`, `received_at`,
and `data_status`. The source uses one login, three historical daily queries,
and one logout, with Baostock `adjustflag=3`.

## CLI and artifacts

```bash
python -m a_share_ai.cli capture-market-context \
  --start 2026-01-02 \
  --end 2026-01-06 \
  --as-of 2026-01-06T12:00:00+00:00 \
  --received-at 2026-01-07T00:00:00+00:00 \
  --calendar fixtures/market/calendar/sample.json \
  --output-dir reports/market-context-001
```

The output directory contains `request.json`, `raw_response.json`,
`market_context_snapshot.json`, and `market_context_report.json`. The report
records calendar, raw-response, and snapshot hashes. It is ready only when all
three indexes have complete calendar coverage, there are no future or
non-trading rows, and all records pass the fixed OHLCV and timestamp checks.
Both success and failure paths keep `decision_ready=false`.

## Scope exclusions

This node does not modify `analysis-input-v1`, the six evidence IDs, the stock
`DailyBar` contract, or the research release, freshness, diff, review, and
safety modules. It does not calculate a market trend, breadth, sectors, capital
flows, news, hotspots, or any investment/trading instruction.

## Verification

Offline tests use a fake Baostock client to cover fixed symbol mapping,
reordered fields, deterministic artifacts, missing dates, non-trading dates,
provider errors, and login/logout cleanup. A limited historical Baostock smoke
test is run separately when the optional dependency and network are available.
